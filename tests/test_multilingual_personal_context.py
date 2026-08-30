from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from reckoning.personal_context import (
    JsonFilePersonalContextRepository,
    PersonalContextService,
    RetrievalQuery,
)


def test_russian_correction_supersedes_english_fact_and_only_current_meaning_retrieves(
    tmp_path: Path,
) -> None:
    instant = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)
    path = tmp_path / "context.json"
    service = PersonalContextService(JsonFilePersonalContextRepository(path))
    service.remember(
        record_id="fact-1",
        original_text="My Chinese class is on Tuesday.",
        language="en",
        canonical_meaning="Mary's Chinese class is on Tuesday.",
        source="direct user statement",
        created_at=instant,
        fresh_until=instant + timedelta(days=30),
    )
    corrected = service.correct(
        "fact-1",
        original_text="Теперь занятие по китайскому в среду.",
        language="ru",
        canonical_meaning="Mary's Chinese class is on Wednesday.",
        corrected_at=instant + timedelta(days=1),
    )

    restarted = PersonalContextService(JsonFilePersonalContextRepository(path))
    retrieved = restarted.retrieve(
        RetrievalQuery(
            "When is my Chinese class?",
            instant + timedelta(days=2),
            ("direct user correction",),
            ("low",),
            "local",
        )
    )
    why = restarted.inspect("fact-1")

    assert retrieved == (corrected,)
    assert retrieved[0].canonical_meaning.endswith("Wednesday.")
    assert why.current.version == 2
    assert why.current.original_text.startswith("Теперь")
    assert why.superseded[0].original_text.startswith("My Chinese class")
    assert why.superseded[0].canonical_meaning.endswith("Tuesday.")


def test_retrieval_applies_permission_freshness_placement_source_and_sensitivity_filters(
    tmp_path: Path,
) -> None:
    instant = datetime(2026, 8, 30, 18, 0, tzinfo=timezone.utc)
    service = PersonalContextService(
        JsonFilePersonalContextRepository(tmp_path / "context.json")
    )
    for record_id, overrides in (
        ("allowed", {}),
        ("no-permission", {"retrieval_permitted": False}),
        ("stale", {"fresh_until": instant - timedelta(seconds=1)}),
        ("remote", {"processing_location": "personal-server"}),
        ("restricted", {"sensitivity": "restricted"}),
        ("wrong-source", {"source": "imported document"}),
    ):
        service.remember(
            record_id=record_id,
            original_text=f"Chinese class detail {record_id}",
            language="en",
            canonical_meaning=f"Chinese class detail {record_id}",
            source=overrides.get("source", "direct user statement"),
            created_at=instant,
            fresh_until=overrides.get("fresh_until", instant + timedelta(days=1)),
            retrieval_permitted=overrides.get("retrieval_permitted", True),
            processing_location=overrides.get("processing_location", "local"),
            sensitivity=overrides.get("sensitivity", "low"),
        )

    retrieved = service.retrieve(
        RetrievalQuery(
            "Chinese class detail",
            instant,
            ("direct user statement",),
            ("low",),
            "local",
        )
    )

    assert [item.record_id for item in retrieved] == ["allowed"]
