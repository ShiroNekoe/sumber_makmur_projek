# Sumber Makmur Projek

Full-stack trading telemetry prototype with a FastAPI backend and a Vite React frontend.

## Prerequisites

- Python 3.10+
- Node.js 20+ with npm
- PowerShell (Windows)

## Backend Setup

1. Buka terminal di folder root proyek:

```powershell
cd "C:\Users\lastico\OneDrive\Documents\sumber-makmur-hype-V.2"
```

2. Buat virtual environment dan aktifkan:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Install dependency backend:

```powershell
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

4. Copy file environment example ke file `.env` di folder backend:

```powershell
Copy-Item backend\.env.example backend\.env
```

5. Jalankan backend dengan Uvicorn:

```powershell
uvicorn app.main:app --reload --app-dir backend
```

6. Backend akan tersedia di:

- API root: `http://localhost:8000`
- Health endpoint: `http://localhost:8000/health`
- Versioned API: `http://localhost:8000/api/v1`
- WebSocket: `ws://localhost:8000/ws`

## Frontend Setup

1. Buka terminal baru di folder frontend:

```powershell
cd "C:\Users\lastico\OneDrive\Documents\sumber-makmur-hype-V.2\frontend"
```

2. Install dependency frontend:

```powershell
npm ci
```

3. Copy file environment example ke file `.env` di folder frontend:

```powershell
Copy-Item .env.example .env
```

4. Jalankan frontend development server:

```powershell
npm run dev
```

5. Akses aplikasi di browser:

- Frontend: `http://localhost:5173`

## Environment Variables

- Backend: `backend\.env`
- Frontend: `frontend\.env`

Default frontend env values:

```text
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_WS_BASE_URL=ws://localhost:8000/ws
```

## Menjalankan Proyek

Untuk menjalankan keseluruhan proyek, jalankan backend dan frontend di dua terminal terpisah:

1. Jalankan backend di terminal pertama.
2. Jalankan frontend di terminal kedua.
3. Buka `http://localhost:5173` di browser.

## Verifikasi

Jika ingin memastikan setup berfungsi:

```powershell
python -m compileall backend\app
cd frontend
npm run typecheck
npm run build
```

## Catatan

- Pastikan `frontend\node_modules` tidak dikomit ke repositori.
- Gunakan `Copy-Item` untuk membuat file `.env` dari file `.env.example`.
- Data SQLite dan file konfigurasi lokal tidak perlu didorong ke Git kecuali sengaja ingin disimpan.
