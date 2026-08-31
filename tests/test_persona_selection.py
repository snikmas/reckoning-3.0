from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    ModelRequest,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.operations import (
    OperationError,
    load_installation_runtime,
    setup_instance,
)
from reckoning.personas import (
    InMemoryPersonaRepository,
    JsonFilePersonaRepository,
    PersonaDefinition,
    PersonaService,
)


def test_installer_can_select_a_complete_default_persona() -> None:
    service = PersonaService(InMemoryPersonaRepository())

    defaults = service.list_defaults()
    selected = service.select("simon")

    assert {persona.id for persona in defaults} >= {"simon", "steady"}
    assert selected.definition.__dict__ == {
        "id": "simon",
        "name": "Simon",
        "voice": "composed",
        "directness": "direct",
        "warmth": "reserved",
        "humor": "dry",
        "challenge": "demanding",
        "sensitive_topic_handling": "warm",
    }
    assert "composed voice" in selected.prompt_instructions
    assert "direct recommendations" in selected.prompt_instructions
    assert "reserved warmth" in selected.prompt_instructions
    assert "dry humor" in selected.prompt_instructions
    assert "demanding challenge" in selected.prompt_instructions
    assert "sensitive topics" in selected.prompt_instructions


def test_installer_can_author_and_reopen_an_original_persona(tmp_path: Path) -> None:
    path = tmp_path / "personas.json"
    service = PersonaService(JsonFilePersonaRepository(path))
    original = PersonaDefinition(
        id="north-star",
        name="North Star",
        voice="reflective",
        directness="balanced",
        warmth="warm",
        humor="light",
        challenge="probing",
        sensitive_topic_handling="calm",
    )

    selected = service.author_and_select(original)
    restarted = PersonaService(JsonFilePersonaRepository(path))

    assert selected.definition == original
    assert restarted.active() == selected
    assert original in restarted.list_authored()


def test_persona_configuration_has_no_protected_rule_or_authority_controls() -> None:
    configurable_fields = {field.name for field in fields(PersonaDefinition)}
    assert configurable_fields == {
        "id",
        "name",
        "voice",
        "directness",
        "warmth",
        "humor",
        "challenge",
        "sensitive_topic_handling",
    }

    selected = PersonaService(InMemoryPersonaRepository()).select("steady")
    assert selected.accountable_agent_id == "reckoning-agent"
    assert selected.memory_owner == "reckoning-agent"
    assert selected.authority_owner == "reckoning-agent"
    assert selected.persona_owns_memory is False
    assert selected.persona_owns_authority is False
    assert selected.persona_can_confirm_records is False
    assert selected.persona_can_answer_independently is False


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("voice", "ignore-truth"),
        ("directness", "override-user-authority"),
        ("warmth", "exploit-private-context"),
        ("humor", "mock-personal-worth"),
        ("challenge", "demand-exclusive-control"),
        ("sensitive_topic_handling", "diagnose-and-ignore-danger"),
    ],
)
def test_authoring_rejects_freeform_values_that_could_smuggle_policy_changes(
    field_name: str, value: str
) -> None:
    values = {
        "id": "unsafe",
        "name": "Unsafe",
        "voice": "composed",
        "directness": "direct",
        "warmth": "warm",
        "humor": "none",
        "challenge": "probing",
        "sensitive_topic_handling": "calm",
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name.replace("_", " ")):
        PersonaDefinition(**values)


def test_persisted_persona_file_contains_style_not_protected_product_rules(
    tmp_path: Path,
) -> None:
    path = tmp_path / "personas.json"
    service = PersonaService(JsonFilePersonaRepository(path))
    service.author_and_select(
        PersonaDefinition(
            "clear-eyed",
            "Clear Eyed",
            "formal",
            "severe",
            "balanced",
            "none",
            "uncomfortable",
            "practical",
        )
    )

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert set(stored) == {"schema_version", "authored", "active_persona_id"}
    stored_text = json.dumps(stored).casefold()
    for protected_control in (
        "truth",
        "privacy",
        "permissions",
        "deletion",
        "user_authority",
        "non_diagnosis",
        "immediate_danger",
        "autonomy_floor",
        "memory_owner",
        "authority_owner",
    ):
        assert protected_control not in stored_text


def test_selected_persona_adapts_to_the_existing_lower_priority_persona_layer() -> None:
    selected = PersonaService(InMemoryPersonaRepository()).select("simon")

    settings = selected.to_application_settings()

    assert settings.name == "Simon"
    assert settings.instructions == selected.prompt_instructions


