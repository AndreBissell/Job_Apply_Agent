"""Run the local FastAPI backend (uvicorn on 127.0.0.1:8000).

    python scripts/run_api.py

Binds to localhost only — this backend is for the local Chrome extension, not
exposed to the network. ``reload=True`` picks up code edits during development.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.api.main:app", host="127.0.0.1", port=8000, reload=True)
