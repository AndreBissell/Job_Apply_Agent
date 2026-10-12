"""FastAPI backend for the Seek Job Assistant Chrome extension.

Receives job listings the user's *real* browser scraped from Seek pages they
opened themselves (POST /ingest) and upserts them into ``job_listings``. /ingest
makes no LLM call: the idle loop (``_processing_idle_loop``) does the AI work one
job at a time on a single worker thread, in this order: retention sweep, cover
letters, quick screen, extraction + matching. Also serves everything the side panel
and profile editor read and write (/jobs, /profile, /obligation, letters, screening).

Run it with ``python scripts/run_api.py real|test`` (uvicorn on 127.0.0.1, port
8000 for real and 8001 for test).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import retention
from app.api import events, idle_loop, paths
from app.api.deps import get_db  # re-exported: tests key dependency_overrides on it
from app.api.evidence import router as evidence_router
from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.api.job_actions import router as job_actions_router
from app.api.jobs import router as jobs_router
from app.api.letters import router as letters_router
from app.api.obligation import router as obligation_router
from app.api.origin_guard import OriginGuardMiddleware
from app.api.profile import router as profile_router
from app.api.search import router as search_router
from app.api.profile_ui import router as profile_ui_router
from app.api.screening import router as screening_router
from app.llm.letter import view as letter_view
from app.models import (
    CoverLetter,
    JobListing,
    Match,
)
from app.preferences import get_preferences

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    events._event_loop = asyncio.get_running_loop()
    task = asyncio.create_task(idle_loop._processing_idle_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Seek Job Assistant", version="0.1.0", lifespan=lifespan)

# No CORS middleware, on purpose: with no Access-Control-* headers a web page cannot
# read any response or send a preflighted call (JSON body, PATCH/PUT/DELETE) to this
# server. The extension doesn't need them (its pages and background worker have
# host_permissions for localhost), and the profile editor is served from this same
# origin. See docs/cleanup-3-8-plan.md step 5.1.
# What CORS can't stop is a "simple" cross-site write (a body-less POST such as
# /regenerate), so OriginGuardMiddleware refuses writes from other websites.
app.add_middleware(OriginGuardMiddleware)

app.include_router(profile_ui_router)
app.include_router(letters_router)
app.include_router(screening_router)
app.include_router(obligation_router)
app.include_router(health_router)
app.include_router(events.router)
app.include_router(ingest_router)
app.include_router(evidence_router)
app.include_router(search_router)
app.include_router(job_actions_router)
app.include_router(jobs_router)
app.include_router(profile_router)

app.mount("/static", StaticFiles(directory=str(paths._static_dir)), name="static")
app.mount("/screenshots", StaticFiles(directory=str(paths._screenshots_dir)), name="screenshots")


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
