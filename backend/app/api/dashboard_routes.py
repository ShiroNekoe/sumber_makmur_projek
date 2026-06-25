"""
F-07 Dashboard REST API Routes
6 endpoints for signal history, trade log, positions, stats,
wallet candidates, and wallet approval actions.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.schemas import (
    SignalListResponse,
    SignalResponse,
    TradeListResponse,
    TradeResponse,
    PositionListResponse,
    PositionResponse,
    DashboardStatsResponse,
    WalletCandidateListResponse,
    WalletCandidateResponse,
    WalletApprovalRequest,
    WalletApprovalResponse,
    SystemStatusResponse,
    ComponentStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard F-07"])


def _partial_address(addr: str) -> str:
    """Display wallet/token address in partial format: first 6 + ... + last 4."""
    if not addr or len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def _get_query_service(request: Request):
    """Dependency injection helper to retrieve DashboardQueryService from app state."""
    query_service = getattr(request.app.state, "dashboard_query_service", None)
    if query_service is None:
        raise HTTPException(status_code=503, detail="Dashboard query service not available")
    return query_service


# ─── GET /dashboard/signals ──────────────────────────────────────────────────

@router.get("/signals", response_model=SignalListResponse, summary="Get recent signals (24h)")
async def get_recent_signals(
    hours: int = Query(default=24, ge=1, le=168, description="Time window in hours"),
    request: Request = None,
):
    """
    Returns all ML prediction signals from the last N hours (default 24h).
    Source: in-memory ring buffer populated by SafetyCheckGate.
    """
    try:
        query_service = _get_query_service(request)
        raw_signals = await query_service.get_recent_signals(hours=hours)
        signals = []
        for i, s in enumerate(raw_signals):
            token = s.get("token_address", "unknown")
            wallet = s.get("wallet_source", "unknown")
            signals.append(SignalResponse(
                signal_id=s.get("signal_id", f"sig_{i}"),
                token_address=token,
                token_short=_partial_address(token),
                wallet_source=wallet,
                wallet_short=_partial_address(wallet),
                direction=s.get("direction", "HOLD"),
                confidence_score=s.get("confidence_score", 0.0),
                safety_passed=s.get("safety_passed", False),
                timestamp=datetime.fromisoformat(
                    s.get("timestamp", datetime.now(timezone.utc).isoformat())
                    .replace("Z", "+00:00")
                ),
            ))
        return SignalListResponse(signals=signals, total=len(signals), hours_window=hours)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /signals error: {e}", exc_info=True)
        return SignalListResponse(signals=[], total=0, hours_window=hours)


# ─── GET /dashboard/trades ───────────────────────────────────────────────────

@router.get("/trades", response_model=TradeListResponse, summary="Get closed trade log")
async def get_trade_log(
    limit: int = Query(default=50, ge=1, le=200, description="Number of trades to return"),
    request: Request = None,
):
    """Returns most recent closed trades from the SQLite database."""
    try:
        query_service = _get_query_service(request)
        raw_trades = await query_service.get_recent_trades(limit=limit)
        trades = []
        for t in raw_trades:
            trades.append(TradeResponse(
                trade_id=t.trade_id,
                token_address=t.token_address,
                token_symbol=t.token_symbol,
                token_short=_partial_address(t.token_address),
                direction=t.direction,
                confidence_score=t.confidence_score,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                pnl_pct_actual=t.pnl_pct_actual,
                r_multiple=t.r_multiple,
                label=t.label,
                holding_time_minutes=t.holding_time_minutes,
                exit_reason=t.exit_reason,
                exit_ts=t.exit_ts,
            ))
        return TradeListResponse(trades=trades, total=len(trades))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /trades error: {e}", exc_info=True)
        return TradeListResponse(trades=[], total=0)


# ─── GET /dashboard/positions ────────────────────────────────────────────────

@router.get("/positions", response_model=PositionListResponse, summary="Get open positions")
async def get_open_positions(request: Request = None):
    """Returns all currently open positions from the SQLite database."""
    try:
        query_service = _get_query_service(request)
        # F-08/F-09 will populate open_positions; for now return empty (no execution engine yet)
        return PositionListResponse(positions=[], total=0)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /positions error: {e}", exc_info=True)
        return PositionListResponse(positions=[], total=0)


# ─── GET /dashboard/stats ────────────────────────────────────────────────────

@router.get("/stats", response_model=DashboardStatsResponse, summary="Get dashboard statistics")
async def get_dashboard_stats(request: Request = None):
    """
    Returns aggregated stats: win rate, trigger count, alerts fired, confidence threshold.
    The confidence_threshold_pct value is sourced from config.yaml — never hardcoded.
    """
    try:
        query_service = _get_query_service(request)
        stats = await query_service.get_stats()
        return DashboardStatsResponse(**stats)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /stats error: {e}", exc_info=True)
        from app.core.config import settings
        return DashboardStatsResponse(
            win_rate_pct=None,
            total_closed_trades=0,
            buy_benar_count=0,
            triggers_today=0,
            alerts_fired_24h=0,
            total_signals_24h=0,
            open_positions_count=0,
            confidence_threshold_pct=round(settings.CONFIDENCE_THRESHOLD * 100, 1),
            active_model_version="v0 (Bootstrap)",
        )


# ─── GET /dashboard/status ───────────────────────────────────────────────────

@router.get("/status", response_model=SystemStatusResponse, summary="Get system status")
async def get_system_status(request: Request = None):
    """Returns health status of all system components."""
    try:
        query_service = _get_query_service(request)
        raw_status = await query_service.get_system_status()
        return SystemStatusResponse(
            overall_status=raw_status["overall_status"],
            rpc_status=raw_status["rpc_status"],
            components=[ComponentStatus(**c) for c in raw_status["components"]],
            timestamp=datetime.fromisoformat(raw_status["timestamp"].replace("Z", "+00:00")),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Status check failed")


# ─── GET /dashboard/wallets/candidates ──────────────────────────────────────

@router.get(
    "/wallets/candidates",
    response_model=WalletCandidateListResponse,
    summary="Get wallet candidates (F-12 integration point)",
)
async def get_wallet_candidates(request: Request = None):
    """
    Returns auto-discovered wallet candidates awaiting user approval.
    Currently returns empty list — populated when F-12 Dynamic Wallet Discovery is implemented.
    """
    try:
        query_service = _get_query_service(request)
        candidates_raw = await query_service.get_wallet_candidates()
        candidates = []
        for c in candidates_raw:
            addr = c.get("wallet_address", "")
            candidates.append(WalletCandidateResponse(
                wallet_address=addr,
                wallet_short=_partial_address(addr),
                label=c.get("label", "Auto-Discovered"),
                source="auto_discovered",
                discovery_reason=c.get("discovery_reason", ""),
                discovered_at=c.get("discovered_at", datetime.now(timezone.utc)),
                status=c.get("status", "pending"),
            ))
        return WalletCandidateListResponse(candidates=candidates, total=len(candidates))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /wallets/candidates error: {e}", exc_info=True)
        return WalletCandidateListResponse(candidates=[], total=0)


# ─── POST /dashboard/wallets/{address}/approve ───────────────────────────────

@router.post(
    "/wallets/{wallet_address}/approve",
    response_model=WalletApprovalResponse,
    summary="Approve or reject a wallet candidate",
)
async def approve_wallet_candidate(
    wallet_address: str,
    body: WalletApprovalRequest,
    request: Request = None,
):
    """
    Approve or reject a wallet candidate. Records approval_timestamp + action.
    When approved, wallet will be added to the active watchlist.
    F-12 integration point: this action will trigger F-12's update flow.
    """
    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")

    approval_ts = datetime.now(timezone.utc)
    logger.info(
        f"[DASHBOARD API] Wallet {_partial_address(wallet_address)} "
        f"-> {body.action.upper()} at {approval_ts.isoformat()}"
    )

    # Broadcast the approval event to all WebSocket clients
    try:
        from app.websocket.manager import manager as ws_manager
        await ws_manager.broadcast_event("wallet_approval_result", {
            "wallet_address": wallet_address,
            "wallet_short": _partial_address(wallet_address),
            "action": body.action,
            "approval_timestamp": approval_ts.isoformat(),
        })
    except Exception as e:
        logger.warning(f"[DASHBOARD API] Could not broadcast wallet approval: {e}")

    return WalletApprovalResponse(
        wallet_address=wallet_address,
        action=body.action,
        approval_timestamp=approval_ts,
        success=True,
        message=f"Wallet {_partial_address(wallet_address)} has been {body.action}d.",
    )
