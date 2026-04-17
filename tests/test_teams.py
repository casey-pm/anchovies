"""Tests for track-based team structure and keyword routing."""

import pytest

from anchovies.teams import (
    TRACKS,
    get_relevant_personas,
    get_suggested_persona,
    get_track,
    get_track_display_name,
    get_track_lead,
    get_track_members,
)


# ---------------------------------------------------------------------------
# Track membership
# ---------------------------------------------------------------------------


class TestGetTrack:
    def test_data_engineering_members(self):
        for member in ["elena", "james", "victor", "anna"]:
            assert get_track(member) == "data_engineering"

    def test_analytics_members(self):
        for member in ["sofia", "julia", "raj", "leo"]:
            assert get_track(member) == "analytics"

    def test_bi_reporting_members(self):
        for member in ["natalie", "tom", "priya", "mike", "nina"]:
            assert get_track(member) == "bi_reporting"

    def test_leadership_members(self):
        for member in ["marcus", "kai", "olivia"]:
            assert get_track(member) == "leadership"

    def test_case_insensitive(self):
        assert get_track("Sofia") == "analytics"
        assert get_track("ELENA") == "data_engineering"

    def test_unknown_member(self):
        assert get_track("unknown") is None

    def test_all_16_covered(self):
        """Every team member must belong to exactly one track."""
        all_members = set()
        for track in TRACKS.values():
            all_members.update(track["members"])
        assert len(all_members) == 16


class TestGetTrackLead:
    def test_data_engineering_lead(self):
        assert get_track_lead("james") == "elena"
        assert get_track_lead("elena") == "elena"

    def test_analytics_lead(self):
        assert get_track_lead("raj") == "sofia"

    def test_bi_lead(self):
        assert get_track_lead("mike") == "natalie"

    def test_leadership_lead(self):
        assert get_track_lead("kai") == "marcus"

    def test_unknown(self):
        assert get_track_lead("nobody") is None


class TestGetTrackMembers:
    def test_data_engineering(self):
        members = get_track_members("data_engineering")
        assert set(members) == {"elena", "james", "victor", "anna"}

    def test_unknown_track(self):
        assert get_track_members("nonexistent") == []


class TestGetTrackDisplayName:
    def test_known_tracks(self):
        assert get_track_display_name("data_engineering") == "Data Engineering"
        assert get_track_display_name("analytics") == "Analytics & Science"
        assert get_track_display_name("bi_reporting") == "BI & Reporting"
        assert get_track_display_name("leadership") == "Leadership & Quality"

    def test_unknown_fallback(self):
        result = get_track_display_name("some_track")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Keyword routing
# ---------------------------------------------------------------------------


class TestGetRelevantPersonas:
    def test_pipeline_routes_to_data_engineering(self):
        results = get_relevant_personas("fix the data pipeline ingestion")
        members = [r[0] for r in results]
        # Data engineering members should be top results
        assert any(m in ["elena", "james", "victor", "anna"] for m in members[:3])

    def test_dbt_routes_to_analytics(self):
        results = get_relevant_personas("update the dbt model for staging")
        members = [r[0] for r in results]
        assert "sofia" in members[:3]

    def test_dashboard_routes_to_bi(self):
        results = get_relevant_personas("fix the dashboard visualization")
        members = [r[0] for r in results]
        assert any(m in ["natalie", "tom", "priya", "mike", "nina"] for m in members[:3])

    def test_code_review_routes_to_leadership(self):
        results = get_relevant_personas("review the code quality standards")
        members = [r[0] for r in results]
        assert "kai" in members[:3]

    def test_respects_n_limit(self):
        results = get_relevant_personas("fix the data pipeline and update reports", n=3)
        assert len(results) <= 3

    def test_no_match_returns_empty(self):
        results = get_relevant_personas("hello world how are you today")
        # Should return empty or very low-scoring results
        assert len(results) == 0 or results[0][2] == 0

    def test_lead_gets_bonus(self):
        """Track lead should score higher than non-lead for same keywords."""
        results = get_relevant_personas("fix the data pipeline")
        members_scores = {r[0]: r[2] for r in results}
        # Elena (lead) should have higher score than James (non-lead) for same track
        if "elena" in members_scores and "james" in members_scores:
            assert members_scores["elena"] > members_scores["james"]

    def test_returns_tuples_with_track(self):
        results = get_relevant_personas("update dbt model", n=1)
        if results:
            member, track, score = results[0]
            assert isinstance(member, str)
            assert isinstance(track, str)
            assert isinstance(score, float)

    def test_no_duplicates(self):
        results = get_relevant_personas("fix pipeline and etl and data quality and schema")
        members = [r[0] for r in results]
        assert len(members) == len(set(members))


# ---------------------------------------------------------------------------
# Suggested persona
# ---------------------------------------------------------------------------


class TestGetSuggestedPersona:
    def test_suggests_for_dbt_task(self):
        result = get_suggested_persona("fix the dbt staging model")
        assert result is not None
        member, track, reason = result
        assert member == "sofia"
        assert "Analytics" in track
        assert "dbt" in reason

    def test_suggests_for_pipeline_task(self):
        result = get_suggested_persona("fix the etl pipeline")
        assert result is not None
        member, track, reason = result
        assert member == "elena"

    def test_no_suggestion_for_generic(self):
        result = get_suggested_persona("hello there")
        assert result is None

    def test_returns_reason_with_keywords(self):
        result = get_suggested_persona("update the dashboard metrics")
        assert result is not None
        _, _, reason = result
        assert "dashboard" in reason or "metrics" in reason
