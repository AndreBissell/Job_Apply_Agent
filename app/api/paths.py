"""Folders the API serves or writes to.

``_screenshots_dir`` is read as ``paths._screenshots_dir`` at call time (never copied with
``from app.api.paths import ...``), so a test or script that replaces it on this module is
seen by every reader.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.db import app_env

_static_dir = Path(__file__).parent.parent / "static"

# Per-environment, so the real instance's evidence files never share a folder
# (or a retention sweep) with the test instance's. APP_SCREENSHOTS_DIR moves the TEST
# folder (an e2e run on a scratch DB keeps its files in its own temp folder); the real
# environment ignores it.
_screenshots_dir = Path(__file__).parent.parent / (
    "screenshots_real" if app_env() == "real" else "screenshots"
)
if app_env() != "real" and os.environ.get("APP_SCREENSHOTS_DIR"):
    _screenshots_dir = Path(os.environ["APP_SCREENSHOTS_DIR"])
_screenshots_dir.mkdir(parents=True, exist_ok=True)
