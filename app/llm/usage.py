"""Spend totals and the budget cap check, read from the ``llm_usage`` log.

Kept apart from client.py so the API (a usage endpoint for the sidebar) can use
it without pulling in the provider SDKs. Caps live in ``profiles.preferences``
(``llm_daily_budget_usd`` / ``llm_total_budget_usd``); "today" is the local
calendar day, stored and compared in UTC.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LlmUsage
from app.preferences import DEFAULTS, get_preferences


def local_day_start_utc(now: datetime.datetime | None = None) -> datetime.datetime:
    """Midnight at the start of the local day, as an aware UTC datetime."""
    local_now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone()
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(datetime.timezone.utc)


def spend_usd(db: Session, since: datetime.datetime | None = None) -> float:
    q = select(func.coalesce(func.sum(LlmUsage.cost_usd), 0))
    if since is not None:
        q = q.where(LlmUsage.created_at >= since)
    return float(db.scalar(q) or 0)


def _cap(value: object, key: str) -> float:
    """A positive number, else the default — a bad stored value never disables the guard."""
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    return float(value) if ok else float(DEFAULTS[key])


def budget_status(db: Session, profile_id: int = 1) -> dict:
    prefs = get_preferences(db, profile_id)
    daily_cap = _cap(prefs.get("llm_daily_budget_usd"), "llm_daily_budget_usd")
    total_cap = _cap(prefs.get("llm_total_budget_usd"), "llm_total_budget_usd")
    today = spend_usd(db, local_day_start_utc())
    total = spend_usd(db)

    reason = None
    if total >= total_cap:
        reason = f"Total LLM budget reached (${total:.2f} of ${total_cap:.2f})."
    elif today >= daily_cap:
        reason = f"Daily LLM budget reached (${today:.2f} of ${daily_cap:.2f})."
    return {
        "spent_today_usd": round(today, 4),
        "spent_total_usd": round(total, 4),
        "daily_cap_usd": daily_cap,
        "total_cap_usd": total_cap,
        "blocked": reason is not None,
        "reason": reason,
    }
