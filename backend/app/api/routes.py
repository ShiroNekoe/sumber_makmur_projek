from fastapi import APIRouter
from typing import Dict

router = APIRouter()

@router.get("/status")
def get_system_status() -> Dict[str, str]:
    return {
        "status": "active",
        "blockchain_monitor": "running",
        "execution_engine": "ready",
        "ml_inference": "ready"
    }

@router.post("/retrain")
def trigger_manual_retrain() -> Dict[str, str]:
    # Placeholder for triggering adaptive learning manually
    return {"message": "Manual retrain pipeline triggered"}
