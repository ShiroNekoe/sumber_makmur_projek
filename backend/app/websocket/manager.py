"""
F-07 WebSocket Connection Manager & Event Publisher
Full-featured WebSocket manager with structured event broadcasting,
connection tracking, and signal history ring buffer.
"""
import logging
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections and broadcasts structured events.
    Maintains an in-memory ring buffer of recent signals for initial_state delivery.
    """

    def __init__(self, max_signal_history: int = 200):
        self.active_connections: List[WebSocket] = []
        self.signal_history: List[dict] = []
        self._max_signal_history = max_signal_history
        self._connection_count = 0  # Total connections ever (for logging)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and register it."""
        await websocket.accept()
        self.active_connections.append(websocket)
        self._connection_count += 1
        logger.info(
            f"[WS MANAGER] Client connected. "
            f"Active: {len(self.active_connections)}, Total ever: {self._connection_count}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active pool."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"[WS MANAGER] Client disconnected. Active: {len(self.active_connections)}")

    @property
    def client_count(self) -> int:
        return len(self.active_connections)

    # ─── Structured Event Broadcasting ───────────────────────────────────

    async def broadcast_event(self, event_type: str, data: dict) -> None:
        """
        Broadcast a structured event to ALL connected clients.
        Event envelope format: { "type": "<event_type>", "data": {...}, "timestamp": "..." }
        """
        envelope = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # If this is a signal event, save to history ring buffer
        if event_type == "signal_new":
            self._append_signal_history(data)

        stale_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(envelope)
            except Exception:
                stale_connections.append(connection)

        # Clean up stale connections
        for stale in stale_connections:
            self.disconnect(stale)

        if stale_connections:
            logger.warning(f"[WS MANAGER] Removed {len(stale_connections)} stale connection(s).")

    async def broadcast(self, message: dict) -> None:
        """
        Legacy broadcast method — wraps raw dict into a signal_new event.
        Kept for backward compatibility with SafetyCheckGate's existing calls.
        """
        event_type = message.get("event", "signal_new")

        # Map legacy event names to new structured types
        type_map = {
            "ALERT": "signal_new",
            "LOG_ONLY": "signal_new",
        }
        ws_event_type = type_map.get(event_type, event_type)

        await self.broadcast_event(ws_event_type, message)

    async def send_personal_message(self, message: str, websocket: WebSocket) -> None:
        """Send a text message to a specific client."""
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)

    async def send_personal_json(self, data: dict, websocket: WebSocket) -> None:
        """Send a JSON message to a specific client."""
        try:
            await websocket.send_json(data)
        except Exception:
            self.disconnect(websocket)

    # ─── Initial State Delivery ──────────────────────────────────────────

    async def send_initial_state(self, websocket: WebSocket, query_service=None) -> None:
        """
        Send the initial dashboard state to a newly connected client.
        Includes: recent signals, system status, stats, confidence threshold.
        """
        try:
            initial_data = {
                "signals": self.get_signal_history(),
                "system_status": {
                    "overall_status": "healthy",
                    "rpc_status": "simulation",
                },
                "confidence_threshold": None,
                "stats": {},
            }

            # If query_service is available, enrich with live data
            if query_service is not None:
                try:
                    stats = await query_service.get_stats()
                    initial_data["stats"] = stats
                    initial_data["confidence_threshold"] = stats.get("confidence_threshold_pct", 75.0)

                    status = await query_service.get_system_status()
                    initial_data["system_status"] = status
                except Exception as e:
                    logger.warning(f"[WS MANAGER] Error enriching initial_state: {e}")

            # If threshold not from query, fall back to config
            if initial_data["confidence_threshold"] is None:
                try:
                    from app.core.config import settings
                    initial_data["confidence_threshold"] = round(settings.CONFIDENCE_THRESHOLD * 100, 1)
                except Exception:
                    initial_data["confidence_threshold"] = 75.0

            await self.send_personal_json({
                "type": "initial_state",
                "data": initial_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, websocket)

            logger.info(
                f"[WS MANAGER] Sent initial_state to client. "
                f"Signals: {len(initial_data['signals'])}, "
                f"Threshold: {initial_data['confidence_threshold']}%"
            )
        except Exception as e:
            logger.error(f"[WS MANAGER] Error sending initial_state: {e}")

    # ─── Signal History Ring Buffer ──────────────────────────────────────

    def _append_signal_history(self, signal_data: dict) -> None:
        """Append a signal to the in-memory ring buffer."""
        self.signal_history.append(signal_data)
        if len(self.signal_history) > self._max_signal_history:
            self.signal_history = self.signal_history[-self._max_signal_history:]

    def get_signal_history(self) -> List[dict]:
        """Return a copy of the signal history buffer."""
        return list(self.signal_history)


# Singleton instance used across the application
manager = ConnectionManager()
