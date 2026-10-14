"""Centrelink evidence: the export, screenshot upload, and the upload-ready PDF bundles.

GET    /jobs/evidence-export              zip of every application + surviving screenshots
POST   /jobs/{job_id}/screenshot          store a Quick Apply screenshot (evidence/<month>/)
GET    /evidence/status                   unreported count + recent bundles (Overview card)
POST   /evidence/bundles                  collate unreported screenshots into PDF(s)
GET    /evidence/bundles/{id}/download    one bundle's PDF
DELETE /evidence/bundles/{id}             undo a collate

All read the screenshots folder as ``paths._screenshots_dir`` at call time (plan rule M1).
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
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import retention
from app.api import events, paths
from app.api.deps import get_db
from app.evidence_pdf import EvidencePage, build_bundles
from app.models import EvidenceBundle, EvidenceBundleItem, JobListing, Match
from app.screenshots import (
    downscale_png, evidence_relpath, screenshot_file, screenshot_relpath, unlink_screenshot,
)

router = APIRouter()


class ScreenshotUpload(BaseModel):
    data_url: str  # "data:image/png;base64,...." — see POST /jobs/{id}/screenshot


# matches.applied_via as the CSV says it: how the application date was recorded.
_RECORDED = {"auto": "Seek confirmation page", "manual": "Marked by hand"}


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
         "Screenshot", "Screenshot Taken", "Screenshot File", "Recorded"]
    )
    files: list[tuple[str, Path]] = []
    for match, job in rows:
        path = screenshot_file(paths._screenshots_dir, match.screenshot_path)
        on_disk = path is not None and path.is_file()
        arcname = f"screenshots/{screenshot_relpath(match.screenshot_path)}" if on_disk else ""
        if on_disk:
            files.append((arcname, path))
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
            arcname,
            _RECORDED.get(match.applied_via, ""),
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
    job = db.get(JobListing, job_id)

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
    rel = evidence_relpath(ts.astimezone(), job.company if job else None, job_id)
    target = paths._screenshots_dir.joinpath(*rel.split("/"))
    if target.exists():  # another job from the same employer in the same minute
        rel = rel[:-len(".png")] + f"_{job_id}.png"
        target = paths._screenshots_dir.joinpath(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes)

    match.screenshot_path = f"screenshots/{rel}"
    match.screenshot_taken_at = ts
    db.commit()
    events.broadcast_from_thread("evidence_changed", {"job_id": job_id})

    return {
        "job_id": job_id,
        "screenshot_path": match.screenshot_path,
        "screenshot_taken_at": ts.isoformat(),
    }


# ---------------------------------------------------------------------------
# Centrelink bundles: Quick Apply screenshots collated into upload-ready PDFs
# ---------------------------------------------------------------------------
BUNDLES_DIR = "centrelink_ready"


def _bundles_dir() -> Path:
    return paths._screenshots_dir / BUNDLES_DIR


def _local(dt: datetime) -> datetime:
    return retention.utc(dt).astimezone()


def _unreported(db: Session, profile_id: int) -> list[tuple[Match, JobListing, Path | None]]:
    """Applied matches with a screenshot that no bundle holds yet, oldest application
    first, each with its file (None if the file has gone missing from disk).

    Only Quick Apply applications ever have a screenshot (apply_flow.js takes it on Seek's
    "application sent" page; Mark Applied takes none), so external ads never appear here.
    """
    reported = select(EvidenceBundleItem.id).where(EvidenceBundleItem.match_id == Match.id)
    rows = db.execute(
        select(Match, JobListing)
        .join(JobListing, Match.job_id == JobListing.id)
        .where(Match.user_id == profile_id)
        .where(Match.applied_at.is_not(None))
        .where(Match.screenshot_path.is_not(None))
        .where(~reported.exists())
        .order_by(Match.applied_at.asc(), Match.id.asc())
    ).all()
    out = []
    for match, job in rows:
        path = screenshot_file(paths._screenshots_dir, match.screenshot_path)
        out.append((match, job, path if path is not None and path.is_file() else None))
    return out


def _caption(match: Match, job: JobListing) -> tuple[str, ...]:
    when = _local(match.applied_at)
    clock = f"{when.hour % 12 or 12}:{when:%M} {'am' if when.hour < 12 else 'pm'}"
    applied = f"Applied {when:%d/%m/%Y} at {clock}"
    role = " — ".join(part for part in (job.title, job.company) if part) or f"Seek job {job.source_job_id}"
    return (applied, role, f"Seek Quick Apply · {job.url}")


def _bundle_name(now_local: datetime, count: int) -> str:
    """(time)_(date)_(number of jobs).pdf, e.g. 1312_14-10-2026_5_jobs.pdf: letters,
    digits, _ and - only, so any upload form accepts it. A clash (two bundles in the
    same minute, or one click split in two) gets _2, _3, ..."""
    stem = f"{now_local:%H%M_%d-%m-%Y}_{count}_{'job' if count == 1 else 'jobs'}"
    name, n = f"{stem}.pdf", 1
    while (_bundles_dir() / name).exists():
        n += 1
        name = f"{stem}_{n}.pdf"
    return name


def _bundle_out(bundle: EvidenceBundle) -> dict:
    return {
        "id": bundle.id,
        "filename": bundle.filename,
        "job_count": bundle.job_count,
        "size_bytes": bundle.size_bytes,
        "created_at": retention.utc(bundle.created_at).isoformat(),
        "download_url": f"/evidence/bundles/{bundle.id}/download",
        "on_disk": (_bundles_dir() / bundle.filename).is_file(),
    }


@router.get("/evidence/status")
def evidence_status(profile_id: int = 1, limit: int = 10, db: Session = Depends(get_db)) -> dict:
    """What the Overview's Centrelink card shows: applications not yet reported, and
    the most recent bundles (newest first)."""
    unreported = _unreported(db, profile_id)
    bundles = db.scalars(
        select(EvidenceBundle)
        .where(EvidenceBundle.user_id == profile_id)
        .order_by(EvidenceBundle.created_at.desc(), EvidenceBundle.id.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return {
        "unreported": sum(1 for *_, path in unreported if path is not None),
        "missing_files": sum(1 for *_, path in unreported if path is None),
        "jobs": [
            {"job_id": job.id, "title": job.title, "company": job.company,
             "applied_at": retention.utc(match.applied_at).isoformat()}
            for match, job, path in unreported if path is not None
        ],
        "bundles": [_bundle_out(b) for b in bundles],
        "folder": f"{paths._screenshots_dir.name}/{BUNDLES_DIR}",
    }


@router.post("/evidence/bundles")
def create_bundles(profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Collate every unreported Quick Apply screenshot into PDF(s) for Centrelink.

    Usually one PDF; more only when one would pass the 3 MB upload limit. Each is saved
    in ``centrelink_ready/`` and its applications count as reported from then on (their
    screenshots stay in ``evidence/<month>/`` until the normal expiry). Undo is
    ``DELETE /evidence/bundles/{id}``. No LLM call anywhere in this path.
    """
    ready = [(m, j, p) for m, j, p in _unreported(db, profile_id) if p is not None]
    if not ready:
        raise HTTPException(status_code=409, detail="No unreported Quick Apply screenshots to collate")

    pages = [EvidencePage(path, _caption(m, j)) for m, j, path in ready]
    by_page = {id(page): match for page, (match, _, _) in zip(pages, ready)}
    built = build_bundles(pages)

    now_local = datetime.now().astimezone()
    _bundles_dir().mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    created: list[EvidenceBundle] = []
    try:
        for group, data in built:
            name = _bundle_name(now_local, len(group))
            path = _bundles_dir() / name
            path.write_bytes(data)
            written.append(path)
            bundle = EvidenceBundle(
                user_id=profile_id, filename=name, job_count=len(group), size_bytes=len(data),
                created_at=now_local.astimezone(timezone.utc),
            )
            db.add(bundle)
            db.flush()
            for page in group:
                db.add(EvidenceBundleItem(bundle_id=bundle.id, match_id=by_page[id(page)].id))
            created.append(bundle)
        db.commit()
    except IntegrityError:  # a second click raced this one for the same applications
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="These applications were just collated")
    except Exception:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise

    events.broadcast_from_thread("evidence_changed", {"bundles": [b.id for b in created]})
    return {"bundles": [_bundle_out(b) for b in created]}


