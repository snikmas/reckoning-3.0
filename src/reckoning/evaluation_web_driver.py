"""Web-driving helpers for conversation-quality evaluation.

Drives the real ReckoningWebApplication through rendered forms so the
scenario runner exercises the same browser-mutation path a user does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Callable, Iterable, cast
from urllib.request import Request
from urllib.parse import urlencode

from reckoning.application import ReckoningApplication
from reckoning.config import DEFAULT_PROVIDER_CREDENTIALS, RuntimeProviderSettings
from reckoning.continuity import (
    Evidence,
    Inference,
    MaterialQuestion,
    PersonalRecordProposal,
    PersonalRecordType,
    Reckoning,
    ReckoningDraft,
    SourcedFact,
)
from reckoning.interfaces import (
    ChannelName,
    JsonFileInterfaceRepository,
    create_local_interface_application,
)
from reckoning.json_store import read_json
from reckoning.operations import (
    create_transfer,
    load_installation_runtime,
    restore_transfer,
    setup_instance,
)
from reckoning.personas import JsonFilePersonaRepository, PersonaService
from reckoning.provider_adapters import AdapterConfig
from reckoning.setup_workflow import MenuOption, SetupPaths, SetupServices, SetupWorkflow
from reckoning.terminal import main as terminal_main
from reckoning.web import ReckoningWebApplication, local_browser_origins


@dataclass
class ParsedForm:
    action: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    textareas: dict[str, str] = field(default_factory=dict)

    def submission(self, **overrides: str) -> dict[str, str]:
        fields = {
            name: value
            for name, value in self.fields.items()
            if name != "_csrf_token"
        }
        return {**fields, **self.textareas, **overrides}


class ScriptedModel:
    """Returns scripted assistant text for deterministic fake scenarios."""

    def __init__(self, outputs: tuple[str, ...]) -> None:
        self._outputs = outputs
        self._index = 0
        self.requests: list[object] = []

    def respond(self, request: object) -> str:
        self.requests.append(request)
        if self._index < len(self._outputs):
            output = self._outputs[self._index]
            self._index += 1
            return output
        return "Simulated response."


class ScriptedReckoningProvider:
    """Returns a scripted ReckoningDraft for deterministic fake scenarios."""

    def __init__(self, draft: dict[str, Any] | None) -> None:
        self._draft = draft

    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        del unstructured_input
        if self._draft is None:
            return _default_reckoning_draft()
        return _draft_from_dict(self._draft)


class _ScriptedSetupUI:
    """Drive the supported setup UI seam while retaining rendered evidence."""

    def __init__(self, answers: list[tuple[str, str]]) -> None:
        self._answers = list(answers)
        self.lines: list[str] = []

    def _next(self, key: str) -> str:
        if not self._answers:
            raise RuntimeError(f"Setup requested an unexpected answer for {key!r}.")
        expected, value = self._answers.pop(0)
        if expected != key:
            raise RuntimeError(
                f"Setup requested {key!r}; the scenario expected {expected!r}."
            )
        return value

    def banner(self) -> None:
        self.lines.append("setup")

    def step(self, index: int, total: int, title: str) -> None:
        self.lines.append(f"step {index}/{total}: {title}")

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
        del options, allow_back, help_text
        self.lines.append(prompt)
        return self._next(key)

    def ask(
        self,
        key: str,
        prompt: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = True,
    ) -> str:
        del default, secret, allow_empty
        self.lines.append(prompt)
        return self._next(key)

    def confirm(self, key: str, question: str, *, default: bool = False) -> bool:
        del default
        self.lines.append(question)
        return self._next(key).casefold() in {"y", "yes", "true", "1"}


def _default_reckoning_draft() -> ReckoningDraft:
    evidence = Evidence(id="msg", source="current user message", content="")
    return ReckoningDraft(
        conflict="The user described a conflict.",
        questions=(),
        matters_now=("Identify the nearest irreversible consequence.",),
        maintained=("Keep the smallest necessary maintenance.",),
        parked=("Park work with no current consequence.",),
        uncertainties=("Deadlines and available time are not yet known.",),
        known=(SourcedFact(text="The user described competing concerns.", evidence_ids=("msg",)),),
        inferences=(
            Inference(
                text="The concerns may compete for the same capacity.",
                evidence_ids=("msg",),
                uncertainty="This is an inference until confirmed.",
            ),
        ),
        evidence=(evidence,),
        next_step="Name the nearest irreversible consequence.",
    )


def _draft_from_dict(data: dict[str, Any]) -> ReckoningDraft:
    return ReckoningDraft(
        conflict=str(data["conflict"]),
        questions=tuple(
            MaterialQuestion(str(q["text"]), str(q["effect_on_recommendation"]))
            for q in data.get("questions", [])
        ),
        matters_now=tuple(str(x) for x in data.get("matters_now", [])),
        maintained=tuple(str(x) for x in data.get("maintained", [])),
        parked=tuple(str(x) for x in data.get("parked", [])),
        uncertainties=tuple(str(x) for x in data.get("uncertainties", [])),
        known=tuple(
            SourcedFact(str(f["text"]), tuple(str(x) for x in f.get("evidence_ids", [])))
            for f in data.get("known", [])
        ),
        inferences=tuple(
            Inference(
                str(i["text"]),
                tuple(str(x) for x in i.get("evidence_ids", [])),
                str(i["uncertainty"]),
            )
            for i in data.get("inferences", [])
        ),
        evidence=tuple(
            Evidence(str(e["id"]), str(e["source"]), str(e["content"]))
            for e in data.get("evidence", [])
        ),
        next_step=str(data["next_step"]),
        proposed_records=tuple(
            PersonalRecordProposal(
                cast(PersonalRecordType, str(r["record_type"])),
                str(r["meaning"]),
                tuple(str(x) for x in r.get("evidence_ids", [])),
            )
            for r in data.get("proposed_records", [])
        ),
    )


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[ParsedForm] = []
        self.csrf_token = ""
        self._current_form: ParsedForm | None = None
        self._textarea_name = ""
        self._textarea_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._current_form = ParsedForm(action=values.get("action") or "")
        elif tag == "input":
            name = values.get("name")
            if name == "_csrf_token":
                self.csrf_token = values.get("value") or ""
            if self._current_form is not None and name:
                self._current_form.fields[name] = values.get("value") or ""
        elif tag == "textarea" and self._current_form is not None:
            self._textarea_name = values.get("name") or ""
            self._textarea_chunks = []

    def handle_data(self, data: str) -> None:
        if self._current_form is not None and self._textarea_name:
            self._textarea_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self._current_form is not None:
            self._current_form.textareas[self._textarea_name] = "".join(
                self._textarea_chunks
            )
            self._textarea_name = ""
        elif tag == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None

    def form_by_action(self, action: str) -> ParsedForm:
        return next(form for form in self.forms if form.action == action)

    def forms_ending(self, suffix: str) -> list[ParsedForm]:
        return [form for form in self.forms if form.action.endswith(suffix)]


@dataclass
class _BrowserSession:
    web: ReckoningWebApplication
    origin: str = "http://127.0.0.1:8000"
    cookie: str = ""
    csrf_token: str = ""

    def get(self, path: str) -> tuple[str, dict[str, str], bytes]:
        status, header_pairs, body = self._raw_request("GET", path)
        headers = dict(header_pairs)
        if "Set-Cookie" in headers:
            self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        parser = _PageParser()
        parser.feed(body.decode("utf-8"))
        if parser.csrf_token:
            self.csrf_token = parser.csrf_token
        return status, headers, body

    def parse(self, body: bytes) -> _PageParser:
        parser = _PageParser()
        parser.feed(body.decode("utf-8"))
        return parser

    def post(
        self, path: str, form: dict[str, str] | None = None
    ) -> tuple[str, dict[str, str], bytes]:
        fields = {"_csrf_token": self.csrf_token, **(form or {})}
        status, header_pairs, body = self._raw_request(
            "POST",
            path,
            form=fields,
            environ_overrides={
                "HTTP_ORIGIN": self.origin,
                "HTTP_SEC_FETCH_SITE": "same-origin",
            },
        )
        return status, dict(header_pairs), body

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        form: dict[str, str] | None = None,
        environ_overrides: dict[str, object] | None = None,
    ) -> tuple[str, list[tuple[str, str]], bytes]:
        body = urlencode(form or {}).encode("utf-8")
        captured_status = ""
        captured_headers: list[tuple[str, str]] = []

        def start_response(
            status: str,
            headers: list[tuple[str, str]],
            exc_info: object | None = None,
        ) -> Callable[[bytes], object]:
            del exc_info
            nonlocal captured_status, captured_headers
            captured_status = status
            captured_headers = headers
            return lambda _chunk: None

        environ: dict[str, object] = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "wsgi.input": BytesIO(body),
            "wsgi.url_scheme": "http",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8000",
            "HTTP_HOST": "127.0.0.1:8000",
            "REMOTE_ADDR": "127.0.0.1",
        }
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie
        if environ_overrides:
            environ.update(environ_overrides)
        response: Iterable[bytes] = self.web(environ, start_response)
        return captured_status, captured_headers, b"".join(response)


def build_evaluation_web_app(
    data_dir: Path,
    *,
    activation: dict[str, Any] | None = None,
    provider_name: str | None = None,
    scripted_model_outputs: tuple[str, ...] | None = None,
    scripted_reckoning: dict[str, Any] | None = None,
    transport: Callable[[Request, float], bytes] | None = None,
    credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    initialize: bool = True,
    scripted_model_override: ScriptedModel | None = None,
) -> ReckoningWebApplication:
    """Set up a temporary installation and return a wired web application."""
    from reckoning.application import create_local_application
    from reckoning.provider_adapters import urlopen_transport

    fake_activation = {
        "status": "activated",
        "provider": "fake",
        "model": "deterministic-fake",
        "context_window": 8192,
        "demo": True,
    }
    selected_activation = activation if activation is not None else fake_activation
    selected_provider_name = str(provider_name or selected_activation.get("provider", "fake"))

    if initialize:
        setup_instance(
            data_dir,
            "local",
            first_conversation=("Hello, Simon.", "Hello. What is on your mind?"),
            activation=selected_activation,
        )
    runtime = load_installation_runtime(data_dir)
    provider = RuntimeProviderSettings.load(
        data_dir,
        credentials_path=credentials_path,
        provider_name=selected_provider_name,
    )

    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        provider_name=provider.provider_name,
        provider_config=AdapterConfig(
            api_key=provider.api_key,
            model=provider.model,
            base_url=provider.base_url,
            protocol=provider.protocol,
            context_window=provider.context_window,
            headers=provider.headers,
        ),
        provider_transport=transport or urlopen_transport,
        persona=runtime.persona,
        placement=runtime.application_placement,
        model_override=(
            scripted_model_override or ScriptedModel(scripted_model_outputs or ())
            if scripted_model_outputs is not None or scripted_reckoning is not None
            else None
        ),
        reckoning_provider_override=(
            ScriptedReckoningProvider(scripted_reckoning)
            if scripted_model_outputs is not None or scripted_reckoning is not None
            else None
        ),
    )
    interface = create_local_interface_application(
        application,
        runtime.state_path("confirmed-state", "interfaces.json"),
        placement=runtime.interface_placement,
        connector_data_dir=runtime.root_for("approved-remote-sources"),
    )
    return ReckoningWebApplication(
        application,
        interface_application=interface,
        allowed_origins=local_browser_origins(8000),
    )


class WebEvaluationDriver:
    """Drive one scenario through the real web application."""

    def __init__(
        self,
        data_dir: Path,
        *,
        activation: dict[str, Any] | None = None,
        provider_name: str | None = None,
        scripted_model_outputs: tuple[str, ...] | None = None,
        scripted_reckoning: dict[str, Any] | None = None,
        transport: Callable[[Request, float], bytes] | None = None,
        credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    ) -> None:
        self._data_dir = data_dir
        self._activation = activation
        self._provider_name = provider_name
        self._scripted_model_outputs = scripted_model_outputs
        self._scripted_reckoning = scripted_reckoning
        self._transport = transport
        self._credentials_path = credentials_path
        self._scripted_model = (
            ScriptedModel(scripted_model_outputs or ())
            if scripted_model_outputs is not None or scripted_reckoning is not None
            else None
        )
        self._web = build_evaluation_web_app(
            data_dir,
            activation=activation,
            provider_name=provider_name,
            scripted_model_outputs=scripted_model_outputs,
            scripted_reckoning=scripted_reckoning,
            transport=transport,
            credentials_path=credentials_path,
            scripted_model_override=self._scripted_model,
        )
        self._browser = _BrowserSession(self._web)
        self._last_reckoning: Reckoning | None = None
        self._last_record_id: str | None = None
        self._observed: list[str] = []
        self._assistant_text: list[str] = []
        self._missing: list[str] = []
        self._composers_by_revision: dict[int, ParsedForm] = {}
        self._state_transitions: list[dict[str, Any]] = []
        self._product_checks: list[dict[str, str]] = []
        self._last_setup_render = ""
        self._private_markers: set[str] = set()
        self._terminal_outputs: list[str] = []
        self._network_requests = 0

    @property
    def application(self) -> ReckoningApplication:
        return self._web.application

    def _check(self, name: str, passed: bool, detail: str) -> None:
        self._product_checks.append(
            {"name": name, "status": "passed" if passed else "failed", "detail": detail}
        )

    def _setup_paths(self) -> SetupPaths:
        prefix = self._data_dir.name
        return SetupPaths(
            data_dir=self._data_dir,
            credentials_path=self._data_dir.parent / f".{prefix}-provider.json",
            telegram_config_path=self._data_dir.parent / f".{prefix}-telegram.json",
            draft_path=self._data_dir.parent / f".{prefix}-setup-draft.json",
        )

    def _run_setup(self, answers: list[tuple[str, str]]) -> str:
        ui = _ScriptedSetupUI(answers)
        workflow = SetupWorkflow(
            paths=self._setup_paths(),
            ui=ui,
            services=SetupServices(environ={}, probe=lambda _url: False),
        )
        workflow.run()
        rendered = "\n".join(ui.lines)
        self._last_setup_render = rendered
        return rendered

    def import_persona(
        self,
        *,
        private_identifier: str,
        display_name: str,
        declared_version: str,
        private_guidance: str,
        stable_identity: str,
        expression_persona: str,
    ) -> None:
        source_dir = self._data_dir.parent / f".{self._data_dir.name}-persona-input"
        source_dir.mkdir(parents=True, exist_ok=True)
        values = {
            "guidance": private_guidance,
            "identity": stable_identity,
            "expression": expression_persona,
        }
        paths: dict[str, Path] = {}
        for role, content in values.items():
            path = source_dir / f"{role}-{declared_version}.md"
            path.write_text(content, encoding="utf-8")
            paths[role] = path
            self._private_markers.add(content)
        rendered = self._run_setup(
            [
                ("status-action", "edit"),
                ("edit-section", "persona"),
                ("persona-manage", "private-import"),
                ("persona-private-id", private_identifier),
                ("persona-private-name", display_name),
                ("persona-private-version", declared_version),
                ("persona-private-guidance-path", str(paths["guidance"])),
                ("persona-stable-identity-path", str(paths["identity"])),
                ("persona-expression-path", str(paths["expression"])),
                ("persona-private-accept", "y"),
                ("status-action", "exit"),
            ]
        )
        service = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        )
        active = service.active_compiled()
        self._check(
            f"persona-import-{declared_version}",
            active.private_identifier == private_identifier
            and active.declared_version == declared_version,
            "supported setup imported and selected the declared fictional version",
        )
        self._check(
            f"persona-import-privacy-{declared_version}",
            all(marker not in rendered for marker in values.values()),
            "setup review omitted private bundle content",
        )

    def review_persona(self, declared_version: str) -> None:
        rendered = self._last_setup_render
        required = (
            "Private guidance: selected document",
            "Stable assistant identity: selected document",
            "Expression persona: selected document",
            f"Declared version: {declared_version}",
            "Fingerprint:",
            "Model destination:",
        )
        self._check(
            f"persona-review-{declared_version}",
            all(item in rendered for item in required)
            and all(marker not in rendered for marker in self._private_markers),
            "rendered setup review showed metadata without bundle content",
        )

    def verify_persona_activation(self, declared_version: str) -> None:
        service = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        )
        active = service.active_compiled()
        self._check(
            f"persona-activation-{declared_version}",
            active.declared_version == declared_version,
            "storage read verified the setup-selected active version",
        )
        self.restart()

    def select_persona_version(self, declared_version: str) -> None:
        service = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        )
        active = service.active_compiled()
        self._check(
            f"persona-version-selection-{declared_version}",
            active.declared_version == declared_version,
            "the supported import control selected this immutable version",
        )

    def rollback_persona(self, declared_version: str) -> None:
        service = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        )
        target = next(
            item
            for item in service.list_private_versions()
            if item.declared_version == declared_version
        )
        rendered = self._run_setup(
            [
                ("status-action", "edit"),
                ("edit-section", "persona"),
                ("persona-manage", "private-rollback"),
                ("persona-private-rollback-target", target.version_id),
                ("status-action", "exit"),
            ]
        )
        active = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        ).active_compiled()
        self._check(
            f"persona-rollback-{declared_version}",
            active.declared_version == declared_version
            and "Rolled back to" in rendered,
            "supported setup rollback selected the earlier immutable version",
        )
        self.restart()

    def restart(self) -> None:
        self._web = build_evaluation_web_app(
            self._data_dir,
            activation=self._activation,
            provider_name=self._provider_name,
            scripted_model_outputs=self._scripted_model_outputs,
            scripted_reckoning=self._scripted_reckoning,
            transport=self._transport,
            credentials_path=self._credentials_path,
            initialize=False,
            scripted_model_override=self._scripted_model,
        )
        self._browser = _BrowserSession(self._web)
        runtime = load_installation_runtime(self._data_dir)
        self._check(
            "restart",
            bool(runtime.persona.name),
            "a fresh runtime reopened the installed persona",
        )

    def backup_and_restore(self) -> None:
        archive = self._data_dir.with_name(f"{self._data_dir.name}-backup.reckoning")
        restored = self._data_dir.with_name(f"{self._data_dir.name}-restored")
        create_transfer(
            self._data_dir,
            archive,
            "fictional-evaluation-passphrase",
            kind="backup",
        )
        restore_transfer(
            archive,
            restored,
            "fictional-evaluation-passphrase",
        )
        before = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        ).active_compiled()
        after = PersonaService(
            JsonFilePersonaRepository(restored / "personas.json")
        ).active_compiled()
        self._check(
            "backup-restore",
            before.private_identifier == after.private_identifier
            and before.declared_version == after.declared_version,
            "encrypted backup and clean restore preserved the active persona version",
        )
        self._data_dir = restored
        self.restart()

    def send_terminal_message(self, text: str) -> None:
        replies = iter([text, "/exit"])
        output: list[str] = []
        result = terminal_main(
            [
                "--data-dir",
                str(self._data_dir),
                "--credentials",
                str(self._setup_paths().credentials_path),
            ],
            line_reader=lambda _prompt: next(replies),
            output=output.append,
        )
        self._terminal_outputs.extend(output)
        repository = JsonFileInterfaceRepository(
            load_installation_runtime(self._data_dir).state_path(
                "confirmed-state", "interfaces.json"
            )
        )
        sessions = repository.list_sessions("terminal")
        self._check(
            "terminal-entry",
            result == 0
            and any(message.content == text for session in sessions for message in session.messages),
            "the supported terminal entry path persisted the fictional message",
        )
        self._observed.extend(f"terminal: {line}" for line in output)

    def verify_runtime_contract(self) -> None:
        runtime = load_installation_runtime(self._data_dir)
        active = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        ).active_compiled()
        self._check(
            "setup-runtime-parity",
            active.instructions == runtime.persona.instructions,
            "setup-selected and reopened runtime instructions match",
        )
        persona_state = read_json(self._data_dir / "personas.json", default={})
        self._check(
            "persona-migration-current",
            persona_state.get("schema_version") == 3,
            "the supported setup path left persona state on schema version 3",
        )

        repository = JsonFileInterfaceRepository(
            runtime.state_path("confirmed-state", "interfaces.json")
        )
        first_selection = repository.get_selected_session("web")
        if first_selection is None:
            raise RuntimeError("The rendered web journey did not create a session.")
        self._browser.get("/simon")
        status, _, _ = self._browser.post(
            "/sessions/new", {"display_name": "Fictional second thread"}
        )
        second_selection = repository.get_selected_session("web")
        self._check(
            "rendered-session-create",
            status.startswith("303")
            and second_selection is not None
            and second_selection.selected_session_id
            != first_selection.selected_session_id,
            "the rendered new-session control selected a distinct session",
        )
        _, _, body = self._browser.get("/simon")
        parser = self._browser.parse(body)
        select_form = next(
            form
            for form in parser.forms
            if form.action == "/sessions/select"
            and form.fields.get("session_id") == first_selection.selected_session_id
        )
        status, _, _ = self._browser.post(
            "/sessions/select", select_form.submission()
        )
        selected_again = repository.get_selected_session("web")
        self._check(
            "rendered-session-select",
            status.startswith("303")
            and selected_again is not None
            and selected_again.selected_session_id
            == first_selection.selected_session_id,
            "the rendered resume control restored the chosen session",
        )

        proposed_meaning = "Fictional confirmed context marker."
        self._run_setup(
            [
                ("status-action", "edit"),
                ("edit-section", "profile"),
                ("profile", "guided"),
                ("profile-address", proposed_meaning),
                ("profile-work", ""),
                ("profile-priorities", ""),
                ("profile-preferences", ""),
                ("profile-boundaries", ""),
                ("status-action", "exit"),
            ]
        )
        self.restart()
        self.send_message("What do you know about the fictional confirmed context marker?")
        before_request = (
            self._scripted_model.requests[-1] if self._scripted_model and self._scripted_model.requests else None
        )
        before_text = "\n".join(
            str(message.content)
            for message in getattr(
                getattr(before_request, "provider_conversation", None),
                "messages",
                (),
            )
        )
        _, _, profile_body = self._browser.get("/simon")
        profile_parser = self._browser.parse(profile_body)
        confirm_form = next(
            form
            for form in profile_parser.forms
            if form.action.startswith("/profile/")
            and form.action.endswith("/confirm")
        )
        confirm_status, _, _ = self._browser.post(
            confirm_form.action, confirm_form.submission()
        )
        self.send_message("What do you know about the fictional confirmed context marker?")
        after_request = (
            self._scripted_model.requests[-1] if self._scripted_model and self._scripted_model.requests else None
        )
        after_text = "\n".join(
            str(message.content)
            for message in getattr(
                getattr(after_request, "provider_conversation", None),
                "messages",
                (),
            )
        )
        self._check(
            "confirmed-only-context",
            bool(proposed_meaning)
            and confirm_status.startswith("303")
            and proposed_meaning not in before_text
            and proposed_meaning in after_text,
            "provider requests excluded proposed context before confirmation="
            f"{proposed_meaning not in before_text}; included after confirmation="
            f"{proposed_meaning in after_text}",
        )

        self._browser.get("/simon")
        self._browser.post(
            "/sessions/new", {"display_name": "Fictional bounded history"}
        )
        for index in range(10):
            self.send_message(f"history-{index} " + ("x" * 5000))
        runs = self.application.inspect_model_runs()
        selection = runs[-1].history_selection if runs else None
        _, _, body = self._browser.get("/simon")
        rendered = body.decode("utf-8", errors="replace")
        selected = repository.get_selected_session("web")
        session = (
            next(
                (
                    item
                    for item in repository.list_sessions("web")
                    if item.session_id == selected.selected_session_id
                ),
                None,
            )
            if selected is not None
            else None
        )
        self._check(
            "bounded-history",
            selection is not None
            and getattr(selection, "omitted_turn_count", 0) > 0,
            "provider request omitted complete earlier turns="
            f"{getattr(selection, 'omitted_turn_count', 0)}",
        )
        self._check(
            "notice-separation",
            "Limited context:" in rendered
            and session is not None
            and all("Limited context:" not in item.content for item in session.messages),
            "the rendered limitation stayed outside assistant speech and stored history",
        )

    def verify_processing_grant_denial(self, text: str) -> None:
        from reckoning.application import create_local_application

        runtime = load_installation_runtime(self._data_dir)
        model = ScriptedModel(("This response must never be used.",))
        application = create_local_application(
            runtime.state_path("confirmed-state", "denial-continuity.json"),
            personal_context_path=runtime.state_path(
                "personal-context", "personal-context.json"
            ),
            provider_name="deepseek",
            provider_config=AdapterConfig(
                api_key="fictional-not-used",
                model="fictional-model",
                base_url="https://fictional.invalid/v1",
            ),
            persona=runtime.persona,
            placement=runtime.application_placement,
            processing_grants_path=runtime.state_path(
                "confirmed-state", "evaluation-empty-grants.json"
            ),
            model_override=model,
        )
        interface = create_local_interface_application(
            application,
            runtime.state_path("confirmed-state", "denial-interfaces.json"),
            placement=runtime.interface_placement,
        )
        web = ReckoningWebApplication(
            application,
            interface_application=interface,
            allowed_origins=local_browser_origins(8000),
        )
        browser = _BrowserSession(web)
        _, _, body = browser.get("/simon")
        form = browser.parse(body).form_by_action("/messages")
        status, _, response = browser.post(
            "/messages", form.submission(message=text)
        )
        rendered = response.decode("utf-8", errors="replace")
        denied = not model.requests and (
            "private persona processing is blocked" in rendered.casefold()
            or not status.startswith("303")
        )
        self._check(
            "processing-grant-denial",
            denied,
            "rendered message submission stopped before model execution",
        )

    def send_message(self, text: str) -> None:
        self._browser.get("/simon")
        composer = self._current_composer()
        submission = composer.submission(message=text)
        before_revision = (
            self._last_reckoning.version if self._last_reckoning is not None else None
        )
        status, headers, _ = self._browser.post(
            "/messages", submission
        )
        if self._last_reckoning is not None:
            self._last_reckoning = self.application.inspect_reckoning(
                self._last_reckoning.id
            )
        self._record_state_transition(
            "message",
            submission,
            before_revision=before_revision,
            response_status=status,
        )
        self._observed.append(f"message status={status} redirect={headers.get('Location', '')}")
        if not status.startswith("303"):
            self._assistant_text.append("")
            return
        location = headers.get("Location", "")
        if location.startswith("/decisions/"):
            decision_status, _, _ = self._browser.get(location)
            self._observed.append(f"decision-follow status={decision_status}")
        simon_status, _, body = self._browser.get("/simon")
        self._observed.append(f"simon status={simon_status}")
        assistant = self._extract_assistant_text(body)
        self._assistant_text.append(assistant)
        if assistant:
            self._observed.append(assistant)
        self._check(
            "rendered-web-message",
            status.startswith("303") and bool(assistant),
            "the rendered composer submitted and displayed an assistant reply",
        )

    def start_reckoning(self, text: str) -> None:
        self._browser.get("/simon")
        composer = self._current_composer()
        submission = composer.submission(message=text)
        status, headers, _ = self._browser.post(
            "/decisions", submission
        )
        self._observed.append(f"reckon status={status} redirect={headers.get('Location', '')}")
        if status.startswith("303") and headers.get("Location", "").startswith("/decisions/"):
            decision_id = headers["Location"].rsplit("/", 1)[-1]
            self._last_reckoning = self.application.inspect_reckoning(decision_id)
            if self._last_reckoning.current_records:
                self._last_record_id = self._last_reckoning.current_records[0].record_id
            self._observed.append(
                f"Reckoning {self._last_reckoning.id} "
                f"status={self._last_reckoning.status} "
                f"version={self._last_reckoning.version}"
            )
            self._capture_proposal_composer()
        else:
            self._last_reckoning = None
            self._last_record_id = None
        self._record_state_transition(
            "reckon",
            submission,
            before_revision=None,
            response_status=status,
        )

    def correct_record(self, text: str) -> None:
        """Correct through the rendered /messages composer, not a direct form."""
        if self._last_reckoning is None or self._last_record_id is None:
            raise RuntimeError("correct step requires a preceding reckon step.")
        decision_id = self._last_reckoning.id
        composer = self._composer_for_current_revision()
        if composer is None:
            self._observed.append("No rendered proposal composer found")
            return
        before_revision = self._last_reckoning.version
        submission = composer.submission(message=f"No, correct it: {text}")
        status, headers, _ = self._browser.post(
            "/messages",
            submission,
        )
        self._observed.append(
            f"correct status={status} redirect={headers.get('Location', '')}"
        )
        if status.startswith("303") and headers.get("Location", "").startswith("/decisions/"):
            self._last_reckoning = self.application.inspect_reckoning(decision_id)
            self._observed.append(
                f"Corrected {self._last_record_id} to version "
                f"{self._last_reckoning.current_records[0].version}"
            )
            self._capture_proposal_composer()
        else:
            self._observed.append(
                "Correction not applied: the handler asked for review"
            )
        self._record_state_transition(
            "correct",
            submission,
            before_revision=before_revision,
            response_status=status,
        )

    def confirm_reckoning(self, expected_revision: int | None = None) -> None:
        """Confirm through the rendered /messages composer.

        When expected_revision names a rendered revision, the form captured for
        that revision is submitted as-is. A stale form is sent to the real
        handler; the driver never rejects it before the application sees it.
        """
        if self._last_reckoning is None:
            raise RuntimeError("confirm step requires a preceding reckon step.")
        decision_id = self._last_reckoning.id
        if expected_revision is not None:
            composer = self._composers_by_revision.get(expected_revision)
            if composer is None:
                self._observed.append(
                    f"No rendered composer for revision {expected_revision}"
                )
                return
        else:
            composer = self._composer_for_current_revision()
            if composer is None:
                self._observed.append("No rendered proposal composer found")
                return
        before_revision = self._last_reckoning.version
        submission = composer.submission(message="I confirm this version")
        status, headers, _ = self._browser.post(
            "/messages", submission
        )
        self._observed.append(
            f"confirm status={status} redirect={headers.get('Location', '')}"
        )
        self._last_reckoning = self.application.inspect_reckoning(decision_id)
        if self._last_reckoning.status == "confirmed":
            self._observed.append(f"Confirmed version {self._last_reckoning.version}")
        else:
            self._observed.append(
                f"Confirm rejected: status={status} "
                f"redirect={headers.get('Location', '')}"
            )
        self._record_state_transition(
            "confirm",
            submission,
            before_revision=before_revision,
            response_status=status,
        )

    def _record_state_transition(
        self,
        action: str,
        submission: dict[str, str],
        *,
        before_revision: int | None,
        response_status: str,
    ) -> None:
        operation_id = submission.get("operation_id", "")
        receipt = self.application.inspect_operation(operation_id) if operation_id else None
        receipt_evidence = None
        if receipt is not None:
            receipt_evidence = {
                "operation_id": receipt.operation_id,
                "status": receipt.status,
                "result_id": receipt.result_id,
                "kind": receipt.kind,
                "target_id": receipt.target_id,
                "displayed_revision": receipt.displayed_revision,
            }
        self._state_transitions.append(
            {
                "action": action,
                "operation_id": operation_id,
                "rendered_binding_present": bool(
                    submission.get("decision_presentation_token")
                ),
                "before_revision": before_revision,
                "after_revision": (
                    self._last_reckoning.version
                    if self._last_reckoning is not None
                    else None
                ),
                "response_status": response_status,
                "durable_receipt": receipt_evidence,
            }
        )

    def _composer_for_current_revision(self) -> ParsedForm | None:
        captured = self._capture_proposal_composer()
        if captured is None:
            return None
        _revision, composer = captured
        return composer

    def _capture_proposal_composer(self) -> tuple[int, ParsedForm] | None:
        status, _, body = self._browser.get("/simon")
        if not status.startswith("200"):
            return None
        text = body.decode("utf-8")
        match = re.search(r"\(revision (\d+)\)", text)
        parser = _PageParser()
        parser.feed(text)
        try:
            composer = parser.form_by_action("/messages")
        except StopIteration:
            return None
        if match is None:
            return None
        revision = int(match.group(1))
        self._composers_by_revision.setdefault(revision, composer)
        return revision, composer

    def explain_reckoning(self) -> None:
        if self._last_reckoning is None:
            raise RuntimeError("explain step requires a preceding reckon step.")
        why = self.application.explain_reckoning(self._last_reckoning.id)
        self._observed.append(
            f"Why view: {len(why.evidence)} evidence, {len(why.record_versions)} records"
        )

    def record_check_in(self, outcome: str) -> None:
        self._missing.append("check_in")

    def resume_decision(self) -> None:
        self._missing.append("resume")

    def profile_reject(self) -> None:
        self._missing.append("profile fact rejection")

    def feedback(self) -> None:
        self._missing.append("reply feedback")

    def missing_journey(self, journey: str) -> None:
        """Record a journey owned by a later ticket as missing implementation."""
        self._missing.append(journey)

    def finish(self) -> tuple[str, str, dict[str, Any]]:
        if self._missing:
            raise MissingImplementationError(
                "Required operations not implemented: "
                + ", ".join(sorted(set(self._missing)))
            )
        raw_runs = self.application.inspect_model_runs()
        runs = [
            {
                "provider": run.provider,
                "model": run.model,
                "status": run.status,
                "latency_ms": run.latency_ms,
                "retries": run.retries,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "billable_units": run.billable_units,
            }
            for run in raw_runs
        ]
        reckonings = self.application.list_reckonings()
        confirmed = [r for r in reckonings if r.status == "confirmed"]
        proposed = [r for r in reckonings if r.status == "proposed"]
        last_reckoning = self._last_reckoning
        runtime = load_installation_runtime(self._data_dir)
        active_persona = PersonaService(
            JsonFilePersonaRepository(self._data_dir / "personas.json")
        ).active_compiled()
        interface_repository = JsonFileInterfaceRepository(
            runtime.state_path("confirmed-state", "interfaces.json")
        )
        selected_sessions = {
            channel: (
                selection.selected_session_id if selection is not None else None
            )
            for channel in cast(tuple[ChannelName, ...], ("web", "terminal"))
            for selection in [interface_repository.get_selected_session(channel)]
        }
        current_record_keys = {
            (record.record_id, record.version)
            for record in (
                last_reckoning.current_records if last_reckoning else ()
            )
        }
        superseded_meanings = [
            record.meaning
            for record in (last_reckoning.record_versions if last_reckoning else ())
            if (record.record_id, record.version) not in current_record_keys
        ]
        evidence: dict[str, Any] = {
            "model_run_count": len(runs),
            "model_run_records": list(runs),
            "last_run_status": runs[-1]["status"] if runs else None,
            "last_run_provider": runs[-1]["provider"] if runs else None,
            "confirmed_reckoning_count": len(confirmed),
            "proposed_reckoning_count": len(proposed),
            "last_reckoning_status": last_reckoning.status if last_reckoning else None,
            "last_reckoning_version": last_reckoning.version if last_reckoning else None,
            "last_reckoning_meanings": [
                record.meaning for record in (last_reckoning.current_records if last_reckoning else ())
            ],
            "superseded_reckoning_meanings": superseded_meanings,
            "last_reckoning_record_versions": [
                {
                    "record_id": record.record_id,
                    "version": record.version,
                    "status": record.status,
                    "meaning": record.meaning,
                    "supersedes_version": record.supersedes_version,
                }
                for record in (
                    last_reckoning.record_versions if last_reckoning else ()
                )
            ],
            "state_transitions": list(self._state_transitions),
            "product_checks": list(self._product_checks),
            "persona_identifier": active_persona.private_identifier
            or active_persona.persona_id,
            "persona_version": active_persona.declared_version,
            "session_identity": selected_sessions,
            "observed_reply": (
                self._assistant_text[-1]
                if self._assistant_text
                else next(
                    (
                        line.split(": ", 1)[1]
                        for line in reversed(self._terminal_outputs)
                        if ": " in line and not line.startswith("Notice:")
                    ),
                    "",
                )
            ),
            "network_requests": self._network_requests,
        }
        return "\n".join(self._observed), "\n".join(self._assistant_text), evidence

    def _current_composer(self) -> ParsedForm:
        status, _, body = self._browser.get("/simon")
        if not status.startswith("200"):
            raise RuntimeError(f"Could not load /simon: {status}")
        parser = _PageParser()
        parser.feed(body.decode("utf-8"))
        return parser.form_by_action("/messages")

    @staticmethod
    def _extract_assistant_text(body: bytes) -> str:
        import re

        text = body.decode("utf-8")
        # Extract the most recent assistant message from the rendered page.
        # This is a coarse heuristic for fake-mode authority scoring only.
        marker = '<strong>Simon</strong>'
        index = text.rfind(marker)
        if index == -1:
            return ""
        article_start = text.rfind('<article', 0, index)
        article_end = text.find("</article>", index)
        if article_end == -1:
            article_end = len(text)
        block = text[article_start:article_end]
        cleaned = re.sub(r"<[^>]+>", "", block)
        if cleaned.startswith("Simon"):
            cleaned = cleaned[len("Simon") :].lstrip()
        return cleaned


class MissingImplementationError(RuntimeError):
    """A scenario step requires a product operation that is not yet available."""
