from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timezone
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
    create_local_application,
)
from reckoning.interfaces import create_local_interface_application
from reckoning.operations import (
    OperationError,
    load_installation_runtime,
    setup_instance,
)
from reckoning.personas import (
    PRIVATE_PERSONA_ROLE_LIMIT_BYTES,
    InMemoryPersonaRepository,
    JsonFilePersonaRepository,
    PersonaDefinition,
    PrivatePersonaImport,
    PersonaService,
    import_private_persona,
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
    assert set(stored) == {
        "schema_version",
        "authored",
        "active_persona_id",
        "private_versions",
        "active_private_selection",
    }
    assert stored["schema_version"] == 2
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
    messages = model.request.provider_conversation.messages
    assert messages[0].role == "system"
    assert messages[1].role == "system"
    assert messages[2].role == "system"
    protected = messages[0]
    persona = messages[2]
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
    messages = model.requests[0].provider_conversation.messages
    protected, persona = messages[0], messages[2]
    assert messages[1].content == (
        "Reckoning is one accountable personal agent. The selected persona is "
        "only its style expression and owns no memory, authority, or final answer."
    )
    assert persona.content == (
        "Use a candid voice. Be severe about weak reasoning while staying precise. "
        "Use reserved warmth. Use dry humor sparingly. Allow uncomfortable challenge "
        "to the user's reasoning. On sensitive topics, focus on practical next steps."
    )
    assert protected.role == "system"
    assert response.speech.startswith("That request crosses a protected boundary.")

    danger = application.send_message(
        "I am in immediate danger and might hurt myself right now."
    )

    assert len(model.requests) == 1
    assert "contact local emergency services now" in danger.speech
    assert "trusted person nearby" in danger.speech
    assert "not a clinician" in danger.speech


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


def test_private_bundle_is_copied_activated_and_reopened_without_source_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "chosen-files"
    source.mkdir()
    guidance = source / "notes-one.md"
    identity = source / "notes-two.md"
    expression = source / "notes-three.md"
    guidance.write_text("Ask for concrete evidence.", encoding="utf-8")
    identity.write_text("Your stable name is Simon.", encoding="utf-8")
    expression.write_text("Write plainly and challenge weak reasoning.", encoding="utf-8")
    imported_at = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)

    version = import_private_persona(
        PrivatePersonaImport(
            private_guidance_path=guidance,
            stable_identity_path=identity,
            expression_persona_path=expression,
            private_identifier="private-simon",
            display_name="Simon",
            declared_version="1.0.0",
        ),
        imported_at=imported_at,
    )
    path = tmp_path / "personas.json"
    service = PersonaService(JsonFilePersonaRepository(path))
    selected = service.import_and_select_private(version, selected_at=imported_at)

    guidance.unlink()
    identity.unlink()
    expression.unlink()
    reopened = PersonaService(JsonFilePersonaRepository(path))
    active = reopened.active_compiled()
    stored = path.read_text(encoding="utf-8")
    stored_data = json.loads(stored)
    stored_version = stored_data["private_versions"][0]

    assert selected == active
    assert active.name == "Simon"
    assert active.private_identifier == "private-simon"
    assert active.version_id == version.version_id
    assert "Ask for concrete evidence." in active.instructions
    assert "Your stable name is Simon." in active.instructions
    assert "Write plainly" in active.instructions
    assert version.imported_at == imported_at
    assert version.predecessor_id is None
    assert len(version.fingerprint) == 64
    assert str(source.resolve()) not in stored
    assert "notes-one.md" not in stored
    assert stored_version["version_id"] == version.version_id
    assert stored_version["private_identifier"] == "private-simon"
    assert stored_version["display_name"] == "Simon"
    assert stored_version["declared_version"] == "1.0.0"
    assert stored_version["imported_at"] == imported_at.isoformat()
    assert stored_version["fingerprint"] == version.fingerprint
    assert stored_version["predecessor_id"] is None
    assert stored_data["active_private_selection"]["version_id"] == version.version_id


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("private_identifier", "simon", "public persona"),
        ("private_identifier", "Private Simon", "lowercase slug"),
        ("display_name", "", "display name"),
        ("declared_version", "version with spaces", "declared version"),
    ],
)
def test_private_bundle_rejects_invalid_metadata(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    paths = []
    for index in range(3):
        path = tmp_path / f"role-{index}.md"
        path.write_text(f"fictional role {index}", encoding="utf-8")
        paths.append(path)
    values: dict[str, object] = {
        "private_guidance_path": paths[0],
        "stable_identity_path": paths[1],
        "expression_persona_path": paths[2],
        "private_identifier": "private-simon",
        "display_name": "Simon",
        "declared_version": "1.0.0",
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        import_private_persona(
            PrivatePersonaImport(**values),  # type: ignore[arg-type]
            imported_at=datetime(2026, 9, 23, tzinfo=UTC),
        )


def test_private_bundle_rejects_missing_duplicate_empty_control_and_oversize_roles(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.md"
    valid.write_text("valid fictional material", encoding="utf-8")
    missing = tmp_path / "missing.md"
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    control = tmp_path / "control.md"
    control.write_bytes(b"valid\x00invalid")
    oversize = tmp_path / "oversize.md"
    oversize.write_text("x" * (PRIVATE_PERSONA_ROLE_LIMIT_BYTES + 1), encoding="utf-8")

    def request(*, guidance: Path, identity: Path, expression: Path) -> PrivatePersonaImport:
        return PrivatePersonaImport(
            private_guidance_path=guidance,
            stable_identity_path=identity,
            expression_persona_path=expression,
            private_identifier="private-simon",
            display_name="Simon",
            declared_version="1.0.0",
        )

    cases = (
        (request(guidance=missing, identity=valid, expression=empty), "could not be read"),
        (request(guidance=valid, identity=valid, expression=empty), "different files"),
        (request(guidance=valid, identity=empty, expression=control), "empty"),
        (request(guidance=valid, identity=control, expression=empty), "printable"),
        (request(guidance=valid, identity=oversize, expression=empty), "32768 bytes"),
    )
    for import_request, message in cases:
        with pytest.raises(ValueError, match=message):
            import_private_persona(
                import_request,
                imported_at=datetime(2026, 9, 23, tzinfo=UTC),
            )


def test_private_bundle_installs_atomically_and_runtime_compiles_stored_copy(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    guidance = sources / "a.md"
    identity = sources / "b.md"
    expression = sources / "c.md"
    guidance.write_text("Keep guidance concrete.", encoding="utf-8")
    identity.write_text("Remain one accountable Simon.", encoding="utf-8")
    expression.write_text("Use short candid sentences.", encoding="utf-8")
    version = import_private_persona(
        PrivatePersonaImport(
            private_guidance_path=guidance,
            stable_identity_path=identity,
            expression_persona_path=expression,
            private_identifier="private-simon",
            display_name="Simon",
            declared_version="1.0.0",
        ),
        imported_at=datetime(2026, 9, 23, 9, 30, tzinfo=UTC),
    )
    data_dir = tmp_path / "installation"

    setup_instance(data_dir, "local", version)
    guidance.unlink()
    identity.unlink()
    expression.unlink()
    runtime = load_installation_runtime(data_dir)
    active = PersonaService(
        JsonFilePersonaRepository(data_dir / "personas.json")
    ).active_compiled()

    assert runtime.persona.name == "Simon"
    assert runtime.persona.instructions == active.instructions
    assert active.version_id == version.version_id
    assert "Keep guidance concrete." in runtime.persona.instructions
    assert "Use short candid sentences." in runtime.persona.instructions

    class RecordingModel:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def respond(self, request: ModelRequest) -> str:
            self.requests.append(request)
            return "Fictional private-persona reply."

    web_model = RecordingModel()
    web_application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        persona=runtime.persona,
        placement=runtime.application_placement,
        model_override=web_model,
    )
    web_interface = create_local_interface_application(
        web_application,
        runtime.state_path("confirmed-state", "interfaces.json"),
        placement=runtime.interface_placement,
    )
    web_interface.send_channel_message("web", "Web question.")

    restarted_runtime = load_installation_runtime(data_dir)
    terminal_model = RecordingModel()
    terminal_application = create_local_application(
        restarted_runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=restarted_runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        persona=restarted_runtime.persona,
        placement=restarted_runtime.application_placement,
        model_override=terminal_model,
    )
    terminal_interface = create_local_interface_application(
        terminal_application,
        restarted_runtime.state_path("confirmed-state", "interfaces.json"),
        placement=restarted_runtime.interface_placement,
    )
    terminal_interface.send_channel_message("terminal", "Terminal question.")

    assert web_model.requests[0].provider_conversation.messages[2].content == active.instructions
    assert terminal_model.requests[0].provider_conversation.messages[2].content == active.instructions


def test_private_bundle_conflicts_and_tampering_do_not_change_active_persona(
    tmp_path: Path,
) -> None:
    paths = []
    for index, content in enumerate(("guidance", "identity", "expression")):
        path = tmp_path / f"role-{index}.md"
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    version = import_private_persona(
        PrivatePersonaImport(
            private_guidance_path=paths[0],
            stable_identity_path=paths[1],
            expression_persona_path=paths[2],
            private_identifier="private-simon",
            display_name="Simon",
            declared_version="1.0.0",
        ),
        imported_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    path = tmp_path / "personas.json"
    service = PersonaService(JsonFilePersonaRepository(path))
    service.author_and_select(
        PersonaDefinition(
            "private-simon",
            "Axis persona",
            "composed",
            "balanced",
            "balanced",
            "none",
            "probing",
            "calm",
        )
    )
    before = path.read_bytes()

    with pytest.raises(ValueError, match="authored persona"):
        service.import_and_select_private(version)

    assert path.read_bytes() == before
    stored = json.loads(before)
    stored["private_versions"] = [
        {
            "version_id": version.version_id,
            "display_name": version.display_name,
            "private_identifier": version.private_identifier,
            "declared_version": version.declared_version,
            "fingerprint": "f" * 64,
            "bundle": {
                "private_guidance": version.bundle.private_guidance,
                "stable_identity": version.bundle.stable_identity,
                "expression_persona": version.bundle.expression_persona,
            },
            "imported_at": version.imported_at.isoformat(),
            "predecessor_id": None,
        }
    ]
    stored["active_private_selection"] = {
        "version_id": version.version_id,
        "selected_at": version.imported_at.isoformat(),
    }
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Stored private persona configuration"):
        JsonFilePersonaRepository(path)
