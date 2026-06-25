# Sumber Makmur Projek

Full-stack trading telemetry prototype with a FastAPI backend and a Vite React frontend.

## Prerequisites

- Python 3.10+
- Node.js 20+ with npm

## Backend Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
uvicorn app.main:app --reload --app-dir backend
```

Backend defaults:

- API: `http://localhost:8000`
- Health: `http://localhost:8000/health`
- Versioned API: `http://localhost:8000/api/v1`
- WebSocket: `ws://localhost:8000/ws`

## Frontend Setup

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Frontend default:

- App: `http://localhost:5173`

## Verification

```powershell
python -m compileall backend\app
cd frontend
npm run typecheck
npm run build
```

## Repository Hygiene

Generated folders such as `node_modules`, `.venv`, `__pycache__`, `dist`, and local `.env` files are intentionally ignored. Install dependencies locally from the lockfile and requirements file instead of committing generated dependency folders.
