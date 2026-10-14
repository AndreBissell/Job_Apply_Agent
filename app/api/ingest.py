"""POST /ingest: upsert the listings the extension captured from pages the user opened.

``SOURCE`` is also used by main's job routes (a plain constant, so a normal import is fine).
Makes no LLM call: the idle loop does the AI work later.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import availability
from app.api.deps import get_db
from app.models import JobListing

router = APIRouter()

SOURCE = "seek"


class IngestListing(BaseModel):
    source_job_id: str
    url: str
    title: str
    company: str | None = None
    location: str | None = None
    classification: str | None = None
    subclassification: str | None = None
    work_type: str | None = None
    salary: str | None = None
    raw_description: str | None = None
    discovered_query: str | None = None


class IngestBody(BaseModel):
    listings: list[IngestListing]
    profile_id: int = 1


@router.post("/ingest")
def ingest(
    body: IngestBody,
    db: Session = Depends(get_db),
) -> dict:
    """Upsert scraped listings; backfill ``raw_description`` on existing rows.

    Insert when ``(source='seek', source_job_id)`` is new. If the row already
    exists but has no description and this payload carries one, update it.
    LLM extraction+matching is handled by the idle loop — no burst on ingest.

    Returns each listing's internal ``job_id`` (same order as the request body)
    so a caller that just ingested a single detail page — e.g. the sidebar's
    Scan Page early-exit loop — can immediately act on that specific job
    (currently: POST /jobs/{id}/quick-screen) without a second lookup.
    """
    received = len(body.listings)
    new = 0
    updated = 0
    job_ids: list[int] = []

    for item in body.listings:
        existing = db.scalar(
            select(JobListing).where(
                JobListing.source == SOURCE,
                JobListing.source_job_id == item.source_job_id,
            )
        )

        if existing is None:
            job = JobListing(
                source=SOURCE,
                source_job_id=item.source_job_id,
                url=item.url,
                title=item.title,
                company=item.company,
                location=item.location,
                classification=item.classification,
                subclassification=item.subclassification,
                work_type=item.work_type,
                salary=item.salary,
                discovered_query=item.discovered_query,
                raw_description=item.raw_description,
            )
            db.add(job)
            db.flush()  # assign job.id before we report it back
            new += 1
            job_ids.append(job.id)  # last_seen_live_at defaults to now: it was just captured
        else:
            # A listing is typically ingested twice — once as a search-results
            # card (which carries discovered_query but no description or
            # taxonomy) and once as a detail page (the reverse). Backfill any
            # field this payload can fill and the row is still missing, so
            # whichever arrives second completes the row. Existing values are
            # never overwritten: the first capture of a field is the one made
            # while the page was actually open.
            changed = False
            for attr in (
                "raw_description",
                "classification",
                "subclassification",
                "discovered_query",
                # Card fields too: a job first captured from its detail page
                # used to keep company/location NULL forever, even after its
                # search card (which carries them) was ingested.
                "company",
                "location",
                "work_type",
                "salary",
            ):
                if getattr(existing, attr) is None and getattr(item, attr):
                    setattr(existing, attr, getattr(item, attr))
                    changed = True
            if changed:
                updated += 1
            # A capture that carries a description is proof the ad is live right now (the
            # page was open with its description on it): un-expire a job flagged closed,
            # clear its strikes and refresh the "seen live" stamp the LLM gate reads. A
            # card or a "NO DESCRIPTION" scan proves nothing and changes none of this.
            if (item.raw_description or "").strip():
                availability.mark_live(existing)
            job_ids.append(existing.id)

    db.commit()
    return {"received": received, "new": new, "updated": updated, "job_ids": job_ids}
