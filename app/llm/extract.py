"""Job skill / requirement extraction from a listing's ``raw_description``.

STUB. The real implementation will read ``job_listings.raw_description`` for the
given job, ask an LLM to pull out skills + qualification/experience requirements,
and populate ``job_skills`` (and the ``*_requirements`` columns). For now it just
logs so the API's ingest → background-task pipeline is exercisable end to end.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def extract_job(job_id: int) -> None:
    """TODO: implement — LLM extraction of job_skills / *_requirements.

    Called as a background task after a listing with a ``raw_description`` is
    ingested. Must be self-contained (open its own DB session when implemented),
    since the request-scoped session is already closed by the time this runs.
    """
    logger.info("TODO: implement extract_job(job_id=%s)", job_id)