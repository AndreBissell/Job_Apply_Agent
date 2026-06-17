"""Profile-to-job matching + cover-letter drafting.

STUB. The real implementation will score the job against the profile (0-100),
write the reasoning/gaps, upsert a ``matches`` row, and generate a draft
``cover_letters`` row. For now it just logs so the pipeline is exercisable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def match_job(job_id: int, profile_id: int) -> None:
    """TODO: implement — LLM match scoring + cover-letter generation.

    Called as a background task after extraction (or on regenerate). Must be
    self-contained (open its own DB session when implemented), since the
    request-scoped session is already closed by the time this runs. Will
    create/update the ``matches`` row (unique on user_id+job_id) and its
    ``cover_letters`` row (unique on match_id).
    """
    logger.info("TODO: implement match_job(job_id=%s, profile_id=%s)", job_id, profile_id)