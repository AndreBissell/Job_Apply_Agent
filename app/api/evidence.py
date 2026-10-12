"""Centrelink evidence: GET /jobs/evidence-export and POST /jobs/{job_id}/screenshot.

Both read the screenshots folder as ``paths._screenshots_dir`` at call time (plan rule M1).
``/jobs/evidence-export`` must be matched before ``GET /jobs/{job_id}``; this router is
included before main's remaining job routes, and tests/test_route_table.py checks it.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import paths
from app.api.deps import get_db
from app.models import JobListing, Match
from app.screenshots import downscale_png, unlink_screenshot

router = APIRouter()


class ScreenshotUpload(BaseModel):
    data_url: str  # "data:image/png;base64,...." — see POST /jobs/{id}/screenshot


def _csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection. Titles/employers come from
    scraped pages, and a cell starting with = + - @ is executed by Excel."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/jobs/evidence-export")
def evidence_export(profile_id: int = 1, db: Session = Depends(get_db)) -> Response:
    """Zip of every applied job's record plus whichever screenshots still exist.

    The safety net for screenshot expiry (``screenshot_ttl_days``): the image
    files are deleted after their TTL, so this is how the user takes a copy
    first. The CSV always lists every application; ``Screenshot`` says
    "yes", "expired" (taken, file since deleted) or "no" (never captured), and
    the screenshots themselves sit alongside it under ``screenshots/``.

    Registered before ``/jobs/{job_id}`` so the literal path wins the match.
    """
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        # applied_at, not status: the same definition as the dashboard (GET /obligation)
        # and retention, so the export can never disagree with the period counts.
        .where(Match.applied_at.is_not(None))
        .order_by(Match.applied_at.desc())
    ).all()

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(
        ["Date Applied", "Job Title", "Employer", "Location", "Source URL",
         "Screenshot", "Screenshot Taken", "Screenshot File"]
    )
    files: list[tuple[str, Path]] = []
    for match, job in rows:
        path = (
            paths._screenshots_dir / Path(match.screenshot_path).name
            if match.screenshot_path else None
        )
        on_disk = path is not None and path.is_file()
        if on_disk:
            files.append((f"screenshots/{path.name}", path))
        if on_disk:
            state = "yes"
        elif match.screenshot_taken_at:
            state = "expired"
        else:
            state = "no"
        writer.writerow([
            match.applied_at.date().isoformat() if match.applied_at else "",
            _csv_safe(job.title), _csv_safe(job.company), _csv_safe(job.location), job.url,
            state,
            match.screenshot_taken_at.date().isoformat() if match.screenshot_taken_at else "",
            f"screenshots/{path.name}" if on_disk else "",
        ])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("applied-jobs.csv", csv_buf.getvalue())
        for arcname, path in files:
            zf.write(path, arcname)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="application-evidence-{stamp}.zip"'},
    )


@router.post("/jobs/{job_id}/screenshot")
def upload_screenshot(
    job_id: int,
    body: ScreenshotUpload,
    profile_id: int = 1,
    db: Session = Depends(get_db),
) -> dict:
    """Persist a Quick-Apply screenshot as durable Centrelink evidence.

    Accepts the raw data: URL the extension already holds in memory from
    chrome.tabs.captureVisibleTab — no multipart re-encode needed. Overwrites
    (deletes) any previous screenshot file for this match so repeat captures
    don't grow disk usage unbounded; one screenshot per match, latest wins.
    Files expire after ``screenshot_ttl_days`` (default 30) — see
    app/retention.py; the application record itself never does.
    """
    match = db.scalar(
        select(Match).where(Match.user_id == profile_id, Match.job_id == job_id)
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    m = re.match(r"^data:image/png;base64,(.+)$", body.data_url, re.DOTALL)
    if not m:
        raise HTTPException(status_code=400, detail="Expected a PNG data URL")
    try:
        png_bytes = base64.b64decode(m.group(1), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid base64 image data")
    if not png_bytes:
        raise HTTPException(status_code=400, detail="Empty image data")

    # Store at <=1200px wide; falls back to the untouched bytes on any failure.
    png_bytes = downscale_png(png_bytes)

    unlink_screenshot(paths._screenshots_dir, match.screenshot_path)

    ts = datetime.now(timezone.utc)
    filename = f"{job_id}-{profile_id}-{ts.strftime('%Y%m%dT%H%M%SZ')}.png"
    (paths._screenshots_dir / filename).write_bytes(png_bytes)

    match.screenshot_path = f"screenshots/{filename}"
    match.screenshot_taken_at = ts
    db.commit()

    return {
        "job_id": job_id,
        "screenshot_path": match.screenshot_path,
        "screenshot_taken_at": ts.isoformat(),
    }
