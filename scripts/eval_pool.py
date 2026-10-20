"""Eval-pool tracker: sorts every usable ad in real.db into the eval scenarios (read-only).

docs/eval-set-plan.md defines the scenarios and their rules. These tags are PROVISIONAL, from
the score, job_skills vs the profile and, for 85+ ads, the job's latest letter run. The final
tags come from analyze_job + match_profile in the eval copy, confirmed by the owner.
scripts/eval_draw.py draws the candidates from these tags.

    python scripts/eval_pool.py
"""
import json
import os
import re
import sqlite3
import sys

os.environ["DATABASE_URL"] = "sqlite:///real.db"
sys.path.insert(0, ".")
from app.skill_gaps import covered  # noqa: E402  (whole-phrase match, both ways)

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles are cp1252; Seek titles aren't
    sys.stdout.reconfigure(encoding="utf-8")

TARGET = {  # scenario -> (dev, test)
    1: ("Strong fit, no must-have gap (85+)", 7, 5),
    2: ("Missing a nice-to-have", 3, 2),
    3: ("Missing a must-have (ask_user)", 4, 3),
    4: ("Listing-only skill", 2, 2),
    # 5 (screening questions in the ad text) dropped 2026-10-20: none of 116 real ads had one
    6: ("Borderline (70-84)", 2, 2),
    7: ("Unrelated (<40)", 2, 1),
}
RESERVE = 15

# Strong-fit rule (owner, 2026-10-20; docs/eval-set-plan.md): tags follow what changes the
# agent's path, read from the job's latest letter run. Partials are allowed everywhere.
#   must-have gap  = Requirement.needs_user: essential, headline/mention, status "gap"
#   nice-to-have   = importance nice_to_have, status partial or gap
# Without a run the old text guess is used and the tag is marked "?".
DESIRABLE = re.compile(r"desirable|nice[- ]to[- ]have|bonus|advantageous|an advantage|preferred", re.I)

c = sqlite3.connect("file:real.db?mode=ro", uri=True)
q = lambda s, *a: c.execute(s, a).fetchall()

profile_skills = [r[0] for r in q("select name from skills where user_id=1")]
backed = [r[0] for r in q(
    "select distinct s.name from skills s join experience_skills es on es.skill_id=s.id where s.user_id=1")]
listed_only = [s for s in profile_skills if s not in backed]
experience_text = " ".join(r[0] or "" for r in q("select description from experiences where user_id=1"))
profile_changed = q("select max(t) from (select max(updated_at) t from experiences where user_id=1"
                    " union all select max(updated_at) from skills where user_id=1)")[0][0]


def run_verdict(job_id):
    """(must_gaps, nice_unsupported, essential_partials, stale_run) from the latest run, else None."""
    r = q("""select lr.state, lr.started_at from letter_runs lr join matches m on m.id = lr.match_id
             where m.job_id = ? and m.user_id = 1 and lr.state is not null order by lr.id desc limit 1""", job_id)
    if not r:
        return None
    reqs = json.loads(r[0][0]).get("requirements") or []
    if not reqs:
        return None
    must = [x for x in reqs if x["importance"] == "essential" and x["letter_role"] in ("headline", "mention")
            and x["status"] == "gap"]
    nice = [x for x in reqs if x["importance"] == "nice_to_have" and x["letter_role"] != "not_for_letter"
            and x["status"] in ("partial", "gap")]
    partials = [x for x in reqs if x["importance"] == "essential" and x["letter_role"] != "not_for_letter"
                and x["status"] == "partial"]
    label = lambda x: x.get("skill") or x["text"][:30]
    return [label(x) for x in must], [label(x) for x in nice], len(partials), bool(
        profile_changed and str(r[0][1]) < str(profile_changed))


jobs = q("""select j.id, j.title, j.company, m.score, m.scored_at, j.raw_description
            from job_listings j join matches m on m.job_id=j.id and m.user_id=1
            where j.raw_description is not null and length(j.raw_description) > 200
              and j.expired_detected_at is null and m.score is not null""")

rows = []
for jid, title, company, score, scored_at, desc in jobs:
    hard = [r[0] for r in q("select name from job_skills where job_id=? and skill_type='hard'", jid)]
    missing = [s for s in hard if not covered(s, profile_skills)]
    # Backed if linked to an experience OR named in an experience's text: the agent's match_profile
    # cites sentences (e.g. "React.js" at Rio Tinto, experience:1#s2), not only the skill links.
    via_listing = [s for s in hard if covered(s, listed_only) and not covered(s, backed)
                   and not re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", experience_text, re.I)]
    how = None
    if score < 40:
        sc = 7
    elif score >= 85:  # strong fits first; a listing-only must-have is settled at final tagging
        verdict = run_verdict(jid)
        if verdict:
            must, nice, partials, stale_run = verdict
            sc = 3 if must else 2 if nice else 1
            how = (f"partials {partials}" + (f"; must-have gap: {', '.join(must)}" if must else "")
                   + (f"; nice-to-have: {', '.join(nice[:3])}" if nice else "")
                   + (" (run before profile change)" if stale_run else ""))
        else:
            sc = 2 if missing and DESIRABLE.search(desc) else 1
            how = "? no letter run yet: text guess"
    elif score >= 70:  # borderline is a score band: checked before listing-only, which nearly
        sc = 6         # every 70-84 developer ad also has (owner, 2026-10-20)
    elif via_listing and score >= 60:
        sc = 4
    elif missing:
        sc = 3
    else:
        sc = None
    stale = scored_at is None or (profile_changed and str(scored_at) < str(profile_changed))
    rows.append((sc, score, jid, title, company or "", stale, missing, via_listing, how))

print(f"Usable ads: {len(rows)}   (need {sum(d + t for _, d, t in TARGET.values())} + ~{RESERVE} reserve)")
print(f"Scored BEFORE the profile change ({profile_changed}): {sum(r[5] for r in rows)}  (re-scored later)\n")
print(f"{'Scenario':40} {'have':>5} {'need':>5}  status")
for k, (name, d, t) in TARGET.items():
    have = sum(1 for r in rows if r[0] == k)
    need = d + t
    status = "OK" if have >= need else f"short {need - have}"
    print(f"{k}. {name:37} {have:>5} {need:>5}  {status}")
print(f"-  {'(40-69, no clear scenario)':37} {sum(1 for r in rows if r[0] is None):>5}")

print("\nAds by scenario (* = scored before the profile change):")
for k in [*TARGET, None]:
    group = sorted((r for r in rows if r[0] == k), key=lambda r: -r[1])
    if not group:
        continue
    print(f"\n[{k or '-'}] {TARGET[k][0] if k else 'unassigned'}")
    for sc, score, jid, title, company, stale, missing, via, how in group:
        extra = [how] if how else []
        if missing:
            extra.append("missing: " + ", ".join(missing[:4]))
        if via:
            extra.append("listing-only: " + ", ".join(via[:3]))
        print(f"  {score:>3}{'*' if stale else ' '} #{jid:<4} {title[:45]:45} {company[:22]:22} {'; '.join(extra)}")
