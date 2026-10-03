"""Run the local FastAPI backend (uvicorn on 127.0.0.1).

    python scripts/run_api.py real     # your real profile  -> real.db,  port 8000
    python scripts/run_api.py test     # the fake profile   -> app.db,   port 8001

The two environments are fully isolated: separate database file, separate
screenshots folder, separate port. The extension's REAL/TEST switch (sidebar
header) picks which port it talks to. The environment is a required argument on
purpose — there is no default to fall into by accident.

Binds to localhost only — this backend is for the local Chrome extension, not
exposed to the network. ``reload=True`` picks up code edits during development.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENVIRONMENTS = {
    "real": {"database_url": "sqlite:///real.db", "port": 8000},
    "test": {"database_url": "sqlite:///app.db", "port": 8001},
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local backend for one environment.")
    parser.add_argument("env", choices=sorted(ENVIRONMENTS))
    args = parser.parse_args()
    cfg = ENVIRONMENTS[args.env]

    # Set BEFORE importing anything from app/: db.py reads DATABASE_URL at import,
    # and load_dotenv() never overrides a variable that is already set — so this
    # wins over whatever the .env file says. uvicorn's reload subprocess inherits it.
    os.environ["DATABASE_URL"] = cfg["database_url"]
    os.environ["APP_ENV"] = args.env
    os.chdir(ROOT)  # sqlite:///real.db is relative to the working directory

    # Bring this environment's DB up to the current schema (a brand-new real.db
    # starts empty). Idempotent — a no-op when already at head.
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    import uvicorn

    print(f"Starting {args.env.upper()} environment: {cfg['database_url']} on port {cfg['port']}")
    uvicorn.run("app.api.main:app", host="127.0.0.1", port=cfg["port"], reload=True)


if __name__ == "__main__":
    main()
