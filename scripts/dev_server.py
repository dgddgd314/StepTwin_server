from __future__ import annotations

import sys
from pathlib import Path

import uvicorn


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    sys.path.insert(0, str(project_root / "src"))

    with (log_dir / "api.log").open("a", encoding="utf-8", buffering=1) as log_file:
        sys.stdout = log_file
        sys.stderr = log_file

        uvicorn.run(
            "steptwin_api.main:app",
            app_dir=str(project_root / "src"),
            host="127.0.0.1",
            port=8000,
            log_level="info",
        )


if __name__ == "__main__":
    main()
