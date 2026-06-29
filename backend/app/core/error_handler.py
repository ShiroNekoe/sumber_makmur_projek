import logging
import uuid
import asyncio
from datetime import datetime, timezone
from enum import Enum
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger(__name__)


class ErrorSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ErrorType(str, Enum):
    RPC_DISCONNECTED = "rpc_disconnected"
    SAFETY_API_TIMEOUT = "safety_api_timeout"
    ENTRY_FAILED = "entry_failed"
    EXIT_PENDING = "exit_pending"
    EXIT_FAILED = "exit_failed"
    CRITICAL_EXIT_FAILED = "critical_exit_failed"
    SYSTEM_CRASH = "system_crash"
    RETRAIN_MISSED = "retrain_missed"


# Injected session factory for database writes
_session_factory = None


def register_session_factory(factory):
    global _session_factory
    _session_factory = factory


async def log_system_error(
    error_type: ErrorType,
    severity: ErrorSeverity,
    context: str,
    recovery_action: str,
    resolution_status: str = "pending"
) -> str:
    """
    Logs structured error entries to the SQLite database
    and broadcasts ws notifications to F-07 dashboard.
    """
    log_id = f"err_{uuid.uuid4().hex[:8]}"
    ts = datetime.now(timezone.utc)
    
    logger.error(f"[SYSTEM ERROR] {error_type.value.upper()} [{severity.value}] Context: {context}. Action: {recovery_action}.")

    # 1. Write to database using the injected session factory
    if _session_factory:
        try:
            db = _session_factory()
            from app.infrastructure.database.models import SystemErrorLogORM
            orm = SystemErrorLogORM(
                log_id=log_id,
                timestamp=ts,
                error_type=error_type.value,
                severity=severity.value,
                context=context,
                recovery_action=recovery_action,
                resolution_status=resolution_status
            )
            db.add(orm)
            db.commit()
            db.close()
        except Exception as e:
            logger.critical(f"Failed to record system error to database: {e}", exc_info=True)

    # 2. Broadcast WebSocket event to dashboard
    level = "error" if severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL] else ("warning" if severity == ErrorSeverity.WARNING else "info")
    try:
        await ws_manager.broadcast({
            "type": "system_alert",
            "data": {
                "event": "system_alert",
                "alert_type": error_type.value,
                "message": f"[{severity.value}] {context}",
                "level": level,
                "timestamp": ts.isoformat()
            }
        })
    except Exception as e:
        logger.error(f"Failed to broadcast WebSocket system alert: {e}")

    return log_id
