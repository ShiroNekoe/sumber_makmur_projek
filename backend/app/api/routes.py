from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, Any
from app.api.dashboard_routes import router as dashboard_router
from app.core.config import settings
from app.api.auth import verify_admin_api_key

router = APIRouter()

# Register dashboard F-07 routes
router.include_router(dashboard_router)

@router.get("/system/sizing-status")
async def get_sizing_status(request: Request) -> Dict[str, Any]:
    query_service = getattr(request.app.state, "dashboard_query_service", None)
    open_count = 0
    exposure = 0.0
    if query_service and hasattr(query_service, "position_repo"):
        try:
            positions = await query_service.position_repo.get_open_positions()
            open_count = len(positions)
            exposure = sum(getattr(p, "position_size_usd", 0.0) or 0.0 for p in positions)
        except Exception:
            pass

    mode = getattr(settings, "SIZING_MODE", "fixed")
    fixed_size = float(getattr(settings, "SIZING_FIXED_ORDER_SIZE_USD", 1.0))
    max_pos = int(getattr(settings, "RISK_MAX_CONCURRENT_POSITIONS", 3))
    max_exp = float(getattr(settings, "RISK_MAX_TOTAL_EXPOSURE_USD", 10.0))

    return {
        "mode": mode,
        "fixed_order_size_usd": fixed_size,
        "min_position_usd_estimate": 0.30,
        "max_concurrent_positions": max_pos,
        "current_open_positions": open_count,
        "total_exposure_usd": exposure,
        "max_total_exposure_usd": max_exp
    }

@router.get("/status")
def get_system_status() -> Dict[str, str]:
    return {
        "status": "active",
        "blockchain_monitor": "running",
        "execution_engine": "ready",
        "ml_inference": "ready"
    }

@router.post("/retrain", dependencies=[Depends(verify_admin_api_key)])
async def trigger_manual_retrain(request: Request) -> Dict[str, str]:
    scheduler = getattr(request.app.state, "retrain_scheduler", None)
    if not scheduler:
        raise HTTPException(status_code=503, detail="Retrain scheduler not available")
        
    success = await scheduler.retrain_model_if_needed(force=True)
    if success:
        return {"status": "success", "message": "Manual retrain pipeline executed successfully. New model activated."}
    else:
        return {"status": "skipped", "message": "Manual retrain executed, but model update was skipped or rolled back."}
