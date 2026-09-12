"""Acceptance tests for the setup workflow at the user-visible seam.

A scripted SetupUI drives the workflow against isolated installation
directories; assertions cover observable output, exit status, and durable
results — not private helpers.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from reckoning.config import ProviderCredentialStore
from reckoning.operations import OperationError, setup_instance
from reckoning.personal_context import JsonFilePersonalContextRepository
from reckoning.provider_adapters import AdapterConfig, urlopen_transport
from reckoning.setup_workflow import (
    MenuOption,
    SetupDraft,
    SetupInputError,
    SetupOutcome,
    SetupPaths,
    SetupServices,
    SetupWorkflow,
)
from reckoning.telegram import TelegramConnectorConfig


class ScriptedUI:
    """Plays back keyed answers and records every rendered line."""

    def __init__(self, answers: list[tuple[str, str]]) -> None:
        self._answers = list(answers)
        self.lines: list[str] = []

    def _next(self, kind: str, key: str) -> str:
        if not self._answers:
            raise AssertionError(f"no scripted answer left for {kind} '{key}'")
        answer_key, value = self._answers.pop(0)
        assert answer_key == key, (
            f"expected answer for '{answer_key}', workflow asked '{key}'"
        )
        return value

    def banner(self) -> None:
        self.lines.append("== RECKONING setup ==")

    def step(self, index: int, total: int, title: str) -> None:
        self.lines.append(f"Step {index}/{total} — {title}")

    def info(self, text: str) -> None:
        self.lines.append(text)

    def secondary(self, text: str) -> None:
        self.lines.append(text)

    def success(self, text: str) -> None:
        self.lines.append(f"OK: {text}")

    def warning(self, text: str) -> None:
        self.lines.append(f"Warning: {text}")

    def failure(self, text: str) -> None:
        self.lines.append(f"Failed: {text}")

    def choose(
        self,
        key: str,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool = False,
        help_text: str | None = None,
    ) -> str:
        self.lines.append(prompt)
        for option in options:
            marker = "" if option.available and not option.dim else " (dim)"
            self.lines.append(f"  option: {option.id} {option.label}{marker}")
        value = self._next("choose", key)
        if value == "__back__":
            from reckoning.setup_workflow import SetupBack

            raise SetupBack
        if value == "__exit__":
            from reckoning.setup_workflow import SetupExit

            raise SetupExit
        return value

    def ask(
        self,
        key: str,
        prompt: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = True,
    ) -> str:
        self.lines.append(("secret: " if secret else "") + prompt)
        value = self._next("ask", key)
        if value == "__back__":
            from reckoning.setup_workflow import SetupBack

            raise SetupBack
        if value == "__exit__":
            from reckoning.setup_workflow import SetupExit

            raise SetupExit
        return value

    def confirm(self, key: str, question: str, *, default: bool = False) -> bool:
        self.lines.append(question)
        value = self._next("confirm", key)
        return value.casefold() in ("y", "yes", "true", "1")


def make_paths(tmp_path: Path) -> SetupPaths:
    return SetupPaths(
        data_dir=tmp_path / "data",
        credentials_path=tmp_path / "provider.json",
        telegram_config_path=tmp_path / "telegram.json",
        draft_path=tmp_path / "setup-draft.json",
    )


def offline_services(**overrides: object) -> SetupServices:
    values: dict[str, object] = {
        "environ": {},
        "probe": lambda url: False,
        "telegram_api_factory": None,
        "transport": urlopen_transport,
    }
    values.update(overrides)
    return SetupServices(**values)  # type: ignore[arg-type]


def run_workflow(
    tmp_path: Path,
    answers: list[tuple[str, str]],
    *,
    services: SetupServices | None = None,
    preselected: dict[str, str] | None = None,
) -> tuple[SetupOutcome, ScriptedUI]:
    ui = ScriptedUI(answers)
    workflow = SetupWorkflow(
        paths=make_paths(tmp_path),
        ui=ui,
        services=services or offline_services(),
        preselected=preselected or {},
    )
    outcome = workflow.run()
    return outcome, ui


GUIDED_FAKE_ANSWERS: list[tuple[str, str]] = [
    ("provider", "fake"),
    ("connectors", "skip"),
    ("persona", "simon"),
    ("persona-accept", "y"),
    ("profile", "skip"),
    ("review-action", "continue"),
    ("first-message", "I need to protect my mornings for study."),
    ("first-message-action", "accept"),
]


def test_guided_setup_uses_five_sections_before_the_first_conversation(
    tmp_path: Path,
) -> None:
    outcome, ui = run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    assert outcome.status == "activated"
    assert outcome.demo is True
    steps = [line for line in ui.lines if line.startswith("Step ")]
    assert steps == [
        "Step 1/5 — AI provider and model",
        "Step 2/5 — Ways to use Reckoning",
        "Step 3/5 — Agent style",
        "Step 4/5 — About you",
        "Step 5/5 — Review",
    ]
    displayed = "\n".join(ui.lines)
    assert "Quick Setup" not in displayed
    assert "Custom Setup" not in displayed
    assert "Continue to your first conversation" in displayed
    assert displayed.index("Step 5/5") < displayed.index("Your first conversation")
    assert "Terminal: Ready. Start with `reckoning`." in displayed
    assert "Web: Ready. Start with `reckoning web`." in displayed
    assert "Telegram: Not configured." in displayed
    assert "Gateway: Stopped. Start with `reckoning gateway`." in displayed


def test_provider_screen_lists_only_supported_choices_and_separates_demo(
    tmp_path: Path,
) -> None:
    outcome, ui = run_workflow(tmp_path, [("provider", "__exit__")])

    assert outcome.status == "draft"
    displayed = "\n".join(ui.lines)
    assert "More providers coming soon" in displayed
    for unavailable in ("OpenAI", "Anthropic", "Gemini", "OpenRouter"):
        assert f"option: {unavailable.casefold()} " not in displayed.casefold()
    assert displayed.index("OrcaRouter") < displayed.index("Demo")


def test_detected_provider_access_is_the_first_choice_without_value_disclosure(
    tmp_path: Path,
) -> None:
    store = ProviderCredentialStore()
    store.set_key("orcarouter", "sk-never-display", verified=True)
    store.save(tmp_path / "provider.json")

    outcome, ui = run_workflow(tmp_path, [("provider", "__exit__")])

    assert outcome.status == "draft"
    displayed = "\n".join(ui.lines)
    choices = [line for line in ui.lines if line.startswith("  option:")]
    assert choices[0].startswith("  option: orcarouter ")
    assert "Detected access" in choices[0]
    assert "sk-never-display" not in displayed
    assert "Test a detected provider now?" not in displayed


def test_about_you_can_create_a_starter_profile_file(tmp_path: Path) -> None:
    profile_path = tmp_path / "about-me.md"
    answers = [
        ("provider", "fake"),
        ("connectors", "skip"),
        ("persona", "simon"),
        ("persona-accept", "y"),
        ("profile", "starter"),
        ("profile-starter-path", str(profile_path)),
        ("review-action", "continue"),
        ("first-message", "Help me choose today's priority."),
        ("first-message-action", "accept"),
    ]

    outcome, ui = run_workflow(tmp_path, answers)

    assert outcome.status == "activated"
    profile = profile_path.read_text(encoding="utf-8")
    assert "# About me" in profile
    assert "## Current work" in profile
    assert "## Boundaries" in profile
    assert "Example:" in "\n".join(ui.lines)
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["profile_bootstrap"]["proposal_count"] == 0


def test_legacy_draft_migration_preserves_choices_and_removes_mode(
    tmp_path: Path,
) -> None:
    draft_path = tmp_path / "setup-draft.json"
    draft_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "incomplete",
                "mode": "quick",
                "completed": ["persona", "provider", "profile"],
                "placement": "local",
                "persona_id": "simon",
                "provider_id": "fake",
                "provider_model": "deterministic-fake",
                "provider_demo": True,
                "profile_choice": "skip",
                "profile_entries": [],
                "next_step": "connectors",
            }
        ),
        encoding="utf-8",
    )

    outcome, ui = run_workflow(
        tmp_path,
        [("resume", "resume"), ("connectors", "__exit__")],
    )

    assert outcome.status == "draft"
    migrated = json.loads(draft_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert "mode" not in migrated
    assert migrated["persona_id"] == "simon"
    assert migrated["provider_id"] == "fake"
    assert "Draft migration preview" in "\n".join(ui.lines)


def test_incompatible_draft_requires_an_exact_removal_confirmation(
    tmp_path: Path,
) -> None:
    draft_path = tmp_path / "setup-draft.json"
    draft_path.write_text(
        json.dumps({"schema_version": 999, "status": "incomplete"}),
        encoding="utf-8",
    )

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("invalid-draft-action", "remove"),
            ("invalid-draft-remove", "n"),
        ],
    )

    assert outcome.status == "draft"
    assert draft_path.exists()
    displayed = "\n".join(ui.lines)
    assert "cannot resume" in displayed
    assert f"Remove exactly: {draft_path}" in displayed
    assert not (tmp_path / "data").exists()


def test_guided_setup_persists_the_accepted_first_conversation(
    tmp_path: Path,
) -> None:
    outcome, ui = run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    assert outcome.status == "activated"
    assert outcome.demo is True
    assert outcome.provider_id == "fake"
    displayed = "\n".join(ui.lines)
    assert "Demo mode" in displayed
    assert "Real provider ready" not in displayed
    assert "Quick Setup" not in displayed
    assert "Custom Setup" not in displayed
    assert not (tmp_path / "setup-draft.json").exists()

    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["status"] == "activated"
    assert instance["activation"]["demo"] is True
    assert instance["placement_profile"] == "local"

    # The accepted exchange survived in the installed state.
    interfaces = json.loads(
        (tmp_path / "data" / "confirmed-state" / "interfaces.json").read_text()
    )
    sessions = interfaces["state"]["sessions"]
    assert len(sessions) == 1
    contents = [(m["role"], m["content"]) for m in sessions[0]["messages"]]
    assert contents[0] == ("user", "I need to protect my mornings for study.")
    assert contents[1][0] == "assistant"


def test_installed_state_survives_a_fresh_application_load(tmp_path: Path) -> None:
    from reckoning.interfaces import JsonFileInterfaceRepository
    from reckoning.operations import load_installation_runtime

    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    runtime = load_installation_runtime(tmp_path / "data")
    state = JsonFileInterfaceRepository(
        runtime.state_path("confirmed-state", "interfaces.json")
    ).load()
    (session,) = state.sessions
    assert session.channel == "web"
    assert session.messages[0].content.startswith("I need to protect")


def test_exit_writes_a_non_secret_draft_and_resume_skips_completed_steps(
    tmp_path: Path,
) -> None:
    outcome, _ = run_workflow(
        tmp_path,
        [("provider", "fake"), ("connectors", "__exit__")],
    )

    assert outcome.status == "draft"
    draft_raw = (tmp_path / "setup-draft.json").read_text(encoding="utf-8")
    draft = json.loads(draft_raw)
    assert draft["status"] == "incomplete"
    assert draft["next_step"] == "connectors"
    assert draft["completed"] == ["provider"]
    assert draft["provider_id"] == "fake"
    assert "secret" not in draft_raw.casefold()
    assert not (tmp_path / "data").exists()
    assert stat.S_IMODE((tmp_path / "setup-draft.json").stat().st_mode) == 0o600

    # Resume: the provider is not asked again; setup finishes from connectors.
    outcome2, ui2 = run_workflow(
        tmp_path,
        [
            ("resume", "resume"),
            ("connectors", "skip"),
            ("persona", "steady"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Hello again."),
            ("first-message-action", "accept"),
        ],
    )
    assert outcome2.status == "activated"
    displayed2 = "\n".join(ui2.lines)
    assert "Step 1/5" not in displayed2
    assert "Completed sections: provider" in displayed2
    assert outcome2.persona_id == "steady"


def test_exit_at_the_provider_step_records_it_as_the_next_step(
    tmp_path: Path,
) -> None:
    outcome, _ = run_workflow(tmp_path, [("provider", "__exit__")])

    assert outcome.status == "draft"
    draft = json.loads((tmp_path / "setup-draft.json").read_text())
    assert draft["status"] == "incomplete"
    assert draft["completed"] == []
    assert draft["next_step"] == "provider"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("next_step", "not-a-step"),
        ("provider_header_env", {"X-Test": "BAD-NAME"}),
    ),
)
def test_setup_draft_rejects_invalid_resume_metadata(
    tmp_path: Path, field: str, value: object
) -> None:
    draft_path = tmp_path / "setup-draft.json"
    draft = {
        "schema_version": 1,
        "status": "incomplete",
        "mode": "quick",
        "completed": [],
        "next_step": "persona",
        field: value,
    }
    draft_path.write_text(json.dumps(draft), encoding="utf-8")

    with pytest.raises(OperationError, match="setup draft"):
        SetupDraft.load(draft_path)


def test_back_changes_no_committed_state(tmp_path: Path) -> None:
    outcome, _ui = run_workflow(
        tmp_path,
        [
            ("provider", "fake"),
            ("connectors", "__back__"),
            ("provider", "fake"),
            ("connectors", "skip"),
            ("persona", "steady"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Testing back navigation."),
            ("first-message-action", "accept"),
        ],
    )

    assert outcome.status == "activated"
    assert outcome.persona_id == "steady"
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["active_persona_id"] == "steady"


def test_a_rejected_first_response_leaves_no_durable_conversation(
    tmp_path: Path,
) -> None:
    outcome, _ = run_workflow(
        tmp_path,
        [
            ("provider", "fake"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "First attempt."),
            ("first-message-action", "retry"),
            ("first-message", "Second attempt."),
            ("first-message-action", "__exit__"),
        ],
    )

    assert outcome.status == "draft"
    assert not (tmp_path / "data").exists()
    draft = json.loads((tmp_path / "setup-draft.json").read_text())
    assert "first-conversation" not in draft["completed"]
    assert "First attempt" not in json.dumps(draft)
    assert "Second attempt" not in json.dumps(draft)


def test_start_over_removes_the_draft_but_preserves_credentials(
    tmp_path: Path,
) -> None:
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-verified")
    store.save(tmp_path / "provider.json")
    run_workflow(tmp_path, [("provider", "__exit__")])

    outcome, _ui = run_workflow(
        tmp_path,
        [
            ("resume", "start-over"),
            ("start-over-credentials", "n"),
            ("start-over-confirm", "y"),
            ("provider", "fake"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Fresh start."),
            ("first-message-action", "accept"),
        ],
        services=offline_services(),
    )

    assert outcome.status == "activated"
    # The verified credential survived the restart.
    loaded = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert loaded.api_key_for("deepseek") == "sk-verified"
    assert loaded.is_active("deepseek")


def test_saved_credentials_alone_do_not_make_a_fresh_installation_configured(
    tmp_path: Path,
) -> None:
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-saved")
    store.save(tmp_path / "provider.json")

    outcome, ui = run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    assert outcome.status == "activated"
    assert (tmp_path / "data" / "instance.json").exists()
    displayed = "\n".join(ui.lines)
    assert "Installation status" not in displayed


def chat_transport(payload_content: str = "Here is my answer."):
    from urllib.request import Request

    calls: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        calls.append(request)
        return json.dumps(
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {"message": {"role": "assistant", "content": payload_content}}
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            }
        ).encode("utf-8")

    return transport, calls


def truncated_deepseek_transport():
    from urllib.request import Request

    calls: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        calls.append(request)
        return json.dumps(
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "private reasoning text",
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 8,
                    "total_tokens": 12,
                },
            }
        ).encode("utf-8")

    return transport, calls


DEEPSEEK_ANSWERS: list[tuple[str, str]] = [
    ("provider", "deepseek"),
    ("provider-key-source", "new"),
    ("provider-key", "sk-test-deepseek"),
    ("provider-model", "recommended"),
    ("provider-verify-consent", "y"),
    ("persona-live-sample", "n"),
    ("connectors", "skip"),
    ("persona", "simon"),
    ("persona-accept", "y"),
    ("profile", "skip"),
    ("review-action", "continue"),
    ("first-message", "Help me plan the semester."),
    ("first-message-action", "accept"),
]


def test_guided_setup_verifies_a_real_provider_and_never_displays_the_key(
    tmp_path: Path,
) -> None:
    transport, calls = chat_transport()
    outcome, ui = run_workflow(
        tmp_path,
        list(DEEPSEEK_ANSWERS),
        services=offline_services(transport=transport),
    )

    assert outcome.status == "activated"
    assert outcome.demo is False
    assert outcome.provider_id == "deepseek"
    displayed = "\n".join(ui.lines)
    assert "Real provider ready" in displayed
    assert "Demo mode" not in displayed
    assert "sk-test-deepseek" not in displayed
    # One verification request plus one first-conversation request.
    assert len(calls) == 2
    assert calls[0].full_url == "https://api.deepseek.com/chat/completions"
    verification_body = json.loads(calls[0].data.decode("utf-8"))
    conversation_body = json.loads(calls[1].data.decode("utf-8"))
    assert verification_body["thinking"] == {"type": "disabled"}
    assert "thinking" not in conversation_body
    # The first conversation has no tools, writes, or routines attached.
    assert "tools" not in conversation_body
    assert "tool_choice" not in conversation_body

    raw = json.loads((tmp_path / "provider.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["providers"]["deepseek"]["secret"] == "sk-test-deepseek"
    assert raw["providers"]["deepseek"]["verified"] is True
    assert raw["providers"]["deepseek"]["model"] == "deepseek-v4-flash"
    assert raw["default_provider"] == "deepseek"
    assert stat.S_IMODE((tmp_path / "provider.json").stat().st_mode) == 0o600

    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "deepseek"
    assert instance["activation"]["model"] == "deepseek-v4-flash"
    assert instance["activation"]["demo"] is False


def test_reasoning_only_truncated_verification_preserves_recovery_and_inactive_state(
    tmp_path: Path,
) -> None:
    transport, calls = truncated_deepseek_transport()
    outcome, ui = run_workflow(
        tmp_path,
        [
            ("provider", "deepseek"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-truncated"),
            ("provider-model", "recommended"),
            ("provider-verify-consent", "y"),
            ("provider-failure", "save"),
            ("provider", "fake"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Hello."),
            ("first-message-action", "accept"),
        ],
        services=offline_services(transport=transport),
    )

    displayed = "\n".join(ui.lines)
    assert outcome.status == "activated"
    assert outcome.provider_id == "fake"
    assert "DeepSeek provider test ended before a final answer." in displayed
    assert "sk-truncated" not in displayed
    assert "private reasoning text" not in displayed
    assert "reasoning_content" not in displayed
    for label in (
        "Retry the test",
        "Edit provider settings",
        "Back to the provider list",
        "Save for later",
        "Exit setup",
    ):
        assert label in displayed
    store = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert store.is_active("deepseek") is False
    assert len(calls) == 1


def test_manual_model_entry_stays_available_when_discovery_fails(
    tmp_path: Path,
) -> None:
    import io
    from urllib.error import HTTPError
    from urllib.request import Request

    calls: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        calls.append(request)
        if request.get_method() == "GET":
            raise HTTPError(
                request.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"{}")
            )
        return json.dumps(
            {
                "model": "deepseek-manual",
                "choices": [{"message": {"role": "assistant", "content": "ready"}}],
                "usage": {},
            }
        ).encode("utf-8")

    outcome, _ui = run_workflow(
        tmp_path,
        [
            ("provider", "deepseek"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-manual"),
            ("provider-model", "discover"),
            ("provider-failure", "edit"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-manual"),
            ("provider-model", "manual"),
            ("provider-model-manual", "deepseek-manual"),
            ("provider-verify-consent", "y"),
            ("persona-live-sample", "n"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Hello."),
            ("first-message-action", "accept"),
        ],
        services=offline_services(transport=transport),
    )

    assert outcome.status == "activated"
    assert outcome.provider_id == "deepseek"
    assert outcome.model == "deepseek-manual"
    assert any(call.get_method() == "GET" for call in calls)
    assert any(call.get_method() == "POST" for call in calls)


def test_an_environment_reference_is_reused_without_copying_its_value(
    tmp_path: Path,
) -> None:
    transport, _ = chat_transport()
    outcome, ui = run_workflow(
        tmp_path,
        [
            ("provider", "deepseek"),
            ("provider-key-source", "env-ref"),
            ("provider-model", "recommended"),
            ("provider-verify-consent", "y"),
            ("persona-live-sample", "n"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Hello."),
            ("first-message-action", "accept"),
        ],
        services=offline_services(
            environ={"DEEPSEEK_API_KEY": "sk-env-value"}, transport=transport
        ),
    )

    assert outcome.status == "activated"
    raw_text = (tmp_path / "provider.json").read_text(encoding="utf-8")
    assert "sk-env-value" not in raw_text
    assert "env:DEEPSEEK_API_KEY" in raw_text
    displayed = "\n".join(ui.lines)
    assert "DEEPSEEK_API_KEY is set" in displayed
    assert "sk-env-value" not in displayed


def test_a_failed_verification_can_be_saved_inactive_and_never_completes_setup(
    tmp_path: Path,
) -> None:
    import io
    from urllib.error import HTTPError

    def failing_transport(request, timeout: float) -> bytes:
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error": {"message": "bad key"}}'),
        )

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("provider", "deepseek"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-bad"),
            ("provider-model", "recommended"),
            ("provider-verify-consent", "y"),
            ("provider-failure", "save"),
            ("provider", "fake"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Hello."),
            ("first-message-action", "accept"),
        ],
        services=offline_services(transport=failing_transport),
    )

    # Setup completed in demo mode; the unverified credential stayed inactive.
    assert outcome.demo is True
    store = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert store.api_key_for("deepseek") == "sk-bad"
    assert store.is_active("deepseek") is False
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "fake"
    displayed = "\n".join(ui.lines)
    assert "stays inactive" in displayed
    assert "HTTP 401" in displayed


class StubTelegramClient:
    def __init__(self, updates: tuple[dict[str, object], ...] = ()) -> None:
        self.updates = updates
        self.sent: list[tuple[str, str]] = []

    def get_me(self) -> dict[str, object]:
        return {"id": 7, "username": "reckoning_test_bot"}

    def get_webhook_info(self) -> dict[str, object]:
        return {"url": ""}

    def delete_webhook(self) -> None:
        pass

    def get_updates(self, *, offset, timeout):
        return self.updates

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


PAIRING_UPDATES = (
    {
        "update_id": 5,
        "message": {
            "chat": {"id": 42, "type": "private"},
            "text": "/connect abc123",
        },
    },
)


def test_telegram_pairing_reports_ready_and_sends_a_confirmation(
    tmp_path: Path,
) -> None:
    client = StubTelegramClient(PAIRING_UPDATES)
    answers = [
        ("provider", "fake"),
        ("connectors", "telegram"),
        ("telegram-token", "bot-token"),
        ("telegram-pair", "y"),
        ("persona", "simon"),
        ("persona-accept", "y"),
        ("profile", "skip"),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]
    outcome, ui = run_workflow(
        tmp_path,
        answers,
        services=offline_services(
            telegram_api_factory=lambda token: client,
            telegram_pairing_code="abc123",
            telegram_max_polls=1,
        ),
    )

    assert outcome.status == "activated"
    assert outcome.telegram_status == "ready"
    assert client.sent == [("42", client.sent[0][1])]
    config = TelegramConnectorConfig.load(tmp_path / "telegram.json")
    assert config.paired_chat_id == "42"
    assert config.provider_name == "fake"
    displayed = "\n".join(ui.lines)
    assert "Bot verified: @reckoning_test_bot" in displayed
    assert "reckoning gateway" in displayed


def test_telegram_can_be_saved_verified_without_pairing(tmp_path: Path) -> None:
    client = StubTelegramClient()
    answers = [
        ("provider", "fake"),
        ("connectors", "telegram"),
        ("telegram-token", "bot-token"),
        ("telegram-pair", "n"),
        ("persona", "simon"),
        ("persona-accept", "y"),
        ("profile", "skip"),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]
    outcome, ui = run_workflow(
        tmp_path,
        answers,
        services=offline_services(telegram_api_factory=lambda token: client),
    )

    assert outcome.telegram_status == "verified-not-paired"
    assert client.sent == []
    displayed = "\n".join(ui.lines)
    assert "Verified, not paired" in displayed


def test_status_view_edits_the_telegram_section_in_place(tmp_path: Path) -> None:
    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))
    client = StubTelegramClient(PAIRING_UPDATES)

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("status-action", "edit"),
            ("edit-section", "connectors"),
            ("telegram-token", "bot-token"),
            ("telegram-pair", "y"),
            ("status-action", "exit"),
        ],
        services=offline_services(
            telegram_api_factory=lambda token: client,
            telegram_pairing_code="abc123",
            telegram_max_polls=1,
        ),
    )

    assert outcome.status == "managed"
    config = TelegramConnectorConfig.load(tmp_path / "telegram.json")
    assert config.paired_chat_id == "42"
    assert "Ready" in "\n".join(ui.lines)
    # Editing the Telegram section did not disturb the installed provider.
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "fake"


def test_guided_profile_answers_become_unconfirmed_proposals(
    tmp_path: Path,
) -> None:
    answers = [
        ("provider", "fake"),
        ("connectors", "skip"),
        ("persona", "simon"),
        ("persona-accept", "y"),
        ("profile", "guided"),
        ("profile-address", "Mary"),
        ("profile-work", "Finish the semester strong"),
        ("profile-priorities", ""),
        ("profile-preferences", ""),
        ("profile-boundaries", ""),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]
    outcome, _ = run_workflow(tmp_path, answers)

    assert outcome.status == "activated"
    versions = JsonFilePersonalContextRepository(
        tmp_path / "data" / "personal-context" / "personal-context.json"
    ).all_versions()
    meanings = [item.canonical_meaning for item in versions]
    assert any("address: Mary" in meaning for meaning in meanings)
    assert any(
        "work: Finish the semester strong" in meaning for meaning in meanings
    )
    assert {item.status for item in versions} == {"proposed"}


def test_profile_import_previews_and_removes_statements(tmp_path: Path) -> None:
    profile = tmp_path / "profile.md"
    profile.write_text(
        "# About me\nI study computer science.\n\n# Goals\n- Graduate in 2028\n",
        encoding="utf-8",
    )
    answers = [
        ("provider", "fake"),
        ("connectors", "skip"),
        ("persona", "simon"),
        ("persona-accept", "y"),
        ("profile", "import"),
        ("profile-import-path", str(profile)),
        ("profile-import-remove", "2"),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]
    outcome, ui = run_workflow(tmp_path, answers)

    assert outcome.status == "activated"
    displayed = "\n".join(ui.lines)
    assert "Parsed statements" in displayed
    versions = JsonFilePersonalContextRepository(
        tmp_path / "data" / "personal-context" / "personal-context.json"
    ).all_versions()
    all_text = "\n".join(
        f"{item.original_text}\n{item.canonical_meaning}" for item in versions
    )
    assert "Graduate in 2028" not in all_text
    assert "computer science" in all_text


def test_review_can_change_storage_to_a_personal_server(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "server"
    answers = [
        ("provider", "fake"),
        ("connectors", "skip"),
        ("persona", "steady"),
        ("persona-accept", "y"),
        ("profile", "skip"),
        ("review-action", "placement"),
        ("placement", "personal-server"),
        ("server-data-dir", str(server_dir)),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]
    outcome, _ = run_workflow(tmp_path, answers)

    assert outcome.status == "activated"
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["placement_profile"] == "personal-server"
    assert (server_dir / ".reckoning-node.json").exists()


def test_persona_authoring_flow_builds_an_original_persona(tmp_path: Path) -> None:
    answers = [
        ("provider", "fake"),
        ("connectors", "skip"),
        ("persona", "author"),
        ("persona-name", "Clear Eyed"),
        ("persona-id", ""),
        ("persona-start", "blank"),
        ("persona-axis-voice", "candid"),
        ("persona-axis-directness", "direct"),
        ("persona-axis-warmth", "warm"),
        ("persona-axis-humor", "dry"),
        ("persona-axis-challenge", "demanding"),
        ("persona-axis-sensitive_topic_handling", "practical"),
        ("persona-accept", "y"),
        ("profile", "skip"),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]
    outcome, ui = run_workflow(tmp_path, answers)

    assert outcome.persona_id == "clear-eyed"
    personas = json.loads((tmp_path / "data" / "personas.json").read_text())
    assert personas["active_persona_id"] == "clear-eyed"
    (authored,) = personas["authored"]
    assert authored["voice"] == "candid"
    assert authored["challenge"] == "demanding"
    displayed = "\n".join(ui.lines)
    assert "autonomy floor" in displayed
    assert "Persona preview" in displayed


def test_the_status_view_loads_without_network_and_offers_actions(
    tmp_path: Path,
) -> None:
    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    def forbidden_transport(request, timeout: float) -> bytes:
        raise AssertionError("the status view must not make network calls")

    outcome, ui = run_workflow(
        tmp_path,
        [("status-action", "exit")],
        services=offline_services(transport=forbidden_transport),
    )

    assert outcome.status == "managed"
    displayed = "\n".join(ui.lines)
    assert "Installation status" in displayed
    assert "Storage: local" in displayed
    assert "Agent style: Simon" in displayed
    assert "AI provider and model: fake (demo mode)" in displayed
    assert "Interfaces: Terminal ready; Web ready" in displayed
    assert "Telegram: Not configured" in displayed
    assert "Gateway: Stopped; start with `reckoning gateway`" in displayed
    assert "Setup draft: none" in displayed
    assert "Installation format: current" in displayed
    assert "Verify all" in displayed


def test_status_repairs_an_invalid_draft_only_after_confirmation(
    tmp_path: Path,
) -> None:
    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))
    draft_path = tmp_path / "setup-draft.json"
    draft_path.write_text("not json", encoding="utf-8")

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("status-action", "repair"),
            ("repair-section", "draft"),
            ("repair-draft-remove", "n"),
            ("status-action", "repair"),
            ("repair-section", "draft"),
            ("repair-draft-remove", "y"),
            ("status-action", "exit"),
        ],
    )

    assert outcome.status == "managed"
    assert not draft_path.exists()
    displayed = "\n".join(ui.lines)
    assert displayed.count("Remove the invalid setup draft?") == 2
    assert "Setup draft: none" in displayed


def test_verify_all_asks_before_a_paid_refresh(tmp_path: Path) -> None:
    transport, calls = chat_transport()
    run_workflow(
        tmp_path, list(DEEPSEEK_ANSWERS),
        services=offline_services(transport=transport),
    )
    calls.clear()

    # Declining the paid refresh makes no request.
    _, ui = run_workflow(
        tmp_path,
        [("status-action", "verify-all"), ("verify-paid-refresh", "n"),
         ("status-action", "exit")],
        services=offline_services(transport=transport),
    )
    assert calls == []
    assert "Skipped the paid provider check." in "\n".join(ui.lines)

    # Accepting it re-verifies and refreshes the timestamp.
    _, ui = run_workflow(
        tmp_path,
        [("status-action", "verify-all"), ("verify-paid-refresh", "y"),
         ("status-action", "exit")],
        services=offline_services(transport=transport),
    )
    assert len(calls) == 1
    assert "verified" in "\n".join(ui.lines)


def test_section_editing_changes_the_persona_without_rerunning_setup(
    tmp_path: Path,
) -> None:
    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("status-action", "edit"),
            ("edit-section", "persona"),
            ("persona-manage", "select"),
            ("persona-select", "steady"),
            ("status-action", "exit"),
        ],
    )

    assert outcome.status == "managed"
    personas = json.loads((tmp_path / "data" / "personas.json").read_text())
    assert personas["active_persona_id"] == "steady"
    assert "Active persona: Steady" in "\n".join(ui.lines)


def test_status_view_reports_a_running_gateway(tmp_path: Path) -> None:
    from reckoning.runtime_status import record_gateway_runtime

    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))
    with record_gateway_runtime(tmp_path / "data"):
        outcome, ui = run_workflow(
            tmp_path,
            [("status-action", "exit")],
        )

    assert outcome.status == "managed"
    assert "Gateway: Running (pid " in "\n".join(ui.lines)


def test_review_edits_one_row_and_returns_to_review(tmp_path: Path) -> None:
    answers = [
        ("provider", "fake"),
        ("connectors", "skip"),
        ("persona", "simon"),
        ("persona-accept", "y"),
        ("profile", "skip"),
        ("review-action", "persona"),
        ("persona", "steady"),
        ("persona-accept", "y"),
        ("review-action", "continue"),
        ("first-message", "Hello."),
        ("first-message-action", "accept"),
    ]

    outcome, ui = run_workflow(tmp_path, answers)

    assert outcome.status == "activated"
    assert outcome.persona_id == "steady"
    # Editing one row returns to Review without repeating the provider step.
    assert (
        sum(
            line == "Step 1/5 — AI provider and model" for line in ui.lines
        )
        == 1
    )
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["active_persona_id"] == "steady"


def test_personal_server_placement_requires_a_directory(tmp_path: Path) -> None:
    with pytest.raises(SetupInputError, match="server-data-dir is required"):
        run_workflow(
            tmp_path,
            [],
            preselected={"placement": "personal-server"},
        )


def test_migration_previews_backs_up_and_commits_atomically(tmp_path: Path) -> None:
    setup_instance(tmp_path / "data", "local")
    ProviderCredentialStore().save(tmp_path / "provider.json")
    # Legacy v1 credential format.
    (tmp_path / "provider.json").write_text(
        json.dumps(
            {"default_provider": "deepseek", "providers": {"deepseek": "sk-old"}}
        ),
        encoding="utf-8",
    )

    outcome, ui = run_workflow(
        tmp_path,
        [("migrate", "migrate"), ("migrate-confirm", "y"),
         ("status-action", "exit")],
    )

    assert outcome.status == "managed"
    displayed = "\n".join(ui.lines)
    assert "needs migration" in displayed
    assert "backup kept" in displayed
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "deepseek"
    assert instance["activation"]["model"] == "deepseek-v4-flash"
    assert (tmp_path / "provider.json.bak").exists()
    assert (tmp_path / "data" / "instance.json.bak").exists()
    migrated = json.loads((tmp_path / "provider.json").read_text())
    assert migrated["schema_version"] == 2
    assert migrated["providers"]["deepseek"]["secret"] == "sk-old"


def test_non_interactive_setup_matches_the_interactive_result(
    tmp_path: Path,
) -> None:
    from reckoning.setup_terminal import NonInteractiveUI

    interactive = tmp_path / "interactive"
    interactive.mkdir()
    run_workflow(interactive, list(GUIDED_FAKE_ANSWERS))

    scripted = tmp_path / "scripted"
    scripted.mkdir()
    ui = NonInteractiveUI(
        {
            "profile": "skip",
            "connectors": "skip",
            "review-action": "continue",
            "first-message": "Automated first message.",
            "first-message-action": "accept",
        },
        output=lambda line: None,
    )
    workflow = SetupWorkflow(
        paths=make_paths(scripted),
        ui=ui,
        services=offline_services(),
        preselected={"persona": "simon", "provider": "fake"},
    )
    outcome = workflow.run()

    assert outcome.status == "activated"
    left = json.loads((interactive / "data" / "instance.json").read_text())
    right = json.loads((scripted / "data" / "instance.json").read_text())
    for field_name in (
        "schema_version",
        "instance_type",
        "placement_profile",
        "active_persona_id",
        "placement_policy",
    ):
        assert right[field_name] == left[field_name]
    assert right["activation"]["provider"] == left["activation"]["provider"]


def test_non_interactive_setup_fails_loudly_on_a_missing_answer(
    tmp_path: Path,
) -> None:
    from reckoning.setup_terminal import NonInteractiveUI

    ui = NonInteractiveUI({}, output=lambda line: None)
    workflow = SetupWorkflow(
        paths=make_paths(tmp_path),
        ui=ui,
        services=offline_services(),
        preselected={"persona": "simon"},
    )
    with pytest.raises(SetupInputError, match="provider"):
        workflow.run()
    assert not (tmp_path / "data").exists()


def test_the_optional_live_persona_sample_runs_after_verification(
    tmp_path: Path,
) -> None:
    transport, calls = chat_transport("I am Simon, your Reckoning agent.")
    outcome, ui = run_workflow(
        tmp_path,
        [
            ("provider", "deepseek"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-sample"),
            ("provider-model", "recommended"),
            ("provider-verify-consent", "y"),
            ("persona-live-sample", "y"),
            ("persona-live-sample-keep", "y"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "Hello."),
            ("first-message-action", "accept"),
        ],
        services=offline_services(transport=transport),
    )

    assert outcome.status == "activated"
    displayed = "\n".join(ui.lines)
    assert "I am Simon, your Reckoning agent." in displayed
    # Verification, live sample, and first conversation: three requests.
    assert len(calls) == 3


def test_resuming_after_profile_onboarding_keeps_the_proposals(
    tmp_path: Path,
) -> None:
    first_outcome, _ = run_workflow(
        tmp_path,
        [
            ("provider", "fake"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "guided"),
            ("profile-address", "Mary"),
            ("profile-work", ""),
            ("profile-priorities", ""),
            ("profile-preferences", ""),
            ("profile-boundaries", ""),
            ("review-action", "__exit__"),
        ],
    )
    assert first_outcome.status == "draft"

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("resume", "resume"),
            ("review-action", "continue"),
            ("first-message", "Hello after resuming."),
            ("first-message-action", "accept"),
        ],
    )

    assert outcome.status == "activated"
    versions = JsonFilePersonalContextRepository(
        tmp_path / "data" / "personal-context" / "personal-context.json"
    ).all_versions()
    assert [item.original_text for item in versions] == ["Mary"]
    assert "About you: 1 unconfirmed proposals" in ui.lines


def test_keyless_local_provider_remains_active_in_the_installed_runtime(
    tmp_path: Path,
) -> None:
    from reckoning.application import create_local_application
    from reckoning.config import RuntimeProviderSettings
    from reckoning.operations import load_installation_runtime

    transport, calls = chat_transport("Local runtime answer.")
    services = offline_services(
        probe=lambda url: url.endswith(":11434"),
        transport=transport,
    )
    outcome, _ = run_workflow(
        tmp_path,
        [
            ("provider", "ollama"),
            ("provider-model", "manual"),
            ("provider-model-manual", "qwen3"),
            ("provider-verify-consent", "y"),
            ("persona-live-sample", "n"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "skip"),
            ("review-action", "continue"),
            ("first-message", "First local message."),
            ("first-message-action", "accept"),
        ],
        services=services,
    )
    assert outcome.provider_id == "ollama"

    _, status_ui = run_workflow(
        tmp_path,
        [("status-action", "exit")],
        services=services,
    )
    assert any(
        "[ok] AI provider and model: ollama, model qwen3" in line
        for line in status_ui.lines
    )

    settings = RuntimeProviderSettings.load(
        tmp_path / "data",
        credentials_path=tmp_path / "provider.json",
        environ={},
    )
    runtime = load_installation_runtime(tmp_path / "data")
    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        provider_name=settings.provider_name,
        provider_config=AdapterConfig(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
        ),
        provider_transport=transport,
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    answer = application.send_message("Use the installed local provider.")
    assert "Local runtime answer." in answer.content
    assert calls[-1].full_url == "http://127.0.0.1:11434/v1/chat/completions"


def test_custom_provider_is_not_selectable_until_its_smoke_passes(
    tmp_path: Path,
) -> None:
    from reckoning.provider_adapters import (
        candidate_adapter_for,
        setup_adapter_for,
    )

    outcome, ui = run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    assert outcome.status == "activated"
    rendered = "\n".join(ui.lines)
    assert "option: custom " not in rendered
    assert "More providers coming soon" in rendered
    with pytest.raises(KeyError, match="not available"):
        setup_adapter_for("custom")
    assert candidate_adapter_for("custom").definition.available is False


def test_preselected_coming_soon_provider_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SetupInputError, match="coming soon"):
        run_workflow(
            tmp_path,
            [],
            preselected={"provider": "custom"},
        )
    assert not (tmp_path / "data").exists()


def test_custom_header_references_are_validated() -> None:
    environ = {"CUSTOM_TENANT": "tenant-secret"}
    references = SetupWorkflow._parse_header_env(
        "X-Tenant=CUSTOM_TENANT", environ
    )
    assert references == {"X-Tenant": "CUSTOM_TENANT"}
    with pytest.raises(SetupInputError, match="Header=ENV_VAR"):
        SetupWorkflow._parse_header_env("X-Tenant", environ)
    with pytest.raises(SetupInputError, match="not set"):
        SetupWorkflow._parse_header_env("X-Tenant=MISSING_TENANT", environ)


def test_reopen_failure_rolls_back_the_new_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = ScriptedUI(list(GUIDED_FAKE_ANSWERS))
    workflow = SetupWorkflow(
        paths=make_paths(tmp_path),
        ui=ui,
        services=offline_services(),
    )

    def fail_reopen() -> None:
        raise OperationError("simulated reopen failure")

    monkeypatch.setattr(workflow, "_prove_reopen", fail_reopen)
    with pytest.raises(OperationError, match="Activation failed"):
        workflow.run()

    assert not (tmp_path / "data").exists()
    assert (tmp_path / "setup-draft.json").exists()


def test_partial_setup_failure_rolls_back_created_installation_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_partway(data_dir: Path, *args: object, **kwargs: object) -> None:
        data_dir.mkdir(parents=True)
        (data_dir / "partial.json").write_text("partial", encoding="utf-8")
        raise OSError("simulated setup failure")

    monkeypatch.setattr("reckoning.setup_workflow.setup_instance", fail_partway)

    with pytest.raises(OperationError, match="Activation failed"):
        run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    assert not (tmp_path / "data").exists()
    assert (tmp_path / "setup-draft.json").exists()


def test_activation_never_erases_a_nonempty_installation_root(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sentinel = data_dir / "keep.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    with pytest.raises(OperationError, match="empty installation root"):
        run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))

    assert sentinel.read_text(encoding="utf-8") == "keep me"


def test_verified_credential_is_not_activated_before_setup_finishes(
    tmp_path: Path,
) -> None:
    transport, _ = chat_transport()

    outcome, _ = run_workflow(
        tmp_path,
        [
            ("provider", "deepseek"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-pending-activation"),
            ("provider-model", "recommended"),
            ("provider-verify-consent", "y"),
            ("persona-live-sample", "n"),
            ("connectors", "skip"),
            ("persona", "simon"),
            ("persona-accept", "y"),
            ("profile", "__exit__"),
        ],
        services=offline_services(transport=transport),
    )

    assert outcome.status == "draft"
    store = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert store.api_key_for("deepseek") == "sk-pending-activation"
    assert store.default_provider is None


def test_provider_edit_updates_the_authoritative_installed_provider(
    tmp_path: Path,
) -> None:
    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))
    transport, _ = chat_transport()

    _, ui = run_workflow(
        tmp_path,
        [
            ("status-action", "edit"),
            ("edit-section", "provider"),
            ("provider", "deepseek"),
            ("provider-key-source", "new"),
            ("provider-key", "sk-edited"),
            ("provider-model", "recommended"),
            ("provider-verify-consent", "y"),
            ("persona-live-sample", "n"),
            ("status-action", "exit"),
        ],
        services=offline_services(transport=transport),
    )

    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "deepseek"
    assert any(
        "[ok] AI provider and model: deepseek" in line for line in ui.lines
    )
    assert sum("fake (demo mode)" in line for line in ui.lines) == 1


def test_provider_edit_rolls_back_when_the_installed_settings_do_not_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning.config import RuntimeProviderSettings

    run_workflow(tmp_path, list(GUIDED_FAKE_ANSWERS))
    transport, _ = chat_transport()
    original_load = RuntimeProviderSettings.load.__func__
    load_calls = 0

    def fail_commit_reopen(cls, *args: object, **kwargs: object):
        nonlocal load_calls
        load_calls += 1
        if load_calls == 2:
            raise ValueError("simulated provider reopen failure")
        return original_load(cls, *args, **kwargs)

    monkeypatch.setattr(
        RuntimeProviderSettings,
        "load",
        classmethod(fail_commit_reopen),
    )

    with pytest.raises(OperationError, match="rolled back"):
        run_workflow(
            tmp_path,
            [
                ("status-action", "edit"),
                ("edit-section", "provider"),
                ("provider", "deepseek"),
                ("provider-key-source", "new"),
                ("provider-key", "sk-edited"),
                ("provider-model", "recommended"),
                ("provider-verify-consent", "y"),
                ("persona-live-sample", "n"),
            ],
            services=offline_services(transport=transport),
        )

    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "fake"
    assert (
        ProviderCredentialStore.load(tmp_path / "provider.json").default_provider
        is None
    )


def test_provider_edit_can_make_fake_authoritative_over_saved_credentials(
    tmp_path: Path,
) -> None:
    transport, _ = chat_transport()
    run_workflow(
        tmp_path,
        list(DEEPSEEK_ANSWERS),
        services=offline_services(transport=transport),
    )

    _, ui = run_workflow(
        tmp_path,
        [
            ("status-action", "edit"),
            ("edit-section", "provider"),
            ("provider", "fake"),
            ("status-action", "exit"),
        ],
        services=offline_services(transport=transport),
    )

    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    store = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert instance["activation"]["provider"] == "fake"
    assert store.default_provider is None
    assert store.api_key_for("deepseek") == "sk-test-deepseek"
    assert any(
        "[ok] AI provider and model: fake (demo mode)" in line
        for line in ui.lines
    )


def _legacy_installation(tmp_path: Path) -> None:
    setup_instance(tmp_path / "data", "local")
    (tmp_path / "provider.json").write_text(
        json.dumps(
            {"default_provider": "deepseek", "providers": {"deepseek": "sk-old"}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "telegram.json").write_text(
        json.dumps(
            {
                "bot_token": "bot-token",
                "bot_username": "reckoning_test_bot",
                "allowed_chat_ids": ["42"],
                "provider_name": "fake",
            }
        ),
        encoding="utf-8",
    )


def _flaky_replace(monkeypatch: pytest.MonkeyPatch, fail_on: int) -> None:
    import os as os_module

    real_replace = os_module.replace
    state = {"calls": 0, "failed": False}

    def flaky(source: object, destination: object) -> None:
        state["calls"] += 1
        if state["calls"] == fail_on and not state["failed"]:
            state["failed"] = True
            raise OSError("simulated interruption")
        real_replace(source, destination)

    monkeypatch.setattr("reckoning.setup_workflow.os.replace", flaky)


@pytest.mark.parametrize("fail_on", (1, 2, 3))
def test_interrupted_migration_restores_the_original_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_on: int
) -> None:
    _legacy_installation(tmp_path)
    instance_path = tmp_path / "data" / "instance.json"
    original_instance = instance_path.read_bytes()
    original_credentials = (tmp_path / "provider.json").read_bytes()
    original_telegram = (tmp_path / "telegram.json").read_bytes()
    _flaky_replace(monkeypatch, fail_on)

    workflow = SetupWorkflow(
        paths=make_paths(tmp_path),
        ui=ScriptedUI([("migrate", "migrate"), ("migrate-confirm", "y")]),
        services=offline_services(),
    )
    with pytest.raises(OperationError, match="Migration failed"):
        workflow.run()

    assert instance_path.read_bytes() == original_instance
    assert (tmp_path / "provider.json").read_bytes() == original_credentials
    assert (tmp_path / "telegram.json").read_bytes() == original_telegram


def test_interrupted_migration_leaves_a_reopenable_legacy_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckoning.operations import diagnose, load_installation_runtime

    _legacy_installation(tmp_path)
    _flaky_replace(monkeypatch, 3)
    workflow = SetupWorkflow(
        paths=make_paths(tmp_path),
        ui=ScriptedUI([("migrate", "migrate"), ("migrate-confirm", "y")]),
        services=offline_services(),
    )
    with pytest.raises(OperationError, match="Migration failed"):
        workflow.run()

    # The legacy installation is still readable after the interruption.
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert "activation" not in instance
    assert ProviderCredentialStore.load(
        tmp_path / "provider.json"
    ).api_key_for("deepseek") == "sk-old"
    assert load_installation_runtime(tmp_path / "data") is not None
    assert diagnose(tmp_path / "data")


def test_migration_backup_failure_leaves_every_file_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil as shutil_module

    _legacy_installation(tmp_path)
    original_instance = (tmp_path / "data" / "instance.json").read_bytes()

    def fail_copy(*args: object, **kwargs: object) -> None:
        raise OSError("simulated backup failure")

    monkeypatch.setattr(shutil_module, "copy2", fail_copy)
    workflow = SetupWorkflow(
        paths=make_paths(tmp_path),
        ui=ScriptedUI([("migrate", "migrate"), ("migrate-confirm", "y")]),
        services=offline_services(),
    )
    with pytest.raises(OperationError, match="Migration failed"):
        workflow.run()

    assert (tmp_path / "data" / "instance.json").read_bytes() == original_instance
    assert not (tmp_path / "data" / "instance.json.bak").exists()


def test_migration_preview_only_and_cancellation_change_nothing(
    tmp_path: Path,
) -> None:
    _legacy_installation(tmp_path)
    original_instance = (tmp_path / "data" / "instance.json").read_bytes()

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("migrate", "migrate"),
            ("migrate-confirm", "n"),
            ("status-action", "exit"),
        ],
    )

    assert outcome.status == "managed"
    assert "cancelled" in "\n".join(ui.lines)
    assert (tmp_path / "data" / "instance.json").read_bytes() == original_instance
    assert not (tmp_path / "data" / "instance.json.bak").exists()
    assert not (tmp_path / "provider.json.bak").exists()


def test_migration_skip_changes_nothing(tmp_path: Path) -> None:
    _legacy_installation(tmp_path)
    original_instance = (tmp_path / "data" / "instance.json").read_bytes()

    outcome, _ui = run_workflow(
        tmp_path,
        [("migrate", "skip"), ("status-action", "exit")],
    )

    assert outcome.status == "managed"
    assert (tmp_path / "data" / "instance.json").read_bytes() == original_instance
    assert "activation" not in json.loads(
        (tmp_path / "data" / "instance.json").read_text()
    )


def test_corrupt_credential_store_is_rejected_without_changes(
    tmp_path: Path,
) -> None:
    _legacy_installation(tmp_path)
    credential_path = tmp_path / "provider.json"
    credential_path.write_text("{not valid json", encoding="utf-8")
    original = credential_path.read_bytes()

    outcome, ui = run_workflow(
        tmp_path,
        [("status-action", "exit")],
    )

    assert outcome.status == "managed"
    assert "cannot be migrated safely" in "\n".join(ui.lines)
    assert credential_path.read_bytes() == original


def test_unknown_provider_credentials_are_rejected(tmp_path: Path) -> None:
    _legacy_installation(tmp_path)
    (tmp_path / "provider.json").write_text(
        json.dumps(
            {
                "default_provider": "mystery",
                "providers": {"mystery": "sk-unknown"},
            }
        ),
        encoding="utf-8",
    )
    original = (tmp_path / "provider.json").read_bytes()

    outcome, ui = run_workflow(tmp_path, [("status-action", "exit")])

    assert outcome.status == "managed"
    assert "cannot be migrated safely" in "\n".join(ui.lines)
    assert (tmp_path / "provider.json").read_bytes() == original


def test_complete_migration_preserves_every_supported_state(
    tmp_path: Path,
) -> None:
    from reckoning.config import RuntimeProviderSettings
    from reckoning.interfaces import JsonFileInterfaceRepository
    from reckoning.operations import diagnose, load_installation_runtime
    from reckoning.personas import DEFAULT_PERSONAS

    server_dir = tmp_path / "server"
    profile = tmp_path / "about-me.md"
    profile.write_text(
        "# About me\nI study computer science.\n", encoding="utf-8"
    )
    steady = next(item for item in DEFAULT_PERSONAS if item.id == "steady")
    setup_instance(
        tmp_path / "data",
        "personal-server",
        steady,
        server_data_dir=server_dir,
        user_profile=profile,
        first_conversation=("What should I focus on?", "Protect your mornings."),
    )
    (tmp_path / "provider.json").write_text(
        json.dumps(
            {
                "default_provider": "deepseek",
                "providers": {
                    "deepseek": {
                        "secret": "sk-preserved",
                        "model": "deepseek-chat",
                        "base_url": None,
                        "verified": True,
                        "verified_at": "2026-09-01T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "telegram.json").write_text(
        json.dumps(
            {
                "bot_token": "bot-token",
                "bot_username": "reckoning_test_bot",
                "allowed_chat_ids": ["42"],
                "provider_name": "deepseek",
            }
        ),
        encoding="utf-8",
    )

    outcome, ui = run_workflow(
        tmp_path,
        [
            ("migrate", "migrate"),
            ("migrate-confirm", "y"),
            ("status-action", "exit"),
        ],
    )

    assert outcome.status == "managed"
    assert "Migration committed" in "\n".join(ui.lines)
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["activation"]["provider"] == "deepseek"
    assert instance["activation"]["model"] == "deepseek-chat"
    assert instance["active_persona_id"] == "steady"
    assert instance["placement_profile"] == "personal-server"
    assert instance["storage_roots"]["server"] == str(server_dir)

    store = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert store.api_key_for("deepseek") == "sk-preserved"
    assert store.credential_for("deepseek").model == "deepseek-chat"

    telegram = TelegramConnectorConfig.load(tmp_path / "telegram.json")
    assert telegram.paired_chat_id == "42"
    assert telegram.provider_name == "deepseek"

    runtime = load_installation_runtime(
        tmp_path / "data", server_data_dir=server_dir
    )
    versions = JsonFilePersonalContextRepository(
        runtime.state_path("personal-context", "personal-context.json")
    ).all_versions()
    assert [item.original_text for item in versions] == [
        "I study computer science."
    ]

    confirmed = next(
        route for route in runtime.routes if route.category == "confirmed-state"
    )
    state = JsonFileInterfaceRepository(confirmed.root / "interfaces.json").load()
    messages = [
        (message.role, message.content)
        for session in state.sessions
        for message in session.messages
    ]
    assert ("user", "What should I focus on?") in messages
    assert ("assistant", "Protect your mornings.") in messages

    # The migrated installation reopens through setup and doctor.
    assert RuntimeProviderSettings.load(
        tmp_path / "data",
        credentials_path=tmp_path / "provider.json",
        environ={},
    ).provider_name == "deepseek"
    report = diagnose(tmp_path / "data", server_data_dir=server_dir)
    assert report is not None


def test_installed_state_migration_preserves_choices_and_authorization(
    tmp_path: Path,
) -> None:
    from reckoning.operations import load_installation_runtime
    from reckoning.personas import DEFAULT_PERSONAS

    server_dir = tmp_path / "server"
    profile = tmp_path / "about-me.md"
    profile.write_text(
        "# About me\nI study computer science.\n", encoding="utf-8"
    )
    steady = next(item for item in DEFAULT_PERSONAS if item.id == "steady")
    setup_instance(
        tmp_path / "data",
        "personal-server",
        steady,
        server_data_dir=server_dir,
        user_profile=profile,
    )
    ProviderCredentialStore().save(tmp_path / "provider.json")
    TelegramConnectorConfig(
        "bot-token", "reckoning_test_bot", "42", "fake"
    ).save(tmp_path / "telegram.json")

    outcome, _ui = run_workflow(
        tmp_path,
        [
            ("migrate", "migrate"),
            ("migrate-confirm", "y"),
            ("status-action", "exit"),
        ],
    )

    assert outcome.status == "managed"
    instance = json.loads((tmp_path / "data" / "instance.json").read_text())
    assert instance["active_persona_id"] == "steady"
    assert instance["placement_profile"] == "personal-server"
    assert instance["storage_roots"]["server"] == str(server_dir)
    assert "activation" in instance

    telegram = TelegramConnectorConfig.load(tmp_path / "telegram.json")
    assert telegram.paired_chat_id == "42"

    runtime = load_installation_runtime(
        tmp_path / "data", server_data_dir=server_dir
    )
    versions = JsonFilePersonalContextRepository(
        runtime.state_path("personal-context", "personal-context.json")
    ).all_versions()
    assert [item.original_text for item in versions] == [
        "I study computer science."
    ]
