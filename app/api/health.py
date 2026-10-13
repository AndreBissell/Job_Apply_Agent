"""GET /health (the extension's liveness check) and GET /llm/usage (the budget banner)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db import app_env
from app.llm import usage as llm_usage
from app.models import Profile

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness check the extension uses to confirm the backend is up."""
    profile_id = db.scalar(select(Profile.id).order_by(Profile.id).limit(1))
    return {"status": "ok", "profile_id": profile_id, "env": app_env()}


@router.get("/llm/usage")
def llm_usage_status(db: Session = Depends(get_db)) -> dict:
    """LLM spend vs the budget caps, for the sidebar's budget banner.

    ``blocked`` means mid/strong calls (cover letters) are refused until the
    daily window rolls over or the cap is raised in ``profiles.preferences``;
    quick-screen/extraction/matching (``small``) keep running regardless.
    """
    return llm_usage.budget_status(db)
