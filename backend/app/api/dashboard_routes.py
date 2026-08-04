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
from app.api.auth import verify_admin_api_key

from app.api.schemas import (
    SignalListResponse,
    SignalResponse,
    FeatureVectorResponse,
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
    SystemErrorListResponse,
    SystemErrorResponse,
    PortfolioSummaryResponse,
    WalletAddRequest,
    WalletAddResponse,
    WalletDeleteResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard F-07"])


def _partial_address(addr: str) -> str:
    """Display wallet/token address in partial format: first 6 + ... + last 4."""
    if not addr or len(addr) <= 12:
        return addr
    # Check for known mock tokens
    if addr == "DezXAZ8z7PnrnRJjz3wXBoRgixrfNg7yFLBnRx4S75Jb":
        return "BONK"
    elif addr == "EKpQGSJtjMFqKZ9KQGWjhoxjq2WqU1AF9Z23J1x584":
        return "WIF"
    elif addr == "So11111111111111111111111111111111111111112":
        return "SOL"
    elif addr == "CzLSujW7ZJuY7oL4b5C32hiyUeZSt84b5F08Suj752b":
        return "HYPE"
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
            
            features_raw = s.get("features")
            features_mapped = None
            if isinstance(features_raw, dict):
                features_mapped = FeatureVectorResponse(
                    position_size_usd=float(features_raw.get("position_size_usd") or 0.0),
                    token_age_minutes=float(features_raw.get("token_age_minutes") or 0.0),
                    liquidity_pool_depth=float(features_raw.get("liquidity_pool_depth") or 0.0),
                    slippage_actual=features_raw.get("slippage_actual"),
                    cluster_score=float(features_raw.get("cluster_score") or 0.0),
                    win_rate_30d=float(features_raw.get("win_rate_30d") or 0.0),
                    avg_holding_time_minutes=float(features_raw.get("avg_holding_time_minutes") or 0.0),
                    typical_trade_size_usd=float(features_raw.get("typical_trade_size_usd") or 0.0),
                    past_exit_pattern_score=float(features_raw.get("past_exit_pattern_score") or 0.0),
                    sol_usd_momentum=float(features_raw.get("sol_usd_momentum") or 0.0),
                    token_volume_liquidity_ratio=float(features_raw.get("token_volume_liquidity_ratio") or 0.0),
                    hour_of_day_utc=int(features_raw.get("hour_of_day_utc") or 0)
                )

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
                features=features_mapped,
                token_symbol=s.get("token_symbol", ""),
                token_name=s.get("token_name", ""),
                dex_url=s.get("dex_url", "")
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
        raw_positions = await query_service.get_open_positions()
        positions = []
        for p in raw_positions:
            positions.append(PositionResponse(
                position_id=p.position_id,
                token_address=p.token_address,
                token_short=_partial_address(p.token_address),
                wallet_source=p.wallet_source,
                wallet_short=_partial_address(p.wallet_source),
                state=p.state,
                position_size_usd=p.position_size_usd,
                confidence_score=p.confidence_score,
                model_version=p.model_version,
                entry_ts=p.entry_ts
            ))
        return PositionListResponse(positions=positions, total=len(positions))
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
    dependencies=[Depends(verify_admin_api_key)]
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

    query_service = _get_query_service(request)
    wallet_repo = query_service.wallet_repo

    # Fetch candidate from repository
    candidate = await wallet_repo.get_wallet(wallet_address)
    if not candidate:
        raise HTTPException(status_code=404, detail="Wallet candidate not found")

    # Update candidate fields
    candidate.status = "approved" if body.action == "approve" else "rejected"
    candidate.active = True if body.action == "approve" else False
    await wallet_repo.update_wallet(candidate)

    # Hot reload active watchlist on Monitor Orchestrator if available
    monitor_use_case = getattr(request.app.state, "monitor_use_case", None)
    if monitor_use_case is not None:
        try:
            await monitor_use_case.reload_watchlist()
        except Exception as err:
            logger.error(f"[DASHBOARD API] Failed to hot-reload monitor watchlist: {err}")

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


# ─── GET /dashboard/errors ───────────────────────────────────────────────────

@router.get("/errors", response_model=SystemErrorListResponse, summary="Get system error logs")
async def get_system_errors(
    limit: int = Query(default=50, ge=1, le=200, description="Number of logs to return"),
    request: Request = None,
):
    """Returns most recent system error/diagnostics logs from the SQLite database."""
    try:
        query_service = _get_query_service(request)
        raw_errors = await query_service.get_system_errors(limit=limit)
        errors = []
        for o in raw_errors:
            errors.append(SystemErrorResponse(
                log_id=o["log_id"],
                timestamp=datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00")),
                error_type=o["error_type"],
                severity=o["severity"],
                context=o["context"],
                recovery_action=o["recovery_action"],
                resolution_status=o["resolution_status"]
            ))
        return SystemErrorListResponse(errors=errors, total=len(errors))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /errors error: {e}", exc_info=True)
        return SystemErrorListResponse(errors=[], total=0)


# ─── GET /dashboard/wallets/active ───────────────────────────────────────────

@router.get("/wallets/active", summary="Get active watchlist wallets")
async def get_active_wallets(request: Request = None):
    """Returns the list of active watchlist wallets from the SQLite database."""
    try:
        query_service = _get_query_service(request)
        wallet_repo = query_service.wallet_repo
        active_list = await wallet_repo.get_active_wallets()
        return [
            {
                "wallet_address": w.wallet_address,
                "wallet_short": _partial_address(w.wallet_address),
                "label": w.label,
                "source": w.source,
                "added_at": w.added_at.isoformat() if w.added_at else None,
                "active": w.active
            }
            for w in active_list
        ]
    except Exception as e:
        logger.error(f"[DASHBOARD API] /wallets/active error: {e}", exc_info=True)
        return []


# ─── POST /dashboard/wallets ──────────────────────────────────────────────────

@router.post("/wallets", response_model=WalletAddResponse, summary="Manually add a wallet to the watchlist", dependencies=[Depends(verify_admin_api_key)])
async def add_manual_wallet(body: WalletAddRequest, request: Request):
    try:
        # Verify Solana address format
        from solders.pubkey import Pubkey
        try:
            Pubkey.from_string(body.wallet_address)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Solana wallet address format")
            
        query_service = _get_query_service(request)
        wallet_repo = query_service.wallet_repo
        
        existing = await wallet_repo.get_wallet(body.wallet_address)
        if existing:
            if existing.active:
                return WalletAddResponse(success=False, message="Wallet is already active in watchlist.")
            else:
                # Reactivate it
                existing.active = True
                existing.status = "approved"
                existing.label = body.label or existing.label or "Manual Whale Target"
                await wallet_repo.update_wallet(existing)
                message = f"Wallet re-activated in watchlist: {body.wallet_address}"
        else:
            # Create new WatchlistWallet
            from app.domain.models import WatchlistWallet
            from datetime import datetime, timezone
            new_wallet = WatchlistWallet(
                wallet_address=body.wallet_address,
                label=body.label or "Manual Whale Target",
                source="manual",
                added_at=datetime.now(timezone.utc),
                active=True,
                status="approved"
            )
            await wallet_repo.add_wallet(new_wallet)
            message = f"Wallet successfully added to watchlist: {body.wallet_address}"
            
        # Hot reload active watchlist on Monitor Orchestrator if available
        monitor_use_case = getattr(request.app.state, "monitor_use_case", None)
        if monitor_use_case is not None:
            try:
                await monitor_use_case.reload_watchlist()
            except Exception as err:
                logger.error(f"[DASHBOARD API] Failed to hot-reload monitor watchlist: {err}")
                
        return WalletAddResponse(success=True, message=message)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /wallets POST error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── DELETE /dashboard/wallets/{wallet_address} ───────────────────────────────

@router.delete("/wallets/{wallet_address}", response_model=WalletDeleteResponse, summary="Manually remove/deactivate a wallet from the watchlist", dependencies=[Depends(verify_admin_api_key)])
async def delete_manual_wallet(wallet_address: str, request: Request):
    try:
        query_service = _get_query_service(request)
        wallet_repo = query_service.wallet_repo
        
        existing = await wallet_repo.get_wallet(wallet_address)
        if not existing:
            raise HTTPException(status_code=404, detail="Wallet not found in watchlist.")
            
        # Soft delete by deactivating to prevent database foreign key constraint errors
        existing.active = False
        existing.status = "rejected"
        await wallet_repo.update_wallet(existing)
        
        # Hot reload active watchlist on Monitor Orchestrator if available
        monitor_use_case = getattr(request.app.state, "monitor_use_case", None)
        if monitor_use_case is not None:
            try:
                await monitor_use_case.reload_watchlist()
            except Exception as err:
                logger.error(f"[DASHBOARD API] Failed to hot-reload monitor watchlist: {err}")
                
        return WalletDeleteResponse(success=True, message=f"Wallet {wallet_address} successfully deactivated.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /wallets DELETE error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



# ─── GET /dashboard/portfolio ────────────────────────────────────────────────

@router.get("/portfolio", response_model=PortfolioSummaryResponse, summary="Get portfolio summary and holdings")
async def get_portfolio(request: Request):
    """Returns total realized/unrealized PnL, portfolio value, and detailed asset allocations."""
    try:
        pnl_calculator = getattr(request.app.state, "pnl_calculator", None)
        if not pnl_calculator:
            raise HTTPException(status_code=503, detail="PnL Calculator not available")
            
        from app.infrastructure.blockchain.wallet_manager import load_wallet_from_env
        keypair = load_wallet_from_env()
        pubkey_str = str(keypair.pubkey()) if keypair else "2fRGriSp8o32KdV1K8yxic1ZBLnqJXRiXpQK9ovCebf8"
        
        summary = await pnl_calculator.get_portfolio_summary(pubkey_str)
        
        # If keypair is None, add some mock holdings to summary so dashboard is lively in paper trading
        if not keypair:
            summary["holdings"] = [
                {
                    "mint": "TokenA11111111111111111111111111111111111",
                    "symbol": "WHALE_ALPHA",
                    "name": "Whale Alpha Sniper Token",
                    "amount": 1000.0,
                    "price_usd": 0.50,
                    "cost_basis": 0.45,
                    "value_usd": 500.0,
                    "unrealized_pnl_usd": 50.0,
                    "unrealized_pnl_pct": 0.1111
                },
                {
                    "mint": "TokenB22222222222222222222222222222222222",
                    "symbol": "WHALE_BETA",
                    "name": "Whale Beta Sniper Token",
                    "amount": 2500.0,
                    "price_usd": 0.20,
                    "cost_basis": 0.22,
                    "value_usd": 500.0,
                    "unrealized_pnl_usd": -50.0,
                    "unrealized_pnl_pct": -0.0909
                }
            ]
            summary["portfolio_value_usd"] = 1000.0
            summary["unrealized_pnl_usd"] = 0.0
            summary["total_pnl_usd"] = summary["realized_pnl_usd"]
            
        return summary
    except Exception as e:
        logger.error(f"[DASHBOARD API] /portfolio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── GET /dashboard/export/pdf ───────────────────────────────────────────────

@router.get("/export/pdf", summary="Export complete transaction and portfolio report as PDF")
async def export_portfolio_pdf(
    request: Request,
    start_date: Optional[str] = Query(default=None, description="Start date filter (ISO format)"),
    end_date: Optional[str] = Query(default=None, description="End date filter (ISO format)"),
):
    """
    Generates and returns a premium PDF transaction and portfolio report.
    Filters by start_date and end_date if provided.
    """
    try:
        from fastapi.responses import StreamingResponse
        from app.services.pdf_generator import generate_portfolio_pdf
        from app.infrastructure.database.session import SessionLocal
        
        parsed_start = None
        parsed_end = None
        
        if start_date:
            try:
                parsed_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid start_date format. Use ISO format.")
                
        if end_date:
            try:
                parsed_end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid end_date format. Use ISO format.")
                
        # Generate the PDF bytes
        with SessionLocal() as db_session:
            pdf_bytes = await generate_portfolio_pdf(
                db_session=db_session,
                start_date=parsed_start,
                end_date=parsed_end
            )
            
        filename = f"Sumber_Makmur_Report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
        
        return StreamingResponse(
            io.BytesIO(pdf_bytes) if "io" in globals() else __import__("io").BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] /export/pdf error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {str(e)}")


# ─── Market Insights F-02 / FR-105 API Endpoints ────────────────────────────

@router.get("/insights", summary="Get market insights list")
async def get_market_insights(
    request: Request,
    status: Optional[str] = Query(default=None, description="Filter by status: PENDING_REVIEW, REJECTED_STATISTICAL, APPROVED, REJECTED_MANUAL")
):
    try:
        repo = getattr(request.app.state, "market_insight_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="MarketInsight repository not available")
        insights = await repo.get_insights(status=status)
        return [i.model_dump() for i in insights]
    except Exception as e:
        logger.error(f"[DASHBOARD API] GET /insights error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/insights/{insight_id}/approve", summary="Approve market insight hypothesis", dependencies=[Depends(verify_admin_api_key)])
async def approve_market_insight(request: Request, insight_id: str):
    try:
        repo = getattr(request.app.state, "market_insight_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="MarketInsight repository not available")
        success = await repo.update_insight_status(insight_id, "APPROVED")
        if not success:
            raise HTTPException(status_code=404, detail="Insight not found")
        return {"status": "success", "message": f"Insight {insight_id} approved. Added as feature candidate for retrain pipeline."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] POST /insights/{insight_id}/approve error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/insights/{insight_id}/reject", summary="Reject market insight hypothesis", dependencies=[Depends(verify_admin_api_key)])
async def reject_market_insight(request: Request, insight_id: str, reason: Optional[str] = Query(default=None)):
    try:
        repo = getattr(request.app.state, "market_insight_repo", None)
        if not repo:
            raise HTTPException(status_code=503, detail="MarketInsight repository not available")
        success = await repo.update_insight_status(insight_id, "REJECTED_MANUAL", rejection_reason=reason or "Rejected manually by user.")
        if not success:
            raise HTTPException(status_code=404, detail="Insight not found")
        return {"status": "success", "message": f"Insight {insight_id} rejected."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DASHBOARD API] POST /insights/{insight_id}/reject error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/insights/trigger", summary="Manually trigger AI Market Insight generator job", dependencies=[Depends(verify_admin_api_key)])
async def trigger_insight_generator(request: Request):
    try:
        job = getattr(request.app.state, "insight_generator_job", None)
        if not job:
            raise HTTPException(status_code=503, detail="InsightGeneratorJob not available")
        results = await job.run_insight_pipeline()
        return {
            "status": "success",
            "message": f"Insight generator executed successfully. Generated {len(results)} insight(s).",
            "results": [r.model_dump() for r in results]
        }
    except Exception as e:
        logger.error(f"[DASHBOARD API] POST /insights/trigger error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

