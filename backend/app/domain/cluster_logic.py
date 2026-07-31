"""
Domain Layer: Cluster Score Logic
Pure function to evaluate whether multiple unique wallets buy the same token mint
within a configurable time window (TRIGGER_WINDOW_MINUTES).
Used identically in training (bootstrap.py) and serving (inference.py / new_token_discovery_service.py).

DESIGN NOTE — cluster event log vs signal log:
  _cluster_event_log  → NOT de-duped. Records EVERY (wallet, token, ts) event.
                        Used exclusively as the data source for compute_cluster_score.
  _signal_log (dashboard_query.py) → De-duped per-token per 30 min. Used only for UI/WebSocket.
  These two stores MUST NOT be mixed. compute_cluster_score in serving-path reads
  from _cluster_event_log via get_all_cluster_events().
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence, Union, Optional, List
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cluster event ring-buffer — separate from _signal_log in dashboard_query.py
# No dedup: every unique (wallet, token, ts) is recorded so that
# multi-wallet detection is never silently dropped.
# ---------------------------------------------------------------------------
_cluster_event_log: List[dict] = []
_MAX_CLUSTER_EVENT_LOG = 1000  # ring-buffer cap


def append_cluster_event(wallet_address: str, token_mint: str, timestamp: Optional[datetime] = None) -> None:
    """
    Record a raw cluster event (no dedup).
    Called by inference.py / new_token_discovery_service.py every time a trigger
    event is processed — BEFORE compute_cluster_score reads the log.
    """
    global _cluster_event_log
    ts = timestamp or datetime.now(timezone.utc)
    _cluster_event_log.append({
        "wallet_address": wallet_address,
        "token_mint": token_mint,
        "timestamp": ts.isoformat(),   # stored as ISO string — same format as _signal_log
    })
    if len(_cluster_event_log) > _MAX_CLUSTER_EVENT_LOG:
        _cluster_event_log = _cluster_event_log[-_MAX_CLUSTER_EVENT_LOG:]


def get_all_cluster_events() -> List[dict]:
    """Return a copy of the raw (non-deduped) cluster event log."""
    return list(_cluster_event_log)


# ---------------------------------------------------------------------------
# Timestamp normalisation helper (Bug #1 fix)
# ---------------------------------------------------------------------------
def _normalize_ts(ts: Union[datetime, str, None]) -> Optional[datetime]:
    """
    Convert a timestamp value to timezone-aware datetime.
    Handles:
      - datetime objects (with or without tzinfo)
      - ISO-format strings (including those ending in 'Z')
      - None → returns None

    This is necessary because append_signal_event() and append_cluster_event()
    both store timestamps as isoformat() strings, but training-path callers
    (bootstrap.py) pass datetime objects directly.
    """
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            # Replace trailing Z with +00:00 for Python < 3.11 compatibility
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    return None


@dataclass(frozen=True)
class GenericTradeEvent:
    wallet_address: str
    token_mint: str
    timestamp: datetime


def compute_cluster_score(
    target_wallet: str,
    target_token: str,
    target_timestamp: Union[datetime, str],
    events: Sequence[Union[GenericTradeEvent, object]],
    window_minutes: float = 30.0,
    confidence_boost: bool = False,
) -> float:
    """
    Pure function to calculate cluster_score.
    Returns 1.0 if confidence_boost is True or >=1 OTHER unique wallet bought
    target_token within window_minutes of target_timestamp, else 0.0.

    Args:
        target_wallet: Wallet address of the target trigger event.
        target_token: Token mint address of the target token.
        target_timestamp: Trigger timestamp — accepts datetime OR ISO string.
        events: Sequence of trade events (dict or dataclass/object).
        window_minutes: Window size in minutes (read from settings.TRIGGER_WINDOW_MINUTES).
        confidence_boost: If True, bypass cluster checks and return 1.0.

    Returns:
        float: 1.0 if at least 1 other unique wallet bought the same token within window_minutes, 0.0 otherwise.
    """
    if confidence_boost:
        return 1.0

    if not events:
        return 0.0

    # Normalize target_timestamp — handles both datetime objects and ISO strings
    norm_target_ts = _normalize_ts(target_timestamp)
    if norm_target_ts is None:
        logger.warning(
            "[CLUSTER SCORE] target_timestamp could not be parsed (%r). Returning 0.0.", target_timestamp
        )
        return 0.0

    window_seconds = window_minutes * 60.0
    other_wallets = set()

    for ev in events:
        try:
            # --- Extract fields: support dict, dataclass, and arbitrary objects ---
            if isinstance(ev, dict):
                w_addr = ev.get("wallet_address") or ev.get("wallet_source")
                t_mint = ev.get("token_mint") or ev.get("token_address")
                ev_ts_raw = ev.get("timestamp") or ev.get("entry_ts")
            else:
                w_addr = getattr(ev, "wallet_address", None) or getattr(ev, "wallet_source", None)
                t_mint = getattr(ev, "token_mint", None) or getattr(ev, "token_address", None)
                ev_ts_raw = (
                    getattr(ev, "entry_ts", None)
                    or getattr(ev, "timestamp", None)
                    or getattr(ev, "signal_ts", None)
                )

            if not w_addr or not t_mint or ev_ts_raw is None:
                continue

            if w_addr == target_wallet:
                continue

            if t_mint != target_token:
                continue

            # Normalize event timestamp — this is the fix for Bug #1:
            # data from _signal_log / _cluster_event_log arrives as ISO strings.
            ev_ts = _normalize_ts(ev_ts_raw)
            if ev_ts is None:
                logger.warning(
                    "[CLUSTER SCORE] Could not parse event timestamp (%r) for wallet %s — skipping event.",
                    ev_ts_raw, w_addr
                )
                continue

            if abs((ev_ts - norm_target_ts).total_seconds()) <= window_seconds:
                other_wallets.add(w_addr)

        except Exception as exc:
            # A single malformed event MUST NOT crash the entire feature extraction.
            logger.warning(
                "[CLUSTER SCORE] Unexpected error processing event (%r): %s — skipping.", ev, exc
            )
            continue

    return 1.0 if len(other_wallets) > 0 else 0.0
