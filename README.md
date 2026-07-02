# Steptwin API

FastAPI + MySQL-ready backend starter for the hackathon.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
docker compose up -d mysql
python -m uvicorn steptwin_api.main:app --reload --app-dir src
```

On Windows, you can also run:

```powershell
.\scripts\run-api.ps1
```

To keep it running in the background on Windows:

```powershell
Start-Process -FilePath ".\.venv\Scripts\pythonw.exe" -ArgumentList "scripts\dev_server.py" -WorkingDirectory .
```

Health check:

```powershell
curl http://127.0.0.1:8000/api/v1/health
```

Interactive API docs:

```text
http://127.0.0.1:8000/docs
```

If `DATABASE_URL` is empty, the health API still works and reports the database check as disabled.

## Project Shape

```text
src/steptwin_api/
  api/        HTTP routers
  core/       app config, logging, lifespan
  db/         async SQLAlchemy engine/session utilities
  schemas/    Pydantic response models
```