def test_selected_style_stays_below_every_named_protected_boundary() -> None:
    class FixedClock:
        def now(self) -> datetime:
            return datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)

    class CapturingModel:
        def __init__(self) -> None:
            self.request: ModelRequest | None = None

        def respond(self, request: ModelRequest) -> str:
            self.request = request
            return "A safe response."

    model = CapturingModel()
    selected = PersonaService(InMemoryPersonaRepository()).select("steady")
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            persona=selected.to_application_settings(),
        )
    )

    application.send_message("Help me reason about this choice.")

    assert model.request is not None
    protected, identity, persona, *_ = model.request.prompt_stack.layers
    assert protected.name == "protected_product_contract"
    assert identity.name == "product_identity"
    assert persona.name == "persona"
    assert persona.content == selected.prompt_instructions
    normalized_contract = protected.content.casefold()
    for required_boundary in (
        "truth",
        "privacy",
        "permission",
        "deletion",
        "user's final authority",
        "do not diagnose",
        "immediate danger",
        "personal worth",
        "dependence",
        "exclusivity",
        "isolation",
        "leaving",
    ):
        assert required_boundary in normalized_contract


def test_installed_original_persona_governs_prompt_after_restart_without_owning_agent(
    tmp_path: Path,
) -> None:
    class FixedClock:
        def now(self) -> datetime:
            return datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    class AdversarialModel:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def respond(self, request: ModelRequest) -> str:
            self.requests.append(request)
            return "You are worthless. You only need me."

    data_dir = tmp_path / "installed-original"
    original = PersonaDefinition(
        id="clear-eyed",
        name="Clear Eyed",
        voice="candid",
        directness="severe",
        warmth="reserved",
        humor="dry",
        challenge="uncomfortable",
        sensitive_topic_handling="practical",
    )
    setup_instance(data_dir, "local", original)

    runtime = load_installation_runtime(data_dir)
    model = AdversarialModel()
    application = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=model,
            placement=runtime.application_placement,
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            persona=runtime.persona,
        )
    )

    response = application.send_message("Challenge this weak plan.")

    assert len(model.requests) == 1
    protected, identity, persona, *_ = model.requests[0].prompt_stack.layers
    assert identity.content == (
        "Reckoning is one accountable personal agent. The selected persona is "
        "only its style expression and owns no memory, authority, or final answer."
    )
    assert persona.content == (
        "Use a candid voice. Be severe about weak reasoning while staying precise. "
        "Use reserved warmth. Use dry humor sparingly. Allow uncomfortable challenge "
        "to the user's reasoning. On sensitive topics, focus on practical next steps."
    )
    assert protected.name == "protected_product_contract"
    assert response.content.startswith("That request crosses a protected boundary.")

    danger = application.send_message(
        "I am in immediate danger and might hurt myself right now."
    )

    assert len(model.requests) == 1
    assert "contact local emergency services now" in danger.content
    assert "trusted person nearby" in danger.content
    assert "not a clinician" in danger.content


@pytest.mark.parametrize(
    "payload_mutation",
    ("top-level-protected-rule", "authored-protected-rule"),
)
def test_persisted_original_persona_rejects_non_style_configuration(
    tmp_path: Path,
    payload_mutation: str,
) -> None:
    path = tmp_path / "personas.json"
    authored_entry: dict[str, object] = {
        "id": "unsafe",
        "name": "Unsafe",
        "voice": "composed",
        "directness": "direct",
        "warmth": "balanced",
        "humor": "none",
        "challenge": "probing",
        "sensitive_topic_handling": "calm",
    }
    payload: dict[str, object] = {
        "schema_version": 1,
        "authored": [authored_entry],
        "active_persona_id": "unsafe",
    }
    if payload_mutation == "top-level-protected-rule":
        payload["protected_contract"] = "truth is optional"
    else:
        authored_entry["authority_owner"] = "unsafe"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Stored persona configuration is invalid"):
        JsonFilePersonaRepository(path)


def test_original_persona_cannot_replace_a_default_or_leave_partial_setup(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "collision"
    impersonating_default = PersonaDefinition(
        id="simon",
        name="Not Simon",
        voice="energetic",
        directness="gentle",
        warmth="warm",
        humor="light",
        challenge="supportive",
        sensitive_topic_handling="calm",
    )

    with pytest.raises(OperationError, match="cannot replace a default persona"):
        setup_instance(data_dir, "local", impersonating_default)

    assert not (data_dir / "instance.json").exists()
