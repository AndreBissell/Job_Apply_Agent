"""Screenshot file handling: downscale on capture, expire after a fixed TTL.

Screenshots are Centrelink evidence, so the rules are asymmetric on purpose:
anything that could lose evidence errs toward *keeping the original bytes*
(a failed resize stores the untouched PNG), and expiry only ever removes the
image FILE — the application record and the fact a screenshot was taken stay
(see ``expire_screenshots``).
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match

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


def unlink_screenshot(screenshots_dir: Path, screenshot_path: str | None) -> None:
    """Remove one screenshot file. Uses only the basename, like the upload path."""
    if screenshot_path:
        (screenshots_dir / Path(screenshot_path).name).unlink(missing_ok=True)


def expire_screenshots(
    db: Session, screenshots_dir: Path, cutoff: datetime, profile_id: int | None = None
) -> int:
    """Delete screenshot FILES taken before ``cutoff``; returns how many.

    Nulls ``screenshot_path`` but deliberately keeps ``screenshot_taken_at``:
    "path NULL, taken_at set" reads as "captured, since expired", so the
    Applied tab and CSV export can still show that evidence once existed. The
    match row and ``applied_at`` are never touched — the application record is
    permanent, only the image expires.

    Applies to applied matches too; that is the whole point of the TTL.
    """
    query = (
        select(Match)
        .where(Match.screenshot_path.isnot(None))
        .where(Match.screenshot_taken_at < cutoff)
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
