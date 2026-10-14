"""Screenshot file handling: where files go, downscale on capture, expire after a TTL.

Screenshots are Centrelink evidence, so the rules are asymmetric on purpose:
anything that could lose evidence errs toward *keeping the original bytes*
(a failed resize stores the untouched PNG), and expiry only ever removes the
image FILE — the application record and the fact a screenshot was taken stay
(see ``expire_screenshots``).
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EvidenceBundleItem, Match

logger = logging.getLogger(__name__)

# captureVisibleTab returns device-pixel-ratio-sized images (2560px+ on a HiDPI
# laptop). 1200px wide is still comfortably legible as evidence of what was on
# screen, and typically 75-90% smaller on disk.
MAX_WIDTH = 1200


def downscale_png(png_bytes: bytes, max_width: int = MAX_WIDTH) -> bytes:
    """Return ``png_bytes`` resized to at most ``max_width`` wide.

    Returns the ORIGINAL bytes unchanged if the image is already small enough,
    if re-encoding would not make it smaller, or if anything at all goes wrong
    — never lose the evidence over an optimisation.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png_bytes)) as img:
            if img.width <= max_width:
                return png_bytes
            height = max(1, round(img.height * max_width / img.width))
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA")
            resized = img.resize((max_width, height), Image.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="PNG", optimize=True)
        smaller = out.getvalue()
        return smaller if len(smaller) < len(png_bytes) else png_bytes
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("downscale_png: keeping original bytes", exc_info=True)
        return png_bytes


# Every screenshot lives once, under evidence/<YYYY-MM>/ (the month it was taken, local
# time), named <HHMM>_<DD-MM-YYYY>_<Company>.png. Older rows hold a bare
# "screenshots/<name>.png" at the folder's top level; both forms resolve below.
EVIDENCE_DIR = "evidence"


def safe_name_part(text: str | None, max_len: int = 40) -> str:
    """``text`` reduced to letters, digits and hyphens: safe in a file name on any OS,
    in a URL, and on an upload form. "Acme Pty. Ltd." -> "Acme-Pty-Ltd"."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return cleaned[:max_len].rstrip("-")


def evidence_relpath(taken_local: datetime, company: str | None, job_id: int) -> str:
    """Where a new screenshot goes, relative to the screenshots folder (posix form)."""
    name = safe_name_part(company) or f"job-{job_id}"
    stamp = taken_local.strftime("%H%M_%d-%m-%Y")
    return f"{EVIDENCE_DIR}/{taken_local:%Y-%m}/{stamp}_{name}.png"


def screenshot_relpath(screenshot_path: str | None) -> str | None:
    """``matches.screenshot_path`` ("screenshots/evidence/2026-10/x.png" or the older
    "screenshots/x.png") as a path inside the screenshots folder, or None if it is empty
    or tries to leave the folder."""
    if not screenshot_path:
        return None
    parts = list(PurePosixPath(screenshot_path.replace("\\", "/")).parts)
    if parts and parts[0] == "screenshots":
        parts = parts[1:]
    if not parts or any(p in ("", ".", "..", "/") or ":" in p for p in parts):
        return None
    return "/".join(parts)


def screenshot_file(screenshots_dir: Path, screenshot_path: str | None) -> Path | None:
    rel = screenshot_relpath(screenshot_path)
    return screenshots_dir.joinpath(*rel.split("/")) if rel else None


def unlink_screenshot(screenshots_dir: Path, screenshot_path: str | None) -> None:
    """Remove one screenshot file (and its month folder, once empty)."""
    path = screenshot_file(screenshots_dir, screenshot_path)
    if path is None:
        return
    path.unlink(missing_ok=True)
    month_dir = path.parent
    if month_dir.parent.name == EVIDENCE_DIR and month_dir.parent.parent == screenshots_dir:
        try:
            month_dir.rmdir()  # only succeeds when empty
        except OSError:
            pass


def expire_screenshots(
    db: Session, screenshots_dir: Path, cutoff: datetime, profile_id: int | None = None
) -> int:
    """Delete screenshot FILES taken before ``cutoff``; returns how many.

    Nulls ``screenshot_path`` but deliberately keeps ``screenshot_taken_at``:
    "path NULL, taken_at set" reads as "captured, since expired", so the
    Applied tab and CSV export can still show that evidence once existed. The
    match row and ``applied_at`` are never touched — the application record is
    permanent, only the image expires.

    A screenshot not yet in a Centrelink bundle (``evidence_bundle_items``) never
    expires: it is the only copy of evidence the user still has to report.
    """
    reported = select(EvidenceBundleItem.id).where(EvidenceBundleItem.match_id == Match.id)
    query = (
        select(Match)
        .where(Match.screenshot_path.isnot(None))
        .where(Match.screenshot_taken_at < cutoff)
        .where(reported.exists())
    )
    if profile_id is not None:
        query = query.where(Match.user_id == profile_id)

    expired = 0
    for match in db.scalars(query).all():
        unlink_screenshot(screenshots_dir, match.screenshot_path)
        match.screenshot_path = None
        expired += 1
    if expired:
        db.commit()
    return expired
