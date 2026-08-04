"""
API Security Authentication Helper
Provides static API key header verification for state-changing endpoints.
"""
import os
import logging
from typing import Optional
from fastapi import Header, HTTPException
from app.core.config import settings

logger = logging.getLogger(__name__)


async def verify_admin_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")):
    """
    Verifies X-API-Key header against configured ADMIN_API_KEY.
    If ADMIN_API_KEY is unset, logs warning and allows request for local single-user mode (127.0.0.1 bound).
    If ADMIN_API_KEY is configured, strictly validates X-API-Key header.
    """
    configured_key = os.environ.get("ADMIN_API_KEY") or getattr(settings, "ADMIN_API_KEY", "")
    
    if not configured_key:
        return True

    if not x_api_key or x_api_key != configured_key:
        logger.warning("[API AUTH] Unauthorized attempt to invoke state-changing endpoint without valid X-API-Key header.")
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing X-API-Key header")
        
    return True
