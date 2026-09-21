from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reckoning.research_forecasts import (
    BoundedResearchService,
    InMemoryResearchForecastRepository,
    ResearchRequest,
    ResearchSource,
)


def test_bounded_research_uses_current_primary_source_without_ongoing_authority(
) -> None:
    instant = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    repository = InMemoryResearchForecastRepository()
    service = BoundedResearchService(repository)
    request = ResearchRequest(
        "research-1",
        "What is the current IELTS score requirement?",
        "IELTS requirement for one named program",
        instant,
        10,
        True,
    )
    secondary = ResearchSource(
        "source-2",
        "Forum discussion",
        "https://example.invalid/forum",
        "Independent forum",
        "secondary",
        "Unverified account",
        instant,
        instant - timedelta(days=10),
        request.scope,
        "public",
        1,
    )
    primary = ResearchSource(
        "source-1",
        "Official admissions requirement",
        "https://example.invalid/official",
        "Named university",
        "primary",
        "Official program page",
        instant,
        instant - timedelta(days=1),
        request.scope,
        "public",
        2,
    )

    result = service.complete(
        request,
        conclusion="The named program currently requires the score stated by its official page.",
        sources=(secondary, primary),
        completed_at=instant,
    )

    assert result.sources[0] == primary
    assert result.sources[1].owner == "Independent forum"
    assert result.total_cost_units == 3
    assert result.created_watch is None
    assert result.created_routine is None
    assert result.granted_broader_permission is False
    assert service.inspect(request.id) == result
    checks = service.official_requirement_checks(
        request.id, "The named program currently requires IELTS 7.0."
    )
    assert checks[0].source_id == primary.id
    assert checks[0].checked_at == instant


def test_current_decision_rejects_research_without_current_primary_source() -> None:
    instant = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    service = BoundedResearchService(InMemoryResearchForecastRepository())
    request = ResearchRequest("research-1", "Question", "one scope", instant, 10, True)
    secondary = ResearchSource(
        "source-1",
        "Old article",
        "https://example.invalid/article",
        "Publisher",
        "secondary",
        "Editorial article",
        instant,
        instant,
        request.scope,
        "public",
        1,
    )

    with pytest.raises(ValueError, match="current primary source"):
        service.complete(
            request,
            conclusion="Unsupported conclusion",
            sources=(secondary,),
            completed_at=instant,
        )


def test_bounded_research_rejects_negative_source_cost() -> None:
    instant = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    repository = InMemoryResearchForecastRepository()
    service = BoundedResearchService(repository)
    request = ResearchRequest("research-1", "Question", "one scope", instant, 3, False)
    positive = ResearchSource(
        "source-1",
        "Paid source",
        "https://example.invalid/paid",
        "Publisher",
        "secondary",
        "Editorial article",
        instant,
        instant,
        request.scope,
        "public",
        5,
    )
    negative = ResearchSource(
        "source-2",
        "Invalid rebate",
        "https://example.invalid/rebate",
        "Publisher",
        "secondary",
        "Invalid cost report",
        instant,
        instant,
        request.scope,
        "public",
        -2,
    )

    with pytest.raises(ValueError, match="source cost cannot be negative"):
        service.complete(
            request,
            conclusion="The negative source must not offset the paid source.",
            sources=(positive, negative),
            completed_at=instant,
        )

    with pytest.raises(KeyError, match="Unknown research request"):
        service.inspect(request.id)


def test_bounded_research_accepts_zero_cost_at_zero_budget() -> None:
    instant = datetime(2026, 8, 30, 22, 0, tzinfo=timezone.utc)
    service = BoundedResearchService(InMemoryResearchForecastRepository())
    request = ResearchRequest("research-1", "Question", "one scope", instant, 0, False)
    source = ResearchSource(
        "source-1",
        "Free source",
        "https://example.invalid/free",
        "Publisher",
        "primary",
        "Official page",
        instant,
        instant,
        request.scope,
        "public",
        0,
    )

    result = service.complete(
        request,
        conclusion="A free source stays within a zero budget.",
        sources=(source,),
        completed_at=instant,
    )

    assert result.total_cost_units == 0
    assert service.inspect(request.id) == result
