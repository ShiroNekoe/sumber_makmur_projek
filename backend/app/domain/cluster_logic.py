"""
Domain Layer: Cluster Score Logic
Pure function to evaluate whether multiple unique wallets buy the same token mint
within a configurable time window (TRIGGER_WINDOW_MINUTES).
Used identically in training (bootstrap.py) and serving (inference.py / new_token_discovery_service.py).
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence, Union, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenericTradeEvent:
    wallet_address: str
    token_mint: str
    timestamp: datetime


def compute_cluster_score(
    target_wallet: str,
    target_token: str,
    target_timestamp: datetime,
    events: Sequence[Union[GenericTradeEvent, object]],
    window_minutes: float = 30.0,
    confidence_boost: bool = False,
) -> float:
    """
    Pure function to calculate cluster_score.
    Returns 1.0 if confidence_boost is True or >=2 unique wallets buy target_token within window_minutes, 0.0 otherwise.

    Args:
        target_wallet: Wallet address of the target trigger event.
        target_token: Token mint address of the target token.
        target_timestamp: Trigger timestamp.
        events: Sequence of trade events.
        window_minutes: Window size in minutes (read from settings.TRIGGER_WINDOW_MINUTES).
        confidence_boost: If True, bypass cluster checks and return 1.0.

    Returns:
        float: 1.0 if at least 1 other unique wallet bought the same token within window_minutes, 0.0 otherwise.
    """
    if confidence_boost:
        return 1.0

    if not events:
        return 0.0

    window_seconds = window_minutes * 60.0
    other_wallets = set()

    for ev in events:
        w_addr = getattr(ev, "wallet_address", None) or getattr(ev, "wallet_source", None)
        t_mint = getattr(ev, "token_mint", None) or getattr(ev, "token_address", None)
        ev_ts = getattr(ev, "entry_ts", None) or getattr(ev, "timestamp", None) or getattr(ev, "signal_ts", None)

        if isinstance(ev, dict):
            w_addr = ev.get("wallet_address") or ev.get("wallet_source")
            t_mint = ev.get("token_mint") or ev.get("token_address")
            ev_ts = ev.get("timestamp") or ev.get("entry_ts")

        if not w_addr or not t_mint or not ev_ts:
            continue

        if w_addr == target_wallet:
            continue

        if t_mint == target_token:
            if abs((ev_ts - target_timestamp).total_seconds()) <= window_seconds:
                other_wallets.add(w_addr)

    return 1.0 if len(other_wallets) > 0 else 0.0
