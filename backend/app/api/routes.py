from fastapi import APIRouter, Request, HTTPException
from typing import Dict
from app.api.dashboard_routes import router as dashboard_router

router = APIRouter()

# Register dashboard F-07 routes
router.include_router(dashboard_router)

@router.get("/status")
def get_system_status() -> Dict[str, str]:
    return {
        "status": "active",
        "blockchain_monitor": "running",
        "execution_engine": "ready",
        "ml_inference": "ready"
    }

@router.post("/retrain")
async def trigger_manual_retrain(request: Request) -> Dict[str, str]:
    scheduler = getattr(request.app.state, "retrain_scheduler", None)
    if not scheduler:
        raise HTTPException(status_code=503, detail="Retrain scheduler not available")
        
    success = await scheduler.retrain_model_if_needed(force=True)
    if success:
        return {"status": "success", "message": "Manual retrain pipeline executed successfully. New model activated."}
    else:
        return {"status": "skipped", "message": "Manual retrain executed, but model update was skipped or rolled back."}
