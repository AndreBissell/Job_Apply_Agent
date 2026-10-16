"""Skills ads ask for that the profile doesn't list (docs/pipeline-tab-plan.md D10).

The Pipeline tab's "Skills ads ask for" list: the hard ``job_skills`` of every job scored in
the last ``gaps.WINDOW_DAYS`` days (hidden and applied jobs included: they are still what the
market asked for), less the skills the profile has, the remembered "no"s (those are on the
to-work-on list already) and the skills the user chose to ignore. Code only, no LLM.

A profile skill covers a job skill when either name holds the other as a whole phrase
(``gaps.skill_matches`` both ways), NOT by exact name as ``prefilter._partition`` does: the
real profile's skills came from Seek with suffixes ("Typescript Programming", "Docker
Software"), and exact matching listed TypeScript, React and Docker as gaps (565 false-or-real
gap asks on real.db, 395 with whole-phrase matching; plan, "Finding").

The user's answers: ``have`` adds the skill to the profile (``origin='ask_user'``, as a
Quick Apply Yes), ``not_yet`` saves a remembered "no" (which moves it to to-work-on), and
``ignore`` keeps its key in ``profiles.preferences["gap_ignored"]``.
"""

from __future__ import annotations

import datetime
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import gaps
from app.llm.prefilter import normalise_skill
from app.models import JobSkill, Match, Skill
from app.preferences import get_preferences, set_preferences
from app.retention import now_utc, utc

IGNORED_KEY = "gap_ignored"
MAX_IGNORED = 500  # a cap so the preferences blob can't grow without end


def covered(job_skill: str, profile_skills: list[str]) -> bool:
    """Does any profile skill cover this job skill (whole phrase, either way round)?"""
    key = normalise_skill(job_skill)
    return any(
        gaps.skill_matches(key, p) or gaps.skill_matches(normalise_skill(p), job_skill)
        for p in profile_skills
    )


def ignored_keys(db: Session, user_id: int) -> list[str]:
    raw = get_preferences(db, user_id).get(IGNORED_KEY)
    return [k for k in raw if isinstance(k, str)] if isinstance(raw, list) else []


def market_gaps(
    db: Session,
    user_id: int,
    *,
    days: int = gaps.WINDOW_DAYS,
    now: datetime.datetime | None = None,
) -> dict:
    """``{"window_days", "ads", "items": [{"skill", "key", "ads", "avg_score"}]}``.

    ``ads`` is how many scored jobs in the window list any hard skill (the "of M"); each
    item's ``ads`` is how many of them ask for that skill, ``avg_score`` their mean score.
    Sorted by ads, then average score (higher first), then name.
    """
    cutoff = (utc(now) or now_utc()) - datetime.timedelta(days=days)
    rows = db.execute(
        select(Match.job_id, Match.score, Match.scored_at, Match.created_at, JobSkill.name)
        .join(JobSkill, JobSkill.job_id == Match.job_id)
        .where(Match.user_id == user_id, Match.score.is_not(None), JobSkill.skill_type == "hard")
    ).all()
    rows = [r for r in rows if (utc(r.scored_at) or utc(r.created_at) or cutoff) >= cutoff]

    profile_skills = list(db.scalars(select(Skill.name).where(Skill.user_id == user_id)))
    decisions = gaps.active_decisions(db, user_id)
    ignored = set(ignored_keys(db, user_id))

    seen_ads: set[int] = set()
    ads_by_key: dict[str, set[int]] = defaultdict(set)
    spellings: dict[str, Counter] = defaultdict(Counter)
    scores: dict[int, float] = {}
    verdict: dict[str, bool] = {}  # key -> is it a gap (cached: names repeat across ads)
    for job_id, score, _, _, name in rows:
        name = " ".join((name or "").split())
        key = normalise_skill(name) if name else ""
        if not key:
            continue
        seen_ads.add(job_id)
        if key not in verdict:
            verdict[key] = not (
                key in ignored
                or covered(name, profile_skills)
                or gaps.find_decision(decisions, name) is not None
            )
        if not verdict[key]:
            continue
        ads_by_key[key].add(job_id)
        spellings[key][name] += 1
        scores[job_id] = float(score)

    items = []
    for key, job_ids in ads_by_key.items():
        label = sorted(spellings[key].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        items.append({
            "skill": label, "key": key, "ads": len(job_ids),
            "avg_score": round(sum(scores[j] for j in job_ids) / len(job_ids)),
        })
    items.sort(key=lambda i: (-i["ads"], -i["avg_score"], i["skill"].lower()))
    return {"window_days": days, "ads": len(seen_ads), "items": items}


def not_yet(db: Session, user_id: int, skill: str) -> gaps.GapDecision:
    """Remember a "no" for this skill (seeded with every ad already in the DB). Commits."""
    skill = " ".join((skill or "").split())
    key = gaps.skill_key(skill)
    if not key:
        raise ValueError("Which skill is this about?")
    return gaps.save_no(db, user_id, label=skill, key=key)


def ignore(db: Session, user_id: int, skill: str) -> list[str]:
    """Leave this skill out of the list from now on. Commits. Returns the ignored keys."""
    key = normalise_skill(" ".join((skill or "").split()))
    if not key:
        raise ValueError("Which skill is this about?")
    keys = [k for k in ignored_keys(db, user_id) if k != key] + [key]
    set_preferences(db, user_id, {IGNORED_KEY: keys[-MAX_IGNORED:]})
    return keys


def unignore(db: Session, user_id: int, skill: str) -> list[str]:
    """Undo ``ignore``. Commits. Returns the ignored keys."""
    key = normalise_skill(" ".join((skill or "").split()))
    keys = [k for k in ignored_keys(db, user_id) if k != key]
    set_preferences(db, user_id, {IGNORED_KEY: keys})
    return keys
