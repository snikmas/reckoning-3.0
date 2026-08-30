from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reckoning.research_forecasts import (
    ForecastEvidence,
    ForecastService,
    InMemoryResearchForecastRepository,
    OfficialRequirementCheck,
)


def forecast_arguments(instant: datetime) -> dict[str, object]:
    return {
        "forecast_id": "forecast-1",
        "goal_id": "goal-ielts",
        "created_at": instant,
        "target": "Reach IELTS 7.0 before graduation.",
        "baseline": "Approved mock assessment: IELTS 5.5.",
        "effort_range_hours": (240.0, 360.0),
        "money_range": (0.0, 3000.0),
        "resources": ("Official practice tests", "Weekly speaking feedback"),
        "target_start": instant + timedelta(days=7),
        "completion_range": (
            instant + timedelta(days=240),
            instant + timedelta(days=360),
        ),
        "confidence": "Medium because the baseline is current but weekly pace is untested.",
        "uncertainty": ("Actual speaking improvement rate is unknown.",),
        "evidence": (
            ForecastEvidence(
                "evidence-1",
                "approved_assessment",
                "Mock score 5.5",
                "Approved IELTS mock assessment",
                instant,
            ),
            ForecastEvidence(
                "evidence-2",
                "available_time",
                "Six study hours are available each week.",
                "Direct user statement",
                instant,
            ),
            ForecastEvidence(
                "evidence-3",
                "confirmed_obligation",
                "University classes remain fixed.",
                "Confirmed calendar",
                instant,
            ),
        ),
        "assumptions": ("Six study hours remain available most weeks.",),
        "competing_goal_ids": ("goal-backend",),
        "replanning_triggers": (
            "Available time changes by more than two hours per week.",
            "A new official score requirement appears.",
            "Observed pace falls outside the completion range.",
        ),
        "requirement_checks": (
            OfficialRequirementCheck(
                "source-official-1",
                "Named university admissions office",
                "https://example.invalid/official-program",
                "The named program currently requires IELTS 7.0.",
                instant,
            ),
        ),
    }


def test_forecast_records_evidence_ranges_uncertainty_and_official_checks() -> None:
    instant = datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)
    service = ForecastService(InMemoryResearchForecastRepository())

    forecast = service.create_confirmed(**forecast_arguments(instant))

    assert forecast.version == 1
    assert forecast.effort_range_hours == (240.0, 360.0)
    assert forecast.money_range == (0.0, 3000.0)
    assert forecast.resources
    assert forecast.target_start == instant + timedelta(days=7)
    assert forecast.completion_range[0] < forecast.completion_range[1]
    assert forecast.confidence.startswith("Medium")
    assert forecast.uncertainty
    assert forecast.assumptions
    assert forecast.competing_goal_ids == ("goal-backend",)
    assert forecast.replanning_triggers
    assert forecast.requirement_checks[0].owner == "Named university admissions office"
    assert forecast.requirement_checks[0].checked_at == instant
    assert service.current("goal-ielts") == forecast


def test_personality_label_cannot_act_as_capacity_evidence() -> None:
    instant = datetime(2026, 8, 30, 23, 0, tzinfo=timezone.utc)
    service = ForecastService(InMemoryResearchForecastRepository())
    arguments = forecast_arguments(instant)
    arguments["evidence"] = (
        ForecastEvidence(
            "bad-evidence",
            "personality_label",
            "Mary is disciplined.",
            "Model inference",
            instant,
        ),
    )

    with pytest.raises(ValueError, match="personality label"):
        service.create_confirmed(**arguments)