def _get_bundle(db: Session, bundle_id: int, profile_id: int) -> EvidenceBundle:
    bundle = db.get(EvidenceBundle, bundle_id)
    if bundle is None or bundle.user_id != profile_id:
        raise HTTPException(status_code=404, detail="Bundle not found")
    return bundle


@router.get("/evidence/bundles/{bundle_id}/download")
def download_bundle(bundle_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> FileResponse:
    bundle = _get_bundle(db, bundle_id, profile_id)
    path = _bundles_dir() / Path(bundle.filename).name
    if not path.is_file():
        raise HTTPException(status_code=410, detail="The PDF file is no longer on disk")
    return FileResponse(path, media_type="application/pdf", filename=bundle.filename)


@router.delete("/evidence/bundles/{bundle_id}")
def undo_bundle(bundle_id: int, profile_id: int = 1, db: Session = Depends(get_db)) -> dict:
    """Undo a collate: delete the PDF and put its applications back as unreported.

    Their screenshots were never moved, so nothing else changes."""
    bundle = _get_bundle(db, bundle_id, profile_id)
    filename, count = bundle.filename, bundle.job_count
    db.execute(delete(EvidenceBundleItem).where(EvidenceBundleItem.bundle_id == bundle_id))
    db.delete(bundle)
    db.commit()
    (_bundles_dir() / Path(filename).name).unlink(missing_ok=True)
    events.broadcast_from_thread("evidence_changed", {"undone": bundle_id})
    return {"undone": bundle_id, "filename": filename, "job_count": count}
