"""Tests for the score-weighted search-phrase miner (app/search_suggest.py).

The regression these lock down is the original frequency-counting behaviour,
which on the dev profile emitted ``intermediate software`` and ``intermediate
software engineer`` side by side and ranked ``software engineer`` top despite
it averaging below the baseline.
"""

from __future__ import annotations

from app.search_suggest import (
    deduplicate,
    mine,
    phrases_in_title,
    rank_phrases,
    title_segments,
)


class TestTitleSegments:
    def test_keeps_only_the_leading_segment(self):
        assert title_segments("Junior .Net Developer | I.T Services | 5 Days Onsite") == [
            "Junior .Net Developer"
        ]

    def test_splits_on_spaced_hyphen_not_hyphenated_words(self):
        assert title_segments("Software Engineer - Business Systems") == ["Software Engineer"]
        assert title_segments("Full-Stack Developer") == ["Full-Stack Developer"]

    def test_empty_title(self):
        assert title_segments("") == []


class TestPhrasesInTitle:
    def test_strips_seniority_modifiers(self):
        # "Intermediate" is a filter, not a keyword — it must not survive into
        # a phrase, which is what produced the bad suggestions originally.
        phrases = phrases_in_title("Junior-Intermediate Software Engineer")
        assert "software engineer" in phrases
        assert not any("intermediate" in p for p in phrases)

    def test_requires_a_role_noun_at_the_end(self):
        phrases = phrases_in_title("Experienced Software Engineer Front-end Developer")
        assert all(p.split()[-1] in {"engineer", "developer"} for p in phrases)
        assert "engineer front" not in phrases

    def test_returns_a_set_so_repeats_count_once(self):
        assert phrases_in_title("Data Analyst") == {"data analyst"}

    def test_title_with_no_role_noun_yields_nothing(self):
        assert phrases_in_title("Exciting New Opportunity") == set()


class TestRankPhrases:
    def test_high_scoring_phrase_outranks_frequent_low_scoring_one(self):
        # 6 mediocre "software engineer" jobs vs 2 strong "data analyst" ones.
        # Frequency would pick the former; lift must pick the latter.
        scored = [("Software Engineer", 55.0)] * 6 + [("Data Analyst", 92.0)] * 2
        ranked = rank_phrases(scored)
        assert ranked[0].phrase == "data analyst"
        assert "software engineer" not in [s.phrase for s in ranked]

    def test_shrinkage_stops_a_single_job_dominating(self):
        # One 100-point outlier against three solid 85s: the phrase with real
        # support must win despite the lower raw mean.
        scored = [("Lucky Officer", 100.0)] + [("Data Analyst", 85.0)] * 3 + [
            ("Software Engineer", 40.0)
        ] * 4
        ranked = {s.phrase: s for s in rank_phrases(scored)}
        assert ranked["data analyst"].rank > ranked["lucky officer"].rank
        assert ranked["lucky officer"].mean > ranked["data analyst"].mean

    def test_below_baseline_phrases_are_dropped(self):
        scored = [("Data Analyst", 90.0)] * 3 + [("Software Engineer", 30.0)] * 3
        assert "software engineer" not in [s.phrase for s in rank_phrases(scored)]

    def test_empty_input(self):
        assert rank_phrases([]) == []

    def test_support_counts_titles_not_occurrences(self):
        # Needs a contrasting title: a phrase present in every match IS the
        # baseline, so its lift is zero and it is correctly filtered out.
        scored = [("Data Analyst", 90.0), ("Data Analyst", 80.0), ("Warehouse Operator", 20.0)]
        ranked = {s.phrase: s for s in rank_phrases(scored)}
        assert ranked["data analyst"].support == 2

    def test_a_phrase_in_every_match_carries_no_signal(self):
        assert rank_phrases([("Data Analyst", 90.0), ("Data Analyst", 80.0)]) == []


class TestDeduplicate:
    def test_drops_nested_phrases(self):
        scored = [("Business Systems Analyst", 90.0)] * 3 + [("Software Engineer", 40.0)] * 3
        phrases = [s.phrase for s in deduplicate(rank_phrases(scored))]
        # "business systems analyst" and "systems analyst" both qualify; only
        # the better-ranked one survives.
        assert len(phrases) == len(set(phrases))
        assert not any(a != b and (a in b) for a in phrases for b in phrases)

    def test_collapses_one_role_family(self):
        scored = [
            ("Data Analyst", 90.0),
            ("Senior Data Analyst", 88.0),
            ("Graduate Data Analyst", 92.0),
            ("Warehouse Operator", 30.0),
        ]
        phrases = [s.phrase for s in mine(scored)]
        assert phrases.count("data analyst") == 1


class TestMineEndToEnd:
    def test_reproduces_the_expected_ranking_on_representative_data(self):
        """The dev profile's real shape: many mediocre software titles, a few
        strong admin/data ones. The admin and data roles must surface and no
        'intermediate' fragment may appear."""
        scored = [
            ("Software Engineer", 92.0),
            ("Graduate Data Analyst", 92.0),
            ("Software Engineer Graduate", 90.0),
            ("Administration Assistant", 90.0),
            ("Administration/Data Entry Officer", 88.0),
            ("Junior Administration Assistant", 82.0),
            ("Junior-Intermediate Software Engineer", 78.0),
            ("Graduate / Intermediate .NET Developer", 72.0),
            ("Senior Software Developer", 60.0),
            ("Software Engineer", 58.0),
            ("Software Engineer (.NET, Vue.js)", 32.0),
            ("Senior Software Engineer", 32.0),
            ("Software Engineer", 15.0),
        ]
        phrases = [s.phrase for s in mine(scored)]

        assert "administration assistant" in phrases
        assert not any("intermediate" in p for p in phrases)
        # Ranked above the software titles, which average below baseline here.
        assert phrases.index("administration assistant") == 0

    def test_no_matches_yields_no_suggestions(self):
        assert mine([]) == []
