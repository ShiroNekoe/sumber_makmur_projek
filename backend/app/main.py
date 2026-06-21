from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.websocket.manager import manager

app = FastAPI(
    title="Sumber Makmur System Backend",
    description="AI Smart Money Trading System Backend (5-Layer Architecture)",
    version="1.0.0"
)

# Enable CORS for frontend origin (Vite default dev port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "online", "system": "Sumber Makmur Trading Engine"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Maintain connection, handle client events/pings if sent
            data = await websocket.receive_text()
            # In skeleton mode, simply bounce back a ping acknowledgement
            await websocket.send_json({"type": "ping_ack", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
