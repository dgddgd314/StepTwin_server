# Steptwin API

FastAPI + PostGIS/pgRouting-ready backend starter for the hackathon.

Shared Android/backend API contract:

```text
docs/api-contract.md
```

Android handoff contract for the current PoC:

```text
docs/android-api-handoff.md
```

Keep external API keys in local `.env` only. For TMAP, fill `TMAP_APP_KEY` in `.env` and keep
`TMAP_USE_LIVE=false` until the exact TMAP endpoint/response parser is confirmed.
TMAP transit API integration notes are tracked in `docs/tmap-transit-api.md`.
TMAP transit API integration notes are tracked in `docs/tmap-transit-api.md`.
TMAP transit API integration notes are tracked in `docs/tmap-transit-api.md`.
TMAP transit API integration notes are tracked in `docs/tmap-transit-api.md`.
Pedestrian graph data requirements are tracked in `docs/pedestrian-graph-data.md`.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d postgres
python -m uvicorn steptwin_api.main:app --reload --app-dir src
```

Import the MVP Seoul OpenAPI walking network for Dongdaemun-gu into PostGIS:

```powershell
.\.venv\Scripts\python.exe scripts\import_seoul_walk_network.py --sgg-name "동대문구"
```

On Windows, you can also run:

```powershell
.\scripts\run-api.ps1
```

For LAN access from another device on the same Wi-Fi:

```powershell
.\scripts\allow-api-firewall.ps1
.\scripts\run-api.ps1 -Lan
```

Then open `http://<your-laptop-ip>:8000/api/v1/health` from the other device.

After the hackathon, remove the firewall rule:

```powershell
.\scripts\remove-api-firewall.ps1
```

To keep it running in the background on Windows:

```powershell
Start-Process -FilePath ".\.venv\Scripts\pythonw.exe" -ArgumentList "scripts\dev_server.py" -WorkingDirectory .
```

Health check:

```powershell
curl http://127.0.0.1:8000/api/v1/health
```

Hybrid route preview:

```powershell
$body = @{
  origin = @{
    name = "Seoul City Hall"
    coordinate = @{ latitude = 37.5665; longitude = 126.9780 }
  }
  destination = @{
    name = "Namsan Seoul Tower"
    coordinate = @{ latitude = 37.5512; longitude = 126.9882 }
  }
  preferences = @{
    avoid_stairs = $true
    shade_weight = 0.9
    max_extra_walk_ratio = 0.2
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/routes/preview `
  -ContentType "application/json" `
  -Body $body
```

Interactive API docs:

```text
http://127.0.0.1:8000/docs
```

If `DATABASE_URL` is empty, the health API still works and reports the database check as disabled.
The pgRouting walk route APIs require PostgreSQL/PostGIS/pgRouting.

## Project Shape

```text
src/steptwin_api/
  api/        HTTP routers
  core/       app config, logging, lifespan
  db/         async SQLAlchemy engine/session utilities
  schemas/    Pydantic response models
```
