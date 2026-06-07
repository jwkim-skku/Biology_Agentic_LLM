# Gene Therapy Design Dashboard

Next.js dashboard for the MVP backend, including MANE-aware transcript display, candidate comparison, and evidence coverage.

## Run

Keep the FastAPI backend running on `http://127.0.0.1:8000`, then:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open `http://127.0.0.1:3001`.

Set `NEXT_PUBLIC_API_BASE_URL` if the backend moves:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8000/api/v1'
npm.cmd run dev
```

## Smoke Test

With the backend and frontend running, verify the main operational panels and metrics hydration:

```powershell
npm.cmd run smoke:ui
```

Set `FRONTEND_URL` if the dashboard is not running on `http://127.0.0.1:3000`.
