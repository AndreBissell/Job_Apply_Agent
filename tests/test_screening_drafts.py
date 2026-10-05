"""Phase 9c: drafts for open-ended Quick Apply questions (app/screening/drafts.py).

No real model: the writer's ``complete_json`` is a recorder, and the 9b layer-5 fake
(``model``, autouse) stays installed so nothing can reach a provider.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.llm.client import BudgetExceededError, DailyQuotaError, LLMError
from app.models import CoverLetter, JobScreeningQuestion, LetterRun, Match, ScreeningQuestion, Skill
from app.preferences import set_preferences
from app.screening import drafts
from tests.test_screening_assist import (  # noqa: F401  (fixtures are used by name)
    D, Q_S5, Q_YEARS, TODAY, add_job, add_letter, add_profile, by_text, capture, client, db,
    engine, exp_spec, make_state, model, req, sample_q,
)

S5_SKILL = "React Native with Expo"
RN_DESC = "Built a booking app in React Native for iOS and Android. Shipped it to the App Store."
REQS = [req("R1", "Experience building apps with React Native and Expo", skill="React Native"),
        req("R2", "Teamwork", importance="nice_to_have", role="mention")]


class FakeWriter:
    """Stands in for ``complete_json`` inside answer_screening (the draft writer)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answer: dict = {"number": 1, "covered": "partly", "answer": "", "claims": [], "note": ""}
        self.exc: Exception | None = None

    def __call__(self, system, user, schema=None, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        if self.exc is not None:
            raise self.exc
        return {"answers": [dict(self.answer)]}

    def says(self, answer, claims=(), covered="partly", note="Expo isn't in your profile."):
        self.answer = {"number": 1, "covered": covered, "answer": answer, "note": note,
                       "claims": [{"quote": q, "source": s} for q, s in claims]}


@pytest.fixture()
def writer(monkeypatch):
    fake = FakeWriter()
    monkeypatch.setattr("app.llm.letter.tools.answer_screening.complete_json", fake)
    drafts._in_flight.clear()
    yield fake
    drafts._in_flight.clear()


def _sort_s5(db) -> int:
    """Sort S5's React Native question the way layer 5 would, without a model."""
    row = db.scalar(select(ScreeningQuestion).where(ScreeningQuestion.text.contains("React Native with Expo")))
    row.kind, row.strategy, row.classified_by = "assisted", "years_skill_text", "model"
    row.parameters = json.dumps({"skill": S5_SKILL})
    db.commit()
    return row.id


def setup_full(db, *, experiences=None, skills=(), help_on=True, reqs=REQS):
    """Profile with 20 months of React Native work (Jan 2024 - Aug 2025), no Expo; job 5
    with a full-pipeline letter and the S5 form captured. Returns (bank_id, profile)."""
    if experiences is None:
        experiences = [exp_spec("Mobile Developer", "job", D(2024, 1), D(2025, 8), RN_DESC,
                                skills=["React Native"])]
    p = add_profile(db, experiences, skills=skills)
    add_job(db, 5, "React Native Developer")
    add_letter(db, 5, reqs)
    capture(db, 5, *sample_q_all("S5"))
    if not help_on:
        set_preferences(db, 1, {"screening_question_help_enabled": False})
    return _sort_s5(db), p


def sample_q_all(sample_id):
    from tests.test_screening_assist import SAMPLE_BY_ID
    return SAMPLE_BY_ID[sample_id]["questions"]


def bank_id_of(db, fragment):
    return db.scalar(select(ScreeningQuestion.id).where(ScreeningQuestion.text.contains(fragment)))


def draft(db, bank_id, job_id=5):
    return drafts.draft_question(db, job_id, bank_id, 1, today=TODAY)


def pointer(p, n=1):
    return f"experience:{p['exps'][0].id}#s{n}"


# =====================================================================================
# 1. Refused in code, before any call
# =====================================================================================
class TestRefusals:
    def test_help_off_refuses(self, db, writer):
        bank_id, _ = setup_full(db, help_on=False)
        with pytest.raises(drafts.DraftRefused, match="switched off"):
            draft(db, bank_id)
        assert writer.calls == []

    def test_user_question_is_never_sent(self, db, writer):
        setup_full(db)
        salary = bank_id_of(db, "salary expectations")  # S5 Q3: free text, `user`
        with pytest.raises(drafts.DraftRefused, match="yours to answer"):
            draft(db, salary)
        assert writer.calls == []

    def test_unknown_question_is_refused(self, db, writer):
        setup_full(db)
        row = db.get(ScreeningQuestion, bank_id_of(db, "React Native with Expo"))
        row.kind, row.strategy = "unknown", None
        db.commit()
        with pytest.raises(drafts.DraftRefused):
            draft(db, row.id)
        assert writer.calls == []

    def test_choice_question_is_refused(self, db, writer):
        setup_full(db)
        capture(db, 5, Q_YEARS())  # dropdown, years_role_bracket
        with pytest.raises(drafts.DraftRefused, match="Only open-ended"):
            draft(db, bank_id_of(db, "full stack developer"))
        assert writer.calls == []

    def test_assisted_free_text_with_another_strategy_is_refused(self, db, writer):
        bank_id, _ = setup_full(db)
        row = db.get(ScreeningQuestion, bank_id)
        row.strategy = "skill_in_role_yes_no"  # a user correction could leave this on free text
        db.commit()
        with pytest.raises(drafts.DraftRefused):
            draft(db, bank_id)
        assert writer.calls == []

    @pytest.mark.parametrize("letter", ["one_shot", "no_letter", "pending"])
    def test_job_without_a_full_pipeline_letter_is_refused(self, db, writer, letter):
        add_profile(db)
        add_job(db, 5)
        if letter == "one_shot":
            add_letter(db, 5, with_run=False)
        elif letter == "pending":
            add_letter(db, 5, status="running", with_letter=False)
        capture(db, 5, Q_S5())
        bank_id = _sort_s5(db)
        with pytest.raises(drafts.DraftRefused):
            draft(db, bank_id)
        assert writer.calls == []

    def test_question_not_on_the_jobs_form_is_a_lookup_error(self, db, writer):
        bank_id, _ = setup_full(db)
        add_job(db, 6)
        with pytest.raises(LookupError):
            draft(db, bank_id, job_id=6)
        assert writer.calls == []

    def test_a_draft_already_in_flight_is_refused(self, db, writer):
        bank_id, _ = setup_full(db)
        drafts._in_flight.add((5, bank_id))
        with pytest.raises(drafts.DraftRefused, match="already being written"):
            draft(db, bank_id)
        assert writer.calls == []

    def test_every_sampled_question_reaches_the_writer_only_if_draftable(self, db, writer, client):
        """All 22 sampled questions on one full job: only S5 Q4 is ever sent."""
        setup_full(db)
        for sid in ("S1", "S2", "S3", "S4"):
            capture(db, 5, *sample_q_all(sid))
        ids = [r.question_id for r in db.scalars(select(JobScreeningQuestion).where(JobScreeningQuestion.job_id == 5))]
        sent = []
        for bank_id in ids:
            r = client.post("/jobs/5/screening-drafts", json={"bank_id": bank_id})
            if r.status_code == 200:
                sent.append(bank_id)
            else:
                assert r.status_code == 409, r.text
        assert sent == [bank_id_of(db, "React Native with Expo")]
        assert len(writer.calls) == 1
        assert "React Native with Expo" in writer.calls[0]["user"]
        for word in ("salary", "working rights", "Aboriginal", "notice", "motivated"):
            assert word not in writer.calls[0]["user"].split("=== PROFILE")[0]


# =====================================================================================
# 2. The call and what code tells the writer
# =====================================================================================
class TestTheCall:
    def test_one_mid_call_labelled_with_job_and_match_but_no_run(self, db, writer):
        bank_id, _ = setup_full(db)
        writer.says("", covered="no", note="Answer this one yourself.")
        draft(db, bank_id)
        [call] = writer.calls
        assert call["tier"] == "mid" and call["task"] == "quick_apply_draft"
        assert call["job_id"] == 5 and call["match_id"] == 5 and call["run_id"] is None
        assert call["system"].startswith(drafts.writer._SYSTEM_PROMPT)
        assert "FACTS COMPUTED BY THE APP" in call["system"]

    def test_context_carries_years_missing_parts_evidence_and_what_the_job_wants(self, db, writer):
        bank_id, p = setup_full(db)
        draft(db, bank_id)
        user = writer.calls[0]["user"]
        questions = user.split("=== PROFILE")[0]
        assert "1. How many years of experience you have with building application on React Native" in questions
        assert "at most 1 year 8 months" in questions
        assert "NOT IN PROFILE (never claim): Expo" in questions
        assert f"[{pointer(p)}] (work)" in questions
        assert '(essential): "Experience building apps with React Native and Expo"' in questions

    def test_no_dated_experience_means_no_duration(self, db, writer):
        bank_id, _ = setup_full(db, experiences=[], skills=["React Native"])
        draft(db, bank_id)
        assert "no years can be counted. State no duration" in writer.calls[0]["user"]


# =====================================================================================
# 3. Code checks on the draft
# =====================================================================================
class TestCodeChecks:
    def test_a_grounded_answer_within_the_years_is_verified(self, db, writer):
        bank_id, p = setup_full(db)
        writer.says("I have 1 year and 8 months of React Native work, building a booking app for iOS "
                    "and Android. I haven't used Expo yet.",
                    claims=[("1 year and 8 months of React Native work", pointer(p)),
                            ("building a booking app for iOS and Android", pointer(p))])
        d = draft(db, bank_id)["draft"]
        assert d["issues"] == [] and d["verified"] is True
        assert d["evidence"] == [pointer(p)]
        assert d["evidence_text"][pointer(p)].startswith("Built a booking app")
        assert d["years_months"] == 20 and d["stale"] == []

    @pytest.mark.parametrize("stated", ["about 2 years", "2 years", "over 1 year and 8 months", "3+ years"])
    def test_more_years_than_the_dates_support_is_flagged(self, db, writer, stated):
        bank_id, p = setup_full(db)
        writer.says(f"I have {stated} of React Native work.", claims=[(f"{stated} of React Native work", pointer(p))])
        d = draft(db, bank_id)["draft"]
        assert d["verified"] is False
        assert any("more than your profile's dates support" in i and "1 year 8 months" in i for i in d["issues"])

    @pytest.mark.parametrize("stated", ["under 2 years", "1 year and 8 months", "18 months", "nearly two years"])
    def test_a_figure_at_or_below_the_dates_is_fine(self, db, writer, stated):
        bank_id, p = setup_full(db)
        writer.says(f"I have {stated} of React Native work.", claims=[(f"{stated} of React Native work", pointer(p))])
        assert draft(db, bank_id)["draft"]["verified"] is True

    def test_a_missing_part_claimed_is_flagged(self, db, writer):
        bank_id, p = setup_full(db)
        writer.says("I built a booking app in React Native with Expo.",
                    claims=[("built a booking app in React Native with Expo", pointer(p))])
        d = draft(db, bank_id)["draft"]
        assert d["verified"] is False
        assert any(i.startswith("mentions Expo, which isn't in your profile") for i in d["issues"])

    def test_a_missing_part_said_honestly_is_not_flagged(self, db, writer):
        bank_id, p = setup_full(db)
        writer.says("I built a booking app in React Native. I have not used Expo.",
                    claims=[("built a booking app in React Native", pointer(p))])
        assert draft(db, bank_id)["draft"]["issues"] == []

    def test_experience_resting_on_a_listed_skill_is_flagged(self, db, writer):
        bank_id, p = setup_full(db, experiences=[], skills=["React Native"])
        skill_id = p["skills"]["React Native"].id
        writer.says("I have experience with React Native.",
                    claims=[("experience with React Native", f"skill:{skill_id}")])
        d = draft(db, bank_id)["draft"]
        assert d["verified"] is False
        assert any("rests only on a listed skill" in i for i in d["issues"])

    def test_skills_in_a_listed_skill_is_fine(self, db, writer):
        bank_id, p = setup_full(db, experiences=[], skills=["React Native"])
        skill_id = p["skills"]["React Native"].id
        writer.says("I have skills in React Native.", claims=[("skills in React Native", f"skill:{skill_id}")])
        assert draft(db, bank_id)["draft"]["issues"] == []

    @pytest.mark.parametrize("meta", [
        "My profile does not detail direct experience with Expo.",
        "Because this project is undated in my profile, I cannot state a number of years.",
    ])
    def test_a_note_for_the_candidate_inside_the_answer_is_flagged(self, db, writer, meta):
        bank_id, p = setup_full(db)
        writer.says(f"I built a booking app in React Native. {meta}",
                    claims=[("built a booking app in React Native", pointer(p))])
        d = draft(db, bank_id)["draft"]
        assert d["verified"] is False
        assert any(i.startswith("talks to you, not the employer") for i in d["issues"])

    def test_the_prompt_keeps_explanations_in_the_note(self, db, writer):
        bank_id, _ = setup_full(db)
        draft(db, bank_id)
        assert "pasted to the employer exactly as written" in writer.calls[0]["system"]

    def test_verify_issues_are_kept(self, db, writer):
        bank_id, _ = setup_full(db)
        writer.says("I built a booking app in React Native.",
                    claims=[("built a booking app in React Native", "experience:999#s1")])
        d = draft(db, bank_id)["draft"]
        assert d["verified"] is False and any("not in your profile" in i for i in d["issues"])

    def test_covered_no_is_blank_and_never_verified(self, db, writer):
        bank_id, _ = setup_full(db)
        writer.says("I probably have some.", covered="no", note="Answer this one yourself.")
        d = draft(db, bank_id)["draft"]
        assert (d["answer"], d["covered"], d["verified"], d["issues"]) == ("", "no", False, [])
        assert d["note"] == "Answer this one yourself."


class TestStatedDurations:
    @pytest.mark.parametrize("text,expected", [
        ("1 year and 8 months", [20]), ("over 2 years", [25]), ("about 2 years", [24]),
        ("under 2 years", []), ("nearly three years", []), ("up to 2 years", []),
        ("two and a half years", [30]), ("1-2 years", [24]), ("18 months", [18]), ("3+ years", [36]),
        ("half a year", [6]), ("a 3-month internship", [3]), ("Java years ago", []), ("data analyst", []),
    ])
    def test_months_claimed(self, text, expected):
        assert [m for _, m in drafts.stated_durations(text)] == expected


# =====================================================================================
# 4. Stored, served by the assist view, and stale
# =====================================================================================
class TestCache:
    def _good(self, writer, p):
        writer.says("I built a booking app in React Native.",
                    claims=[("built a booking app in React Native", pointer(p))])

    def _view(self, client):
        r = client.get("/jobs/5/screening-assist")
        assert r.status_code == 200, r.text
        return by_text(r.json(), "React Native with Expo")

    def test_stored_on_the_link_and_served_without_a_call(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        r = client.post("/jobs/5/screening-drafts", json={"bank_id": bank_id})
        assert r.status_code == 200, r.text
        stored = json.loads(db.get(JobScreeningQuestion, (5, bank_id)).draft)
        assert stored["profile_id"] == 1 and stored["fingerprint"]["run_id"] is not None
        q = self._view(client)
        assert q["draftable"] is True
        assert q["draft"]["answer"] == "I built a booking app in React Native."
        assert q["draft"]["verified"] is True and q["draft"]["stale"] == []
        assert len(writer.calls) == 1  # the GET made no call

    def test_get_never_drafts(self, db, writer, client):
        setup_full(db)
        q = self._view(client)
        assert q["draftable"] is True and q["draft"] is None
        assert writer.calls == []

    def test_only_draftable_questions_are_marked(self, db, writer, client):
        setup_full(db)
        out = client.get("/jobs/5/screening-assist").json()
        flags = {q["text"][:30]: q["draftable"] for q in out["questions"]}
        assert sum(flags.values()) == 1
        assert by_text(out, "salary expectations")["draftable"] is False

    def test_a_profile_edit_makes_it_stale(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        db.add(Skill(user_id=1, name="Expo"))
        db.commit()
        assert self._view(client)["draft"]["stale"] == [drafts.STALE_PROFILE]

    def test_a_date_edit_makes_it_stale(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        p["exps"][0].end_date = D(2025, 12)
        db.commit()
        assert drafts.STALE_PROFILE in self._view(client)["draft"]["stale"]

    def test_a_sorting_correction_makes_it_stale(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        r = client.patch(f"/screening-questions/{bank_id}",
                         json={"kind": "assisted", "strategy": "free_text_describe",
                               "parameters": {"skill": "React Native"}})
        assert r.status_code == 200, r.text
        assert self._view(client)["draft"]["stale"] == [drafts.STALE_QUESTION]

    def test_a_rewritten_letter_makes_it_stale(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        state = make_state(REQS, 5, "A rewritten letter.")
        db.add(LetterRun(match_id=5, engine="agent", status="done", state=state.model_dump_json(),
                         final_draft_version=1))
        letter = db.scalar(select(CoverLetter).where(CoverLetter.match_id == 5))
        letter.generated_content = "A rewritten letter."
        db.commit()
        assert self._view(client)["draft"]["stale"] == [drafts.STALE_RUN]

    def test_a_new_draft_overwrites_and_is_current_again(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        db.add(Skill(user_id=1, name="Expo"))
        db.commit()
        writer.says("I built a booking app in React Native and Expo.",
                    claims=[("built a booking app in React Native", pointer(p))])
        r = client.post("/jobs/5/screening-drafts", json={"bank_id": bank_id})
        assert r.status_code == 200, r.text
        q = self._view(client)
        assert q["draft"]["stale"] == [] and "Expo" in q["draft"]["answer"]
        assert len(writer.calls) == 2

    def test_help_off_hides_a_stored_draft(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        set_preferences(db, 1, {"screening_question_help_enabled": False})
        q = self._view(client)
        assert q["draft"] is None and q["draftable"] is False

    def test_another_profiles_draft_is_not_served(self, db, writer, client):
        bank_id, p = setup_full(db)
        self._good(writer, p)
        draft(db, bank_id)
        link = db.get(JobScreeningQuestion, (5, bank_id))
        data = json.loads(link.draft)
        data["profile_id"] = 2
        link.draft = json.dumps(data)
        db.commit()
        assert self._view(client)["draft"] is None


# =====================================================================================
# 5. The endpoint's errors
# =====================================================================================
class TestEndpoint:
    @pytest.mark.parametrize("exc,status,text", [
        (BudgetExceededError("daily cap $5.00 reached"), 429, "spending cap"),
        (DailyQuotaError("quota"), 503, "daily quota"),
        (LLMError("boom"), 502, "Couldn't draft"),
        (ValueError("bad json"), 502, "Couldn't draft"),
    ])
    def test_model_errors_are_readable_and_store_nothing(self, db, writer, client, exc, status, text):
        bank_id, _ = setup_full(db)
        writer.exc = exc
        r = client.post("/jobs/5/screening-drafts", json={"bank_id": bank_id})
        assert r.status_code == status and text in r.json()["detail"]
        assert db.get(JobScreeningQuestion, (5, bank_id)).draft is None
        assert drafts._in_flight == set()  # released on failure

    def test_refusal_is_409_and_unknown_job_404(self, db, writer, client):
        setup_full(db)
        r = client.post("/jobs/5/screening-drafts", json={"bank_id": bank_id_of(db, "salary expectations")})
        assert r.status_code == 409 and "yours to answer" in r.json()["detail"]
        assert client.post("/jobs/77/screening-drafts", json={"bank_id": 1}).status_code == 404
        assert writer.calls == []
