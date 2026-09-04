from __future__ import annotations

import json
from pathlib import Path

import pytest

from reckoning.personas import (
    AUTONOMY_FLOOR,
    PERSONA_AXES,
    InMemoryPersonaRepository,
    JsonFilePersonaRepository,
    PersonaService,
    blank_persona_template,
    describe_persona,
    persona_from_preset,
)


def authored(service: PersonaService, persona_id: str = "north-star") -> None:
    service.author_and_select(blank_persona_template(persona_id, "North Star"))


def test_presets_are_immutable_but_duplicable(tmp_path: Path) -> None:
    service = PersonaService(JsonFilePersonaRepository(tmp_path / "personas.json"))

    with pytest.raises(ValueError, match="immutable"):
        service.update_authored(persona_from_preset("simon", "simon", "Simon"))
    with pytest.raises(ValueError, match="immutable"):
        service.rename_authored("simon", "New Simon")
    with pytest.raises(ValueError, match="cannot be removed"):
        service.remove_authored("simon")

    copy = service.duplicate("simon", "simon-two", "Simon Two")
    assert copy.id == "simon-two"
    assert copy.directness == "direct"
    assert copy.humor == "dry"

    reopened = PersonaService(JsonFilePersonaRepository(tmp_path / "personas.json"))
    assert any(item.id == "simon-two" for item in reopened.list_authored())


def test_authored_personas_can_be_edited_renamed_and_removed(tmp_path: Path) -> None:
    path = tmp_path / "personas.json"
    service = PersonaService(JsonFilePersonaRepository(path))
    authored(service)
    service.select("simon")

    edited = persona_from_preset("steady", "north-star", "North Star")
    service.update_authored(edited)
    renamed = service.rename_authored("north-star", "True North")
    assert renamed.name == "True North"

    service.remove_authored("north-star")
    assert all(item.id != "north-star" for item in service.list_authored())

    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["authored"] == []


def test_the_active_persona_must_be_replaced_before_removal(tmp_path: Path) -> None:
    service = PersonaService(JsonFilePersonaRepository(tmp_path / "personas.json"))
    authored(service)

    with pytest.raises(ValueError, match="active persona must be replaced"):
        service.remove_authored("north-star")

    service.select("steady")
    service.remove_authored("north-star")


def test_a_duplicate_cannot_clobber_existing_ids() -> None:
    service = PersonaService(InMemoryPersonaRepository())
    service.duplicate("steady", "steady-copy", "Steady Copy")

    with pytest.raises(ValueError, match="already exists"):
        service.duplicate("simon", "steady-copy", "Another")
    with pytest.raises(ValueError, match="default persona"):
        service.duplicate("simon", "steady", "Steady Again")


def test_the_blank_template_starts_from_balanced_defaults() -> None:
    template = blank_persona_template("clear-eyed", "Clear Eyed")
    assert template.directness == "balanced"
    assert template.warmth == "balanced"
    assert template.challenge == "probing"


def test_persona_preview_is_deterministic_and_plain_language() -> None:
    simon = persona_from_preset("simon", "simon-copy", "Simon Copy")
    first = describe_persona(simon)
    second = describe_persona(simon)

    assert first == second
    rendered = "\n".join(first)
    assert "Simon Copy" in rendered
    for axis in PERSONA_AXES:
        value = getattr(simon, axis.field)
        choice = next(item for item in axis.choices if item.value == value)
        assert choice.explanation in rendered


def test_every_axis_offers_plain_language_choices_with_examples() -> None:
    assert [axis.field for axis in PERSONA_AXES] == [
        "voice",
        "directness",
        "warmth",
        "humor",
        "challenge",
        "sensitive_topic_handling",
    ]
    for axis in PERSONA_AXES:
        assert len(axis.choices) >= 2
        for choice in axis.choices:
            assert choice.explanation and choice.example


def test_the_autonomy_floor_is_listed_separately_from_persona_axes() -> None:
    rendered = "\n".join(AUTONOMY_FLOOR)
    assert "Truthfulness" in rendered
    assert "final decision" in rendered
    axis_fields = {axis.field for axis in PERSONA_AXES}
    assert "autonomy" not in axis_fields
