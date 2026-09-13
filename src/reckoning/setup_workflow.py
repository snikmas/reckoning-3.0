"""The setup workflow: steps, transitions, drafts, validation, activation.

This module owns setup policy and never prints terminal UI directly; a
``SetupUI`` adapter owns prompts, navigation, colors, and fallbacks.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, Protocol, cast

from reckoning.config import (
    DEFAULT_PROVIDER_CREDENTIALS,
    ProviderCredentialStore,
    RuntimeProviderSettings,
    StoredCredential,
)
from reckoning.json_store import atomic_write_json, read_json
from reckoning.operations import (
    OperationError,
    load_installation_runtime,
    setup_instance,
)
from reckoning.personal_context import (
    JsonFilePersonalContextRepository,
    PersonalContextService,
    UserProfileEntry,
    read_user_profile,
)
from reckoning.personas import (
    AUTONOMY_FLOOR,
    DEFAULT_PERSONAS,
    PERSONA_AXES,
    JsonFilePersonaRepository,
    PersonaDefinition,
    PersonaService,
    blank_persona_template,
    describe_persona,
    persona_from_data,
    persona_from_preset,
)
from reckoning.provider_adapters import (
    AdapterConfig,
    ModelDiscoveryError,
    ProviderVerificationError,
    SetupProviderAdapter,
    Transport,
    probe_local_endpoint,
    setup_adapter_for,
    urlopen_transport,
)
from reckoning.provider_registry import (
    GROUP_LABELS,
    GROUP_ORDER,
    ProviderDefinition,
    coming_soon_providers,
    find_provider,
    providers_in_group,
)
from reckoning.provider_validation import is_valid_env_name, is_valid_header_name
from reckoning.runtime_status import gateway_runtime_status
from reckoning.setup_copy import (
    AUTONOMY_FLOOR_TITLE,
    CONNECTOR_COMING_SOON,
    CONNECTOR_PROMPT,
    CONNECTOR_SKIP,
    CONNECTOR_TELEGRAM,
    CONNECTOR_TITLE,
    FIRST_MESSAGE_ACTION_PROMPT,
    FIRST_MESSAGE_ACTIONS,
    FIRST_MESSAGE_INTRO,
    FIRST_MESSAGE_TITLE,
    GATEWAY_COMPLETE_RUNNING,
    GATEWAY_COMPLETE_STOPPED,
    GATEWAY_RUNNING,
    GATEWAY_STOPPED,
    INTERFACE_TERMINAL_COMPLETE,
    INTERFACE_TERMINAL_READY,
    INTERFACE_WEB_COMPLETE,
    INTERFACE_WEB_READY,
    PERSONA_AUTHOR,
    PERSONA_BLANK,
    PERSONA_BUILDER_START,
    PERSONA_MANAGE,
    PERSONA_PRESET_SIMON,
    PERSONA_PRESET_STEADY,
    PERSONA_PREVIEW_TITLE,
    PERSONA_TITLE,
    PLACEMENT_CLOUD_NOTE,
    PLACEMENT_OPTIONS,
    PLACEMENT_TITLE,
    PROFILE_GUIDED,
    PROFILE_IMPORT,
    PROFILE_IMPORT_PREVIEW,
    PROFILE_SAVED_AS_PROPOSALS,
    PROFILE_SECTIONS,
    PROFILE_SKIP,
    PROFILE_STARTER,
    PROFILE_STARTER_HELP,
    PROFILE_STARTER_TEMPLATE,
    PROFILE_TITLE,
    PROVIDER_COMING_SOON,
    PROVIDER_DEMO,
    PROVIDER_DETECTED_TITLE,
    PROVIDER_FAILURE_ACTIONS,
    PROVIDER_LIVE_SAMPLE,
    PROVIDER_LOCAL_MISSING,
    PROVIDER_MODEL_MANUAL,
    PROVIDER_MODEL_TITLE,
    PROVIDER_RECOMMENDED,
    PROVIDER_TITLE,
    PROVIDER_UNVERIFIED_SAVED,
    PROVIDER_VERIFIED,
    PROVIDER_VERIFY_CHARGE,
    PROVIDER_VERIFY_FREE,
    PROVIDER_VERIFY_INTRO,
    RESUME_EXIT,
    RESUME_RESUME,
    RESUME_START_OVER,
    RESUME_TITLE,
    REVIEW_CONFIRM,
    REVIEW_TITLE,
    START_OVER_PREVIEW,
    STATUS_EDIT,
    STATUS_EDIT_CHOICES,
    STATUS_EDIT_PROMPT,
    STATUS_EXIT,
    STATUS_LABEL_ABOUT,
    STATUS_LABEL_DRAFT,
    STATUS_LABEL_FORMAT,
    STATUS_LABEL_GATEWAY,
    STATUS_LABEL_INTERFACES,
    STATUS_LABEL_PROVIDER,
    STATUS_LABEL_STORAGE,
    STATUS_LABEL_STYLE,
    STATUS_LABEL_TELEGRAM,
    STATUS_NOTHING_FAILING,
    STATUS_PAID_REFRESH,
    STATUS_PROMPT,
    STATUS_REPAIR,
    STATUS_REPAIR_PROMPT,
    STATUS_TITLE,
    STATUS_VERIFY_ALL,
    TELEGRAM_COMPLETE,
    TELEGRAM_GATEWAY_NOTE,
    TELEGRAM_PAIRING_PROMPT,
    TELEGRAM_STATUS_LABELS,
    WELCOME,
)
from reckoning.telegram import (
    TelegramBotApi,
    TelegramBotClient,
    TelegramConnectorConfig,
    pair_telegram_owner,
    telegram_connector_status,
    verify_telegram_bot,
)

DEFAULT_DATA_DIR = Path.home() / ".local" / "state" / "reckoning"
DEFAULT_DRAFT_PATH = Path.home() / ".config" / "reckoning" / "setup-draft.json"

SETUP_DRAFT_SCHEMA = 2

SetupStep = Literal[
    "provider",
    "connectors",
    "persona",
    "profile",
    "review",
    "first-conversation",
]

GUIDED_STEPS: tuple[SetupStep, ...] = (
    "provider",
    "connectors",
    "persona",
    "profile",
    "review",
    "first-conversation",
)

STEP_TITLES: dict[SetupStep, str] = {
    "provider": "AI provider and model",
    "connectors": CONNECTOR_TITLE,
    "persona": "Agent style",
    "profile": PROFILE_TITLE,
    "review": REVIEW_TITLE,
    "first-conversation": FIRST_MESSAGE_TITLE,
}


class SetupBack(Exception):
    """The user asked to return to the previous step."""


class SetupExit(Exception):
    """The user asked to save a draft and leave setup."""


class SetupInputError(RuntimeError):
    """A required answer was missing or invalid for this run."""


@dataclass(frozen=True)
class MenuOption:
    id: str
    label: str
    available: bool = True
    note: str = ""
    dim: bool = False


class SetupUI(Protocol):
    """The presentation seam; providers and connectors never see this."""

    def banner(self) -> None: ...

    def step(self, index: int, total: int, title: str) -> None: ...

    def info(self, text: str) -> None: ...

    def secondary(self, text: str) -> None: ...

    def success(self, text: str) -> None: ...

    def warning(self, text: str) -> None: ...

    def failure(self, text: str) -> None: ...

    def choose(
        self,
        key: str,
        prompt: str,
        options: tuple[MenuOption, ...],
        *,
        allow_back: bool = False,
        help_text: str | None = None,
    ) -> str: ...

    def ask(
        self,
        key: str,
        prompt: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = True,
    ) -> str: ...

    def confirm(self, key: str, question: str, *, default: bool = False) -> bool: ...


@dataclass
class SetupDraft:
    """A resumable, non-secret record of an interrupted setup."""

    completed: list[str] = field(default_factory=list)
    placement: str | None = "local"
    server_data_dir: str | None = None
    persona_id: str | None = None
    authored_persona: dict[str, Any] | None = None
    provider_id: str | None = None
    provider_model: str | None = None
    provider_base_url: str | None = None
    provider_protocol: str = "openai-chat-completions"
    provider_context_window: int | None = None
    provider_header_env: dict[str, str] = field(default_factory=dict)
    provider_credential: str | None = None
    provider_demo: bool = False
    profile_choice: str | None = None
    profile_proposal_count: int = 0
    profile_entries: list[dict[str, str]] = field(default_factory=list)
    telegram_status: str | None = None
    provider_verified_at: str | None = None
    status: Literal["incomplete"] = "incomplete"
    next_step: SetupStep | None = None
    schema_version: int = SETUP_DRAFT_SCHEMA

    def mark_completed(self, step: SetupStep) -> None:
        if step not in self.completed:
            self.completed.append(step)

    @classmethod
    def load(cls, path: Path) -> SetupDraft | None:
        data = read_json(path, default={})
        if not data:
            return None
        schema_version = data.get("schema_version")
        if schema_version not in (1, SETUP_DRAFT_SCHEMA):
            raise OperationError("The setup draft uses an unsupported schema.")
        if data.get("status", "incomplete") != "incomplete":
            raise OperationError("The setup draft has an invalid status.")
        completed = data.get("completed", [])
        next_step = data.get("next_step")
        header_env = data.get("provider_header_env", {})
        legacy_steps = frozenset(
            ("mode", "placement", "persona", "provider", "profile", "connectors", "first-conversation", "review")
        )
        all_steps = legacy_steps if schema_version == 1 else frozenset(GUIDED_STEPS)
        if not isinstance(completed, list) or any(
            not isinstance(step, str) or step not in all_steps for step in completed
        ):
            raise OperationError("The setup draft has invalid completed steps.")
        if next_step is not None and next_step not in all_steps:
            raise OperationError("The setup draft has an invalid next step.")
        migrated_completed = [
            step
            for step in completed
            if step in GUIDED_STEPS and step not in ("review", "first-conversation")
        ] if schema_version == 1 else completed
        if not isinstance(header_env, dict) or any(
            not is_valid_header_name(name) or not is_valid_env_name(env_name)
            for name, env_name in header_env.items()
        ):
            raise OperationError("The setup draft has invalid header references.")
        raw_context_window = data.get("provider_context_window")
        try:
            context_window = (
                int(raw_context_window) if raw_context_window is not None else None
            )
        except (TypeError, ValueError) as error:
            raise OperationError(
                "The setup draft has an invalid context size."
            ) from error
        if context_window is not None and context_window <= 0:
            raise OperationError("The setup draft has an invalid context size.")
        protocol = str(data.get("provider_protocol", "openai-chat-completions"))
        if protocol != "openai-chat-completions":
            raise OperationError("The setup draft has an invalid provider protocol.")
        return cls(
            completed=migrated_completed,
            placement=data.get("placement") or "local",
            server_data_dir=data.get("server_data_dir"),
            persona_id=data.get("persona_id"),
            authored_persona=data.get("authored_persona"),
            provider_id=data.get("provider_id"),
            provider_model=data.get("provider_model"),
            provider_base_url=data.get("provider_base_url"),
            provider_protocol=protocol,
            provider_context_window=context_window,
            provider_header_env=header_env,
            provider_credential=data.get("provider_credential"),
            provider_demo=bool(data.get("provider_demo", False)),
            profile_choice=data.get("profile_choice"),
            profile_proposal_count=int(data.get("profile_proposal_count", 0)),
            profile_entries=list(data.get("profile_entries", [])),
            telegram_status=data.get("telegram_status"),
            provider_verified_at=data.get("provider_verified_at"),
            status="incomplete",
            next_step=(
                None
                if schema_version == 1
                else cast(SetupStep | None, next_step)
            ),
            schema_version=SETUP_DRAFT_SCHEMA,
        )

    def save(self, path: Path) -> None:
        self.next_step = next(
            (step for step in GUIDED_STEPS if step not in self.completed),
            None,
        )
        atomic_write_json(path, {"schema_version": self.schema_version, **{
            key: value
            for key, value in asdict(self).items()
            if key != "schema_version"
        }})
        path.chmod(0o600)

    def summary_lines(self) -> tuple[str, ...]:
        done = ", ".join(self.completed) or "none"
        remaining = [
            step
            for step in GUIDED_STEPS
            if step not in self.completed
        ]
        about = (
            f"{self.profile_proposal_count} unconfirmed proposals"
            if self.profile_proposal_count
            else self.profile_choice or "not started"
        )
        return (
            f"Completed sections: {done}",
            f"Remaining sections: {', '.join(remaining) or 'none'}",
            f"{STATUS_LABEL_STORAGE}: {self.placement or 'not chosen'}",
            f"{STATUS_LABEL_STYLE}: {self.persona_id or 'not chosen'}",
            f"{STATUS_LABEL_PROVIDER}: {self.provider_id or 'not chosen'}",
            f"{STATUS_LABEL_ABOUT}: {about}",
            f"{STATUS_LABEL_TELEGRAM}: {self.telegram_status or 'not started'}",
        )


@dataclass(frozen=True)
class SetupPaths:
    data_dir: Path = DEFAULT_DATA_DIR
    server_data_dir: Path | None = None
    credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS
    telegram_config_path: Path = field(
        default_factory=lambda: Path.home() / ".config" / "reckoning" / "telegram.json"
    )
    draft_path: Path = DEFAULT_DRAFT_PATH


@dataclass(frozen=True)
class SetupOutcome:
    status: Literal["activated", "draft", "managed"]
    provider_id: str | None = None
    model: str | None = None
    demo: bool = False
    persona_id: str | None = None
    telegram_status: str | None = None
    data_dir: Path | None = None


class SetupResponder(Protocol):
    """One setup-mode conversation turn; no tools, writes, or persistence."""

    def respond(self, text: str) -> str: ...


@dataclass
class SetupServices:
    """External seams; tests stub every one of them."""

    environ: Mapping[str, str] | None = None
    adapter_for: Callable[[str], SetupProviderAdapter] = setup_adapter_for
    transport: Transport = urlopen_transport
    probe: Callable[[str], bool] = probe_local_endpoint
    telegram_api_factory: Callable[[str], TelegramBotClient] | None = None
    telegram_pairing_code: str | None = None
    telegram_max_polls: int = 12
    responder_for: Callable[
        [PersonaDefinition, str, AdapterConfig], SetupResponder
    ] | None = None

    def api_factory(self) -> Callable[[str], TelegramBotClient]:
        return self.telegram_api_factory or TelegramBotApi

    def responder(
        self,
        persona: PersonaDefinition,
        provider_id: str,
        config: AdapterConfig,
    ) -> SetupResponder:
        if self.responder_for is not None:
            return self.responder_for(persona, provider_id, config)
        return default_setup_responder(
            persona, provider_id, config, transport=self.transport
        )


class _FakeSetupResponder:
    def respond(self, text: str) -> str:
        return f'Reckoning received your message: "{text.strip()}"'


class _ProviderSetupResponder:
    """Real provider + real persona; tools and memory confirmation disabled."""

    def __init__(
        self,
        persona: PersonaDefinition,
        adapter: SetupProviderAdapter,
        config: AdapterConfig,
        transport: Transport,
    ) -> None:
        self._persona = persona
        self._adapter = adapter
        self._config = config
        self._transport = transport

    def respond(self, text: str) -> str:
        from reckoning.personas import SelectedPersona

        contract = (
            "Be truthful and preserve the user's final authority. "
            "Challenge choices and reasoning, never personal worth."
        )
        identity = (
            "Reckoning is one accountable personal agent. The selected "
            "persona is only its style expression."
        )
        persona = SelectedPersona(self._persona).prompt_instructions
        system = f"{contract}\n\n{identity}\n\n{persona}"
        completion = self._adapter.complete(
            self._config,
            (("system", system), ("user", text.strip())),
            transport=self._transport,
        )
        return completion.content


def default_setup_responder(
    persona: PersonaDefinition,
    provider_id: str,
    config: AdapterConfig,
    *,
    transport: Transport = urlopen_transport,
) -> SetupResponder:
    if provider_id == "fake":
        return _FakeSetupResponder()
    return _ProviderSetupResponder(
        persona, setup_adapter_for(provider_id), config, transport
    )


def _slugify(text: str) -> str:
    cleaned = "".join(
        char if char.isalnum() else "-" for char in text.strip().casefold()
    )
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts) or "persona"


@dataclass(frozen=True)
class SectionStatus:
    key: str
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    content: bytes | None
    mode: int | None

    @classmethod
    def capture(cls, path: Path) -> _FileSnapshot:
        if not path.exists():
            return cls(path, None, None)
        return cls(path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode))

    def restore(self) -> None:
        if self.content is None:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.rollback-",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(self.content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
            if self.mode is not None:
                self.path.chmod(self.mode)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _EmptyRootSnapshot:
    path: Path
    existed: bool
    mode: int | None

    @classmethod
    def capture(cls, path: Path) -> _EmptyRootSnapshot:
        if path.exists() and (
            not path.is_dir() or path.is_symlink() or any(path.iterdir())
        ):
            raise OperationError(
                f"Activation requires an empty installation root: {path}"
            )
        return cls(
            path,
            path.exists(),
            stat.S_IMODE(path.stat().st_mode) if path.exists() else None,
        )

    def restore(self) -> None:
        if self.path.exists():
            if self.path.is_dir() and not self.path.is_symlink():
                shutil.rmtree(self.path)
            else:
                self.path.unlink()
        if self.existed:
            self.path.mkdir(parents=True)
            if self.mode is not None:
                self.path.chmod(self.mode)


class SetupWorkflow:
    """One authoritative setup workflow for interactive and scripted runs."""

    def __init__(
        self,
        *,
        paths: SetupPaths,
        ui: SetupUI,
        services: SetupServices | None = None,
        preselected: Mapping[str, str] | None = None,
    ) -> None:
        self._paths = paths
        self._ui = ui
        self._services = services or SetupServices()
        self._preselected = preselected or {}
        self._store_error: RuntimeError | None = None
        try:
            self._store = ProviderCredentialStore.load(paths.credentials_path)
        except RuntimeError as error:
            self._store = ProviderCredentialStore()
            self._store_error = error
        self._draft = SetupDraft()
        self._profile_entries: list[UserProfileEntry] = []
        self._accepted: tuple[str, str] | None = None
        self._pending_key: str | None = None
        self._pending_env_name: str | None = None

    # ------------------------------------------------------------------ entry

    def run(self) -> SetupOutcome:
        self._ui.banner()
        if (self._paths.data_dir / "instance.json").exists():
            try:
                return self._run_configured()
            except SetupExit:
                return SetupOutcome(
                    status="managed", data_dir=self._paths.data_dir
                )
        if self._store_error is not None:
            raise OperationError(
                "The provider credential store is unreadable. Fix or remove "
                f"{self._paths.credentials_path} and rerun setup."
            )
        existing: SetupDraft | None = None
        if self._paths.draft_path.exists():
            raw_draft = read_json(self._paths.draft_path, default={})
            if raw_draft.get("schema_version") == 1:
                self._ui.warning(
                    "Draft migration preview: keep provider, Agent style, "
                    "About you, Telegram, and storage choices; remove the "
                    "obsolete Quick or Custom mode. The draft changes only "
                    "when setup next saves progress."
                )
            try:
                existing = SetupDraft.load(self._paths.draft_path)
            except (OperationError, RuntimeError, ValueError) as error:
                self._ui.failure(f"This setup draft cannot resume: {error}")
                self._ui.warning(f"Remove exactly: {self._paths.draft_path}")
                action = self._ui.choose(
                    "invalid-draft-action",
                    "What should Reckoning do with this draft?",
                    (
                        MenuOption("remove", "Remove only this setup draft"),
                        MenuOption("exit", "Exit without changes"),
                    ),
                )
                if action == "exit" or not self._ui.confirm(
                    "invalid-draft-remove",
                    "Remove the previewed draft?",
                    default=False,
                ):
                    return SetupOutcome(
                        status="draft", data_dir=self._paths.data_dir
                    )
                self._paths.draft_path.unlink()
                self._ui.success("The incompatible setup draft was removed.")
        if existing is not None:
            action = self._ui.choose(
                "resume",
                RESUME_TITLE,
                (
                    MenuOption("resume", RESUME_RESUME),
                    MenuOption("start-over", RESUME_START_OVER),
                    MenuOption("exit", RESUME_EXIT),
                ),
            )
            if action == "exit":
                return SetupOutcome(status="draft", data_dir=self._paths.data_dir)
            if action == "start-over":
                self._start_over(existing)
            else:
                self._draft = existing
                try:
                    self._profile_entries = [
                        UserProfileEntry(
                            record_id=item["record_id"],
                            section=item["section"],
                            text=item["text"],
                            source=item["source"],
                        )
                        for item in existing.profile_entries
                    ]
                except (KeyError, TypeError) as error:
                    raise OperationError(
                        "The setup draft contains invalid profile proposals."
                    ) from error
                for line in existing.summary_lines():
                    self._ui.info(line)
        return self._run_guided()

    def _start_over(self, draft: SetupDraft) -> None:
        for line in draft.summary_lines():
            self._ui.info(line)
        self._ui.warning(START_OVER_PREVIEW)
        remove_credentials = self._ui.confirm(
            "start-over-credentials",
            "Also remove saved provider credentials?",
            default=False,
        )
        if not self._ui.confirm(
            "start-over-confirm", "Discard the draft and start over?", default=False
        ):
            raise SetupExit
        self._paths.draft_path.unlink(missing_ok=True)
        if remove_credentials:
            self._paths.credentials_path.unlink(missing_ok=True)
            self._store = ProviderCredentialStore()
        self._draft = SetupDraft()

    # ------------------------------------------------------------- guided flow

    def _run_guided(self) -> SetupOutcome:
        try:
            self._ui.info(WELCOME)
            steps = GUIDED_STEPS
            if "placement" in self._preselected:
                chosen = self._preselected["placement"]
                server_dir = self._preselected.get("server-data-dir") or None
                if chosen not in PLACEMENT_OPTIONS:
                    raise SetupInputError(f"Unknown placement: {chosen}")
                if chosen != "local" and not server_dir:
                    raise SetupInputError(
                        f"server-data-dir is required for {chosen} placement"
                    )
                self._draft.placement = chosen
                self._draft.server_data_dir = server_dir
            elif self._draft.placement is None:
                self._draft.placement = "local"
            index = 0
            while index < len(steps):
                step = steps[index]
                if step in self._draft.completed:
                    index += 1
                    continue
                if step == "first-conversation":
                    self._ui.info(FIRST_MESSAGE_TITLE)
                else:
                    self._ui.step(index + 1, len(steps) - 1, STEP_TITLES[step])
                try:
                    self._run_step(step)
                except SetupBack:
                    if index > 0:
                        previous = steps[index - 1]
                        if previous in self._draft.completed:
                            self._draft.completed.remove(previous)
                        index -= 1
                    continue
                self._draft.mark_completed(step)
                self._save_draft()
                index += 1
            return self._activate()
        except SetupExit:
            self._save_draft()
            self._ui.info(
                "Setup is paused; the draft holds no secrets. Run "
                "reckoning setup to resume."
            )
            return SetupOutcome(
                status="draft",
                provider_id=self._draft.provider_id,
                model=self._draft.provider_model,
                demo=self._draft.provider_demo,
                persona_id=self._draft.persona_id,
                telegram_status=self._draft.telegram_status,
                data_dir=self._paths.data_dir,
            )

    def _run_step(self, step: SetupStep) -> None:
        if step == "placement":
            self._step_placement()
        elif step == "persona":
            self._step_persona()
        elif step == "provider":
            self._step_provider()
        elif step == "profile":
            self._step_profile()
        elif step == "connectors":
            self._step_connectors()
        elif step == "first-conversation":
            self._step_first_conversation()
        elif step == "review":
            self._step_review()
        else:
            raise KeyError(f"Unknown setup step: {step}")

    def _save_draft(self) -> None:
        self._draft.save(self._paths.draft_path)

    # --------------------------------------------------------------- placement

    def _step_placement(self) -> None:
        if "placement" in self._preselected:
            chosen = self._preselected["placement"]
            if chosen not in PLACEMENT_OPTIONS:
                raise SetupInputError(f"Unknown placement: {chosen}")
            preselected_dir = self._preselected.get("server-data-dir") or None
            if chosen != "local" and not preselected_dir:
                raise SetupInputError(
                    f"server-data-dir is required for {chosen} placement"
                )
            self._draft.placement = chosen
            self._draft.server_data_dir = preselected_dir
            return
        options = tuple(
            MenuOption(name, label, note=note)
            for name, (label, note) in PLACEMENT_OPTIONS.items()
        )
        chosen = self._ui.choose(
            "placement", PLACEMENT_TITLE, options, allow_back=True
        )
        if chosen not in PLACEMENT_OPTIONS:
            raise SetupInputError(f"Unknown placement: {chosen}")
        self._ui.info(PLACEMENT_OPTIONS[chosen][1])
        server_dir: str | None = None
        if chosen != "local":
            server_dir = self._ui.ask(
                "server-data-dir",
                "Personal-server data directory: ",
                default=str(self._paths.server_data_dir or ""),
                allow_empty=False,
            )
        self._draft.placement = chosen
        self._draft.server_data_dir = server_dir or None

    # ----------------------------------------------------------------- persona

    def _step_persona(self) -> None:
        if "persona" in self._preselected:
            chosen = self._preselected["persona"]
            if chosen in ("simon", "steady"):
                self._draft.persona_id = chosen
                self._draft.authored_persona = None
                return
            raise SetupInputError(
                "preselected personas must be simon or steady; author "
                "original personas interactively"
            )
        while True:
            options = [
                MenuOption("simon", PERSONA_PRESET_SIMON),
                MenuOption("steady", PERSONA_PRESET_STEADY),
                MenuOption("author", PERSONA_AUTHOR),
            ]
            if self._draft.authored_persona is not None:
                options.insert(
                    0,
                    MenuOption(
                        "draft-authored",
                        f"{self._draft.authored_persona['name']} (authored)",
                    ),
                )
            choice = self._ui.choose(
                "persona", PERSONA_TITLE, tuple(options), allow_back=True
            )
            if choice in ("simon", "steady"):
                self._draft.persona_id = choice
                self._draft.authored_persona = None
            elif choice == "draft-authored":
                authored = self._draft.authored_persona
                assert authored is not None
                self._draft.persona_id = str(authored["id"])
            elif choice == "author":
                self._author_persona()
            else:
                raise SetupInputError(f"Unknown persona choice: {choice}")
            definition = self._persona_definition()
            self._ui.info(PERSONA_PREVIEW_TITLE)
            for line in describe_persona(definition):
                self._ui.info(line)
            self._ui.info(AUTONOMY_FLOOR_TITLE)
            for line in AUTONOMY_FLOOR:
                self._ui.info(f"- {line}")
            if self._ui.confirm(
                "persona-accept",
                f"Use {definition.name} as your persona?",
                default=True,
            ):
                return

    def _author_persona(self) -> None:
        name = self._ui.ask(
            "persona-name", "Persona display name: ", allow_empty=False
        )
        persona_id = (
            self._ui.ask(
                "persona-id",
                "Persona id (lowercase slug): ",
                default=_slugify(name),
            ).strip()
            or _slugify(name)
        )
        start = self._ui.choose(
            "persona-start",
            PERSONA_BUILDER_START,
            (
                MenuOption("blank", PERSONA_BLANK),
                MenuOption("simon", PERSONA_PRESET_SIMON),
                MenuOption("steady", PERSONA_PRESET_STEADY),
            ),
        )
        if start == "blank":
            definition: PersonaDefinition = blank_persona_template(
                persona_id, name
            )
        elif start in ("simon", "steady"):
            definition = persona_from_preset(start, persona_id, name)
        else:
            raise SetupInputError(f"Unknown persona start: {start}")
        self._draft.persona_id = persona_id
        self._draft.authored_persona = self._edit_persona_axes(definition)

    def _edit_persona_axes(self, definition: PersonaDefinition) -> dict[str, Any]:
        current = definition
        for axis in PERSONA_AXES:
            options = tuple(
                MenuOption(
                    choice.value,
                    f"{choice.value} — {choice.explanation} {choice.example}",
                )
                for choice in axis.choices
            )
            chosen = self._ui.choose(
                f"persona-axis-{axis.field}",
                axis.label,
                options,
            )
            valid = {choice.value for choice in axis.choices}
            if chosen not in valid:
                raise SetupInputError(
                    f"Unknown {axis.field} value: {chosen}; expected one of "
                    f"{', '.join(sorted(valid))}"
                )
            current = persona_from_data({**asdict(current), axis.field: chosen})
        return asdict(current)

    def _persona_definition(self) -> PersonaDefinition:
        authored = self._draft.authored_persona
        if authored is not None and self._draft.persona_id == authored.get("id"):
            return persona_from_data(authored)
        persona_id = self._draft.persona_id or "simon"
        return next(item for item in DEFAULT_PERSONAS if item.id == persona_id)

    # ---------------------------------------------------------------- provider

    def _step_provider(self) -> None:
        if "provider" in self._preselected:
            definition = find_provider(self._preselected["provider"])
            if not definition.available:
                raise SetupInputError(
                    f"{definition.display_name} is coming soon and cannot be "
                    "selected yet."
                )
            self._configure_provider(definition)
            return
        while True:
            definition = self._select_provider()
            try:
                self._configure_provider(definition)
                return
            except SetupBack:
                continue

    def _select_provider(self) -> ProviderDefinition:
        detected = self._detect_providers()
        if detected:
            self._ui.info(PROVIDER_DETECTED_TITLE)
            for definition, hits in detected:
                labels = "; ".join(hit.label for hit in hits)
                self._ui.info(f"- {definition.display_name}: {labels}")
        return self._choose_provider_from_catalog(detected)

    def _detect_providers(self) -> list[tuple[ProviderDefinition, tuple[Any, ...]]]:
        import os

        environ = (
            os.environ if self._services.environ is None else self._services.environ
        )
        configured = frozenset(self._store.providers)
        found: list[tuple[ProviderDefinition, tuple[Any, ...]]] = []
        cloud = providers_in_group("direct") + providers_in_group("gateway")
        for item in (*cloud, *providers_in_group("local")):
            if not item.available:
                continue
            hits = self._services.adapter_for(item.id).detect(
                environ=environ,
                configured=configured,
                probe=self._services.probe if item.is_local else None,
            )
            if hits:
                found.append((item, hits))
        return found

    def _choose_provider_from_catalog(
        self,
        detected: list[tuple[ProviderDefinition, tuple[Any, ...]]] | None = None,
    ) -> ProviderDefinition:
        while True:
            options: list[MenuOption] = []
            detected_ids = {definition.id for definition, _ in detected or ()}
            for definition, _hits in detected or ():
                options.append(
                    MenuOption(
                        definition.id,
                        f"{definition.display_name} (Detected access)",
                        note=definition.summary,
                    )
                )
            for group in GROUP_ORDER:
                for item in providers_in_group(group):
                    if not item.available or item.id in detected_ids:
                        continue
                    badges = f" ({'; '.join(item.badges)})" if item.badges else ""
                    options.append(
                        MenuOption(
                            item.id,
                            f"{item.display_name}{badges}",
                            note=GROUP_LABELS[group],
                        )
                    )
            fake_index = next(
                (index for index, option in enumerate(options) if option.id == "fake"),
                None,
            )
            if fake_index is not None:
                fake = options[fake_index]
                options[fake_index] = MenuOption(
                    fake.id,
                    "Demo — deterministic and offline",
                    note=fake.note,
                )
            if coming_soon_providers():
                self._ui.secondary(PROVIDER_COMING_SOON)
            chosen = self._ui.choose(
                "provider", PROVIDER_TITLE, tuple(options), allow_back=True
            )
            try:
                definition = find_provider(chosen)
            except KeyError as error:
                raise SetupInputError(f"Unknown provider: {chosen}") from error
            if not definition.available:
                self._ui.warning(
                    f"{definition.display_name} is coming soon and cannot be "
                    "selected yet."
                )
                continue
            return definition

    def _configure_provider(self, definition: ProviderDefinition) -> None:
        while True:
            try:
                config = self._collect_provider_config(definition)
                if definition.id == "fake":
                    self._store_verified(definition, config, demo=True)
                    self._ui.success(
                        f"{PROVIDER_DEMO}: the deterministic fake provider "
                        "is ready."
                    )
                    return
                if self._verify_provider(definition, config):
                    self._offer_live_persona_sample(definition, config)
                    return
            except (ProviderVerificationError, ModelDiscoveryError) as error:
                self._ui.failure(str(error))
                action = self._recover_provider_failure(definition)
                if action == "retry":
                    continue
                if action == "edit":
                    continue
                raise SetupBack

    def _offer_live_persona_sample(
        self, definition: ProviderDefinition, config: AdapterConfig
    ) -> None:
        """One optional generated sample after verification; never required."""
        if not self._ui.confirm(
            "persona-live-sample",
            PROVIDER_LIVE_SAMPLE,
            default=False,
        ):
            return
        persona = self._persona_definition()
        responder = self._services.responder(persona, definition.id, config)
        try:
            sample = responder.respond(
                "Introduce yourself in two sentences as my Reckoning agent."
            )
        except (ProviderVerificationError, RuntimeError) as error:
            self._ui.warning(f"The live sample failed: {error}")
            return
        self._ui.info(sample)
        if not self._ui.confirm(
            "persona-live-sample-keep",
            f"Keep {persona.name} as your persona?",
            default=True,
        ):
            self._step_persona()

    def _collect_provider_config(
        self, definition: ProviderDefinition
    ) -> AdapterConfig:
        import os

        environ = (
            os.environ if self._services.environ is None else self._services.environ
        )
        api_key: str | None = None
        credential_kind = "none"
        base_url: str | None = None
        protocol = "openai-chat-completions"
        context_window: int | None = None
        header_env: dict[str, str] = {}
        self._pending_key = None
        self._pending_env_name = None
        if definition.auth == "api-key":
            api_key, credential_kind = self._collect_api_key(definition, environ)
        elif definition.auth == "keyless-local":
            base_url = definition.base_url
            if not any(
                self._services.probe(url) for url in definition.local_probe_urls
            ):
                self._ui.warning(PROVIDER_LOCAL_MISSING)
                if definition.install_hint:
                    self._ui.info(definition.install_hint)
                if not self._ui.confirm(
                    "provider-local-continue",
                    "Try the endpoint anyway?",
                    default=False,
                ):
                    raise SetupBack
            credential_kind = "keyless"
        elif definition.auth == "custom-endpoint":
            base_url = self._ui.ask(
                "provider-base-url",
                "Base URL of the endpoint (e.g. https://host/v1): ",
                default=self._draft.provider_base_url or "",
                allow_empty=False,
            )
            env_name = self._preselected.get("credential-env")
            if env_name:
                api_key = environ.get(env_name, "").strip() or None
                if api_key is None:
                    raise SetupInputError(
                        f"the credential environment variable {env_name} is not set"
                    )
                self._pending_env_name = env_name
                credential_kind = "env-ref"
            else:
                raw_key = self._ui.ask(
                    "provider-custom-key",
                    "API key (input hidden; leave empty for keyless): ",
                    secret=True,
                )
                api_key = raw_key or None
                credential_kind = "store" if api_key else "none"
            advanced = self._ui.confirm(
                "provider-custom-advanced",
                "Configure protocol, context size, or environment-backed headers?",
                default=False,
            )
            if advanced:
                protocol = self._ui.choose(
                    "provider-custom-protocol",
                    "Protocol",
                    (
                        MenuOption(
                            "openai-chat-completions",
                            "OpenAI Chat Completions",
                        ),
                    ),
                )
                raw_context = self._ui.ask(
                    "provider-custom-context",
                    "Context size in tokens (leave empty if unknown): ",
                )
                if raw_context:
                    try:
                        context_window = int(raw_context)
                    except ValueError as error:
                        raise SetupInputError(
                            "custom endpoint context size must be an integer"
                        ) from error
                    if context_window <= 0:
                        raise SetupInputError(
                            "custom endpoint context size must be positive"
                        )
                raw_headers = self._ui.ask(
                    "provider-custom-header-env",
                    "Headers as Name=ENV_VAR, separated by commas (optional): ",
                )
                header_env = self._parse_header_env(raw_headers, environ)
        if definition.id == "fake":
            model = "deterministic-fake"
        else:
            model = self._choose_model(
                definition,
                AdapterConfig(
                    api_key=api_key,
                    base_url=base_url,
                    protocol=protocol,
                    context_window=context_window,
                    headers=tuple(
                        (name, environ[env_name].strip())
                        for name, env_name in header_env.items()
                    ),
                ),
            )
        self._draft.provider_credential = credential_kind
        self._draft.provider_protocol = protocol
        self._draft.provider_context_window = context_window
        self._draft.provider_header_env = header_env
        self._pending_key = api_key
        return AdapterConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            protocol=protocol,
            context_window=context_window,
            headers=tuple(
                (name, environ[env_name].strip())
                for name, env_name in header_env.items()
            ),
        )

    @staticmethod
    def _parse_header_env(
        raw: str, environ: Mapping[str, str]
    ) -> dict[str, str]:
        references: dict[str, str] = {}
        for item in (part.strip() for part in raw.split(",")):
            if not item:
                continue
            if "=" not in item:
                raise SetupInputError(
                    "custom headers must use the form Header=ENV_VAR"
                )
            name, env_name = (part.strip() for part in item.split("=", 1))
            if not is_valid_header_name(name) or not is_valid_env_name(env_name):
                raise SetupInputError(
                    "custom headers must use the form Header=ENV_VAR"
                )
            if not environ.get(env_name, "").strip():
                raise SetupInputError(
                    f"the custom header environment variable {env_name} is not set"
                )
            references[name] = env_name
        return references

    def _collect_api_key(
        self,
        definition: ProviderDefinition,
        environ: Mapping[str, str],
    ) -> tuple[str | None, str]:
        env_name = next(
            (
                name
                for name in definition.env_names
                if environ.get(name, "").strip()
            ),
            None,
        )
        requested_env_name = self._preselected.get("credential-env")
        if requested_env_name:
            requested_value = environ.get(requested_env_name, "").strip()
            if not requested_value:
                raise SetupInputError(
                    f"the credential environment variable {requested_env_name} "
                    "is not set"
                )
            self._pending_env_name = requested_env_name
            return requested_value, "env-ref"
        sources: list[MenuOption] = []
        if env_name is not None:
            sources.append(
                MenuOption(
                    "env-ref",
                    f"Use the {env_name} environment reference "
                    "(the value stays in your environment)",
                )
            )
        if definition.id in self._store.providers:
            sources.append(
                MenuOption("saved", "Use the saved credential (value not shown)")
            )
        sources.append(
            MenuOption(
                "new",
                f"Paste a new {definition.display_name} API key (hidden input)",
            )
        )
        source = self._ui.choose(
            "provider-key-source",
            f"How should Reckoning access {definition.display_name}?",
            tuple(sources),
        )
        if source == "env-ref":
            assert env_name is not None
            self._pending_env_name = env_name
            return environ[env_name].strip(), "env-ref"
        if source == "saved":
            return self._store.api_key_for(definition.id, environ), "store"
        if source != "new":
            raise SetupInputError(f"Unknown credential source: {source}")
        key = self._ui.ask(
            "provider-key",
            f"Paste the {definition.display_name} API key: ",
            secret=True,
            allow_empty=False,
        )
        return key, "store"

    def _choose_model(
        self,
        definition: ProviderDefinition,
        config: AdapterConfig,
    ) -> str:
        recommended = definition.recommended_model
        options: list[MenuOption] = []
        if recommended:
            options.append(
                MenuOption("recommended", f"{recommended} ({PROVIDER_RECOMMENDED})")
            )
        if definition.id != "fake":
            options.append(
                MenuOption("discover", "Search models discovered from the provider")
            )
        options.append(MenuOption("manual", PROVIDER_MODEL_MANUAL))
        choice = self._ui.choose(
            "provider-model", PROVIDER_MODEL_TITLE, tuple(options)
        )
        if choice == "recommended":
            if recommended is None:
                raise SetupInputError(
                    "No recommended model exists for this provider."
                )
            return recommended
        if choice == "manual":
            return self._ui.ask(
                "provider-model-manual", "Model ID: ", allow_empty=False
            )
        if choice == "discover":
            discovered = self._services.adapter_for(definition.id).discover_models(
                config,
                transport=self._services.transport,
            )
            filter_text = self._ui.ask(
                "provider-model-filter", "Filter models (empty shows all): "
            )
            matches = tuple(
                model
                for model in discovered
                if filter_text.casefold() in model.casefold()
            )
            if not matches:
                self._ui.warning("No discovered model matches that filter.")
                raise SetupBack
            return self._ui.choose(
                "provider-model-pick",
                PROVIDER_MODEL_TITLE,
                tuple(MenuOption(model, model) for model in matches),
            )
        # Scripted and non-interactive runs may name a model id directly.
        return choice

    def _verify_provider(
        self, definition: ProviderDefinition, config: AdapterConfig
    ) -> bool:
        from urllib.parse import urlsplit

        domain = urlsplit(config.base_url or definition.base_url or "").netloc
        paid = definition.group in ("direct", "gateway") or (
            definition.group == "custom" and config.api_key
        )
        self._ui.info(PROVIDER_VERIFY_INTRO)
        self._ui.info(
            f"Provider: {definition.display_name}; model: {config.model}; "
            f"destination: {domain or 'local'}."
        )
        self._ui.info(PROVIDER_VERIFY_CHARGE if paid else PROVIDER_VERIFY_FREE)
        if not self._ui.confirm(
            "provider-verify-consent", "Run the provider test now?", default=True
        ):
            raise SetupBack
        verification = self._services.adapter_for(definition.id).verify(
            config, transport=self._services.transport
        )
        self._store_verified(definition, config, demo=verification.demo)
        self._ui.success(
            f"{PROVIDER_VERIFIED}: {definition.display_name} answered in "
            f"{verification.latency_ms} ms."
        )
        return True

    def _store_verified(
        self,
        definition: ProviderDefinition,
        config: AdapterConfig,
        *,
        demo: bool,
        verified: bool = True,
    ) -> None:
        self._draft.provider_id = definition.id
        self._draft.provider_model = config.model
        self._draft.provider_base_url = config.base_url
        self._draft.provider_demo = demo
        self._draft.provider_verified_at = (
            datetime.now(UTC).isoformat() if verified else None
        )
        if definition.id == "fake" or definition.auth == "keyless-local":
            return
        previous_default = self._store.default_provider
        if self._draft.provider_credential == "env-ref" and self._pending_env_name:
            self._store.set_env_reference(
                definition.id,
                self._pending_env_name,
                model=config.model,
                verified=verified,
            )
        elif config.api_key:
            self._store.set_key(
                definition.id,
                config.api_key,
                model=config.model,
                base_url=config.base_url,
                verified=verified,
            )
        elif definition.auth == "custom-endpoint":
            self._store.set_key(
                definition.id,
                "endpoint-only",
                model=config.model,
                base_url=config.base_url,
                verified=verified,
            )
        else:
            return
        self._store.default_provider = previous_default
        self._store.save(self._paths.credentials_path)

    def _recover_provider_failure(self, definition: ProviderDefinition) -> str:
        action = self._ui.choose(
            "provider-failure",
            PROVIDER_FAILURE_ACTIONS,
            (
                MenuOption("retry", "Retry the test"),
                MenuOption("edit", "Edit provider settings"),
                MenuOption("back", "Back to the provider list"),
                MenuOption(
                    "save", "Save for later (stays inactive until verified)"
                ),
                MenuOption("exit", "Exit setup (draft is saved)"),
            ),
        )
        if action == "exit":
            raise SetupExit
        if action == "save":
            config = AdapterConfig(
                api_key=self._pending_key,
                base_url=self._draft.provider_base_url,
                model=self._draft.provider_model,
            )
            self._store_verified(definition, config, demo=False, verified=False)
            self._ui.warning(PROVIDER_UNVERIFIED_SAVED)
            return "back"
        if action not in ("retry", "edit", "back"):
            raise SetupInputError(f"Unknown recovery action: {action}")
        return action

    # ----------------------------------------------------------------- profile

    def _step_profile(self) -> None:
        choice = self._ui.choose(
            "profile",
            PROFILE_TITLE,
            (
                MenuOption("skip", PROFILE_SKIP),
                MenuOption("guided", PROFILE_GUIDED),
                MenuOption("starter", PROFILE_STARTER),
                MenuOption("import", PROFILE_IMPORT),
            ),
            allow_back=True,
        )
        entries: list[UserProfileEntry] = []
        if choice == "guided":
            for section, question in PROFILE_SECTIONS:
                answer = self._ui.ask(
                    f"profile-{section}", f"{question} (optional): "
                )
                if answer.strip():
                    entries.append(
                        UserProfileEntry(
                            record_id=f"setup-guided-{section}",
                            section=section,
                            text=answer.strip(),
                            source="setup-guided",
                        )
                    )
        elif choice == "starter":
            self._ui.secondary(PROFILE_STARTER_HELP)
            raw_path = self._ui.ask(
                "profile-starter-path",
                "Where should Reckoning create the starter file? ",
                default="about-me.md",
                allow_empty=False,
            )
            profile_path = Path(raw_path).expanduser()
            try:
                profile_path.parent.mkdir(parents=True, exist_ok=True)
                with profile_path.open("x", encoding="utf-8") as stream:
                    stream.write(PROFILE_STARTER_TEMPLATE)
            except FileExistsError as error:
                raise SetupInputError(
                    f"The starter profile already exists: {profile_path}"
                ) from error
            except OSError as error:
                profile_path.unlink(missing_ok=True)
                raise SetupInputError(
                    f"Could not create the starter profile: {error}"
                ) from error
            self._ui.success(f"Starter profile created: {profile_path}")
        elif choice == "import":
            raw_path = self._ui.ask(
                "profile-import-path",
                "Path to a UTF-8 Markdown profile: ",
                allow_empty=False,
            )
            try:
                parsed = list(read_user_profile(Path(raw_path)))
            except (OSError, ValueError) as error:
                self._ui.failure(str(error))
                raise SetupBack
            self._ui.info(PROFILE_IMPORT_PREVIEW)
            for index, entry in enumerate(parsed, start=1):
                self._ui.info(f"  {index}. [{entry.section}] {entry.text}")
            removal = self._ui.ask(
                "profile-import-remove",
                "Numbers to remove, comma-separated (empty keeps all): ",
            )
            keep = parsed
            if removal.strip():
                try:
                    dropped = {int(part.strip()) for part in removal.split(",")}
                except ValueError as error:
                    raise SetupInputError(
                        "Removal entries must be comma-separated numbers."
                    ) from error
                keep = [
                    entry
                    for index, entry in enumerate(parsed, start=1)
                    if index not in dropped
                ]
            entries = keep
        elif choice != "skip":
            raise SetupInputError(f"Unknown profile choice: {choice}")
        self._profile_entries = entries
        self._draft.profile_choice = choice
        self._draft.profile_proposal_count = len(entries)
        self._draft.profile_entries = [
            {
                "record_id": entry.record_id,
                "section": entry.section,
                "text": entry.text,
                "source": entry.source,
            }
            for entry in entries
        ]
        if entries:
            self._ui.info(PROFILE_SAVED_AS_PROPOSALS)

    # -------------------------------------------------------------- connectors

    def _step_connectors(self) -> None:
        self._ui.info(INTERFACE_TERMINAL_READY)
        self._ui.info(INTERFACE_WEB_READY)
        while True:
            self._ui.secondary(CONNECTOR_COMING_SOON)
            choice = self._ui.choose(
                "connectors",
                CONNECTOR_PROMPT,
                (
                    MenuOption("telegram", CONNECTOR_TELEGRAM),
                    MenuOption("skip", CONNECTOR_SKIP),
                ),
                allow_back=True,
            )
            if choice == "skip":
                self._draft.telegram_status = telegram_connector_status(
                    self._paths.telegram_config_path
                )
                return
            if choice != "telegram":
                raise SetupInputError(f"Unknown connector: {choice}")
            self._setup_telegram()
            return

    def _setup_telegram(self) -> None:
        existing: TelegramConnectorConfig | None = None
        if self._paths.telegram_config_path.exists():
            try:
                existing = TelegramConnectorConfig.load(
                    self._paths.telegram_config_path
                )
                self._ui.info(
                    f"Telegram is currently: {existing.status} "
                    f"(@{existing.bot_username})."
                )
            except ValueError as error:
                self._ui.warning(str(error))
        raw_token = self._ui.ask(
            "telegram-token",
            "Paste the BotFather token (input hidden"
            + ("; Enter keeps the saved token" if existing else "")
            + "): ",
            secret=True,
        )
        token = raw_token or (existing.bot_token if existing else "")
        if not token:
            raise SetupInputError("A Telegram bot token is required.")
        username = verify_telegram_bot(
            token, api_factory=self._services.api_factory()
        )
        self._ui.success(f"Bot verified: @{username}")
        config = TelegramConnectorConfig(
            token,
            username,
            existing.paired_chat_id if existing else None,
            self._draft.provider_id or "fake",
        )
        if self._ui.confirm(
            "telegram-pair", TELEGRAM_PAIRING_PROMPT, default=False
        ):
            chat_id = pair_telegram_owner(
                token,
                pairing_code=self._services.telegram_pairing_code,
                maximum_polls=self._services.telegram_max_polls,
                api_factory=self._services.api_factory(),
                output=self._ui.info,
            )
            config = TelegramConnectorConfig(
                token, username, chat_id, config.provider_name
            )
            self._ui.success("Ready: the owner chat is paired.")
        else:
            self._ui.info(
                "Verified, not paired: the bot token is saved; the runtime "
                "refuses messages until an owner chat pairs."
            )
        config.save(self._paths.telegram_config_path)
        self._draft.telegram_status = config.status
        self._ui.info(TELEGRAM_GATEWAY_NOTE)

    # -------------------------------------------------------- first conversation

    def _step_first_conversation(self) -> None:
        self._ui.info(FIRST_MESSAGE_INTRO)
        while True:
            message = self._ui.ask(
                "first-message", "Your first message: ", allow_empty=False
            )
            responder = self._services.responder(
                self._persona_definition(),
                self._draft.provider_id or "fake",
                self._current_adapter_config(),
            )
            try:
                response = responder.respond(message)
            except (ProviderVerificationError, RuntimeError) as error:
                self._ui.failure(f"The provider could not answer: {error}")
                action = "retry"
            else:
                self._ui.info(response)
                action = self._ui.choose(
                    "first-message-action",
                    FIRST_MESSAGE_ACTION_PROMPT,
                    tuple(
                        MenuOption(item_id, label)
                        for item_id, label in FIRST_MESSAGE_ACTIONS
                    ),
                )
            if action == "accept":
                self._accepted = (message, response)
                return
            if action == "retry":
                continue
            if action == "exit":
                raise SetupExit
            if action == "edit-persona":
                self._step_persona()
                self._draft.completed = [
                    step
                    for step in self._draft.completed
                    if step != "first-conversation"
                ]
                continue
            if action == "change-model":
                definition = find_provider(self._draft.provider_id or "fake")
                config = self._current_adapter_config()
                self._draft.provider_model = self._choose_model(
                    definition, config
                )
                continue
            if action == "change-provider":
                self._step_provider()
                continue
            raise SetupInputError(f"Unknown first-message action: {action}")

    def _current_adapter_config(self) -> AdapterConfig:
        provider_id = self._draft.provider_id or "fake"
        api_key = self._store.api_key_for(
            provider_id, self._services.environ
        )
        credential = self._store.credential_for(provider_id)
        base_url = (
            self._draft.provider_base_url
            or (credential.base_url if credential else None)
        )
        model = self._draft.provider_model or (
            credential.model if credential else None
        )
        if provider_id == "custom" and api_key == "endpoint-only":
            api_key = None
        environ = os.environ if self._services.environ is None else self._services.environ
        return AdapterConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            protocol=self._draft.provider_protocol,
            context_window=self._draft.provider_context_window,
            headers=tuple(
                (name, environ.get(env_name, ""))
                for name, env_name in self._draft.provider_header_env.items()
            ),
        )

    # ------------------------------------------------------------------ review

    def _step_review(self) -> None:
        while True:
            definition = find_provider(self._draft.provider_id or "fake")
            persona = self._persona_definition()
            storage = self._draft.placement or "local"
            about_you = (
                f"{self._draft.profile_proposal_count} unconfirmed proposals"
                if self._draft.profile_proposal_count
                else "Skipped for now"
            )
            telegram = self._draft.telegram_status or "not configured"
            options = (
                MenuOption(
                    "provider",
                    f"AI provider: {definition.display_name}",
                    note=f"Model: {self._draft.provider_model or 'deterministic-fake'}",
                ),
                MenuOption(
                    "connectors",
                    "Interfaces: Terminal ready; Web ready",
                    note=f"Telegram: {telegram}",
                ),
                MenuOption(
                    "persona",
                    f"Agent style: {persona.name}",
                    note=f"Desired-self persona: {persona.id}",
                ),
                MenuOption("profile", f"About you: {about_you}"),
                MenuOption(
                    "placement",
                    f"Storage: {storage}",
                    note="Local storage is the default; model processing is separate.",
                ),
                MenuOption("continue", REVIEW_CONFIRM),
            )
            action = self._ui.choose(
                "review-action",
                REVIEW_TITLE,
                options,
                allow_back=True,
            )
            if action == "continue":
                if (
                    definition.group in ("direct", "gateway")
                    and storage == "local"
                ):
                    self._ui.info(PLACEMENT_CLOUD_NOTE)
                return
            try:
                if action == "provider":
                    self._step_provider()
                elif action == "connectors":
                    self._step_connectors()
                elif action == "persona":
                    self._step_persona()
                elif action == "profile":
                    self._step_profile()
                elif action == "placement":
                    self._step_placement()
                else:
                    raise SetupInputError(f"Unknown review action: {action}")
            except SetupBack:
                continue

    # -------------------------------------------------------------- activation

    def _activate(self) -> SetupOutcome:
        provider_id = self._draft.provider_id or "fake"
        definition = find_provider(provider_id)
        persona = self._persona_definition()
        demo = self._draft.provider_demo
        model_name = (
            self._draft.provider_model
            or definition.recommended_model
            or "deterministic-fake"
        )
        activation = {
            "status": "activated",
            "setup_schema": SETUP_DRAFT_SCHEMA,
            "provider": provider_id,
            "model": model_name,
            "base_url": self._draft.provider_base_url,
            "protocol": self._draft.provider_protocol,
            "context_window": self._draft.provider_context_window,
            "header_env": self._draft.provider_header_env,
            "demo": demo,
            "verified_at": self._draft.provider_verified_at,
            "activated_at": datetime.now(UTC).isoformat(),
        }
        if self._accepted is None:
            raise OperationError(
                "Activation requires an accepted first conversation."
            )
        root_snapshots = [
            _EmptyRootSnapshot.capture(self._paths.data_dir.expanduser().resolve())
        ]
        if self._draft.server_data_dir:
            root_snapshots.append(
                _EmptyRootSnapshot.capture(
                    Path(self._draft.server_data_dir).expanduser().resolve()
                )
            )
        file_snapshots = (
            _FileSnapshot.capture(self._paths.credentials_path),
            _FileSnapshot.capture(self._paths.telegram_config_path),
        )
        try:
            setup_instance(
                self._paths.data_dir,
                (self._draft.placement or "local"),  # type: ignore[arg-type]
                persona,
                server_data_dir=(
                    Path(self._draft.server_data_dir)
                    if self._draft.server_data_dir
                    else None
                ),
                profile_entries=tuple(self._profile_entries) or None,
                first_conversation=self._accepted,
                activation=activation,
            )
            credential = self._store.credential_for(provider_id)
            if credential is not None:
                self._store.providers[provider_id] = StoredCredential(
                    secret=credential.secret,
                    model=model_name,
                    base_url=self._draft.provider_base_url or credential.base_url,
                    verified=credential.verified,
                    verified_at=credential.verified_at,
                )
            self._store.default_provider = (
                provider_id
                if definition.auth in ("api-key", "custom-endpoint")
                else None
            )
            if self._store.providers or self._paths.credentials_path.exists():
                self._store.save(self._paths.credentials_path)
            self._update_telegram_provider(provider_id)
            self._prove_reopen()
        except (OperationError, OSError, RuntimeError, ValueError) as error:
            rollback_errors: list[str] = []
            for root_snapshot in reversed(root_snapshots):
                try:
                    root_snapshot.restore()
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            for file_snapshot in file_snapshots:
                try:
                    file_snapshot.restore()
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            rollback_note = (
                f" Rollback also failed: {'; '.join(rollback_errors)}."
                if rollback_errors
                else " The partial activation was rolled back."
            )
            raise OperationError(
                f"Activation failed: {error}.{rollback_note} "
                "Run reckoning setup to resume."
            ) from error
        self._paths.draft_path.unlink(missing_ok=True)
        if demo:
            self._ui.success(
                f"{PROVIDER_DEMO}: setup completed with the deterministic fake "
                "provider. No paid provider is active."
            )
        else:
            self._ui.success(
                f"{PROVIDER_VERIFIED}: {definition.display_name} is active."
            )
        telegram_status = TELEGRAM_STATUS_LABELS.get(
            self._draft.telegram_status or "not-configured",
            self._draft.telegram_status or "not-configured",
        )
        self._ui.info(INTERFACE_TERMINAL_COMPLETE)
        self._ui.info(INTERFACE_WEB_COMPLETE)
        self._ui.info(TELEGRAM_COMPLETE.format(status=telegram_status))
        gateway = gateway_runtime_status(self._paths.data_dir)
        if gateway.running and gateway.pid is not None:
            self._ui.info(GATEWAY_COMPLETE_RUNNING.format(pid=gateway.pid))
        else:
            self._ui.info(GATEWAY_COMPLETE_STOPPED)
        self._ui.success(
            "Setup complete: the installed state reopened with your accepted "
            "first conversation."
        )
        return SetupOutcome(
            status="activated",
            provider_id=provider_id,
            model=model_name,
            demo=demo,
            persona_id=persona.id,
            telegram_status=self._draft.telegram_status,
            data_dir=self._paths.data_dir,
        )

    def _update_telegram_provider(self, provider_id: str) -> None:
        if not self._paths.telegram_config_path.exists():
            return
        try:
            config = TelegramConnectorConfig.load(self._paths.telegram_config_path)
        except ValueError:
            return
        TelegramConnectorConfig(
            config.bot_token,
            config.bot_username,
            config.paired_chat_id,
            provider_id,
        ).save(self._paths.telegram_config_path)

    def _prove_reopen(self) -> None:
        from reckoning.interfaces import JsonFileInterfaceRepository

        try:
            runtime = load_installation_runtime(
                self._paths.data_dir,
                server_data_dir=(
                    Path(self._draft.server_data_dir)
                    if self._draft.server_data_dir
                    else None
                ),
            )
            provider = RuntimeProviderSettings.load(
                self._paths.data_dir,
                credentials_path=self._paths.credentials_path,
                environ=self._services.environ,
            )
            session_path = runtime.state_path("confirmed-state", "interfaces.json")
            state = JsonFileInterfaceRepository(session_path).load()
        except (OperationError, RuntimeError, ValueError) as error:
            raise OperationError(
                f"Activation failed at the reopen check: {error}."
            ) from error
        if provider.provider_name != (self._draft.provider_id or "fake"):
            raise OperationError(
                "Activation failed at the reopen check: the installed provider "
                "does not match the verified provider."
            )
        assert self._accepted is not None
        user_text, assistant_text = self._accepted
        web_sessions = [
            session
            for session in state.sessions
            if session.channel == "web" and session.session_id == ""
        ]
        for session in web_sessions:
            contents = [(m.role, m.content) for m in session.messages]
            if ("user", user_text) in contents and (
                "assistant",
                assistant_text,
            ) in contents:
                return
        raise OperationError(
            "Activation failed at the reopen check: the accepted first "
            "conversation is missing from the installed state."
        )

    # ------------------------------------------------------- configured state

    def _run_configured(self) -> SetupOutcome:
        self._maybe_offer_migration()
        while True:
            sections = self._status_sections()
            self._ui.info(STATUS_TITLE)
            for section in sorted(sections, key=lambda item: item.ok):
                marker = "ok" if section.ok else "FAILING"
                render = f"[{marker}] {section.label}: {section.detail}"
                if section.ok:
                    self._ui.info(render)
                else:
                    self._ui.failure(render)
            failing = [item for item in sections if not item.ok]
            action = self._ui.choose(
                "status-action",
                STATUS_PROMPT,
                (
                    MenuOption("verify-all", STATUS_VERIFY_ALL),
                    MenuOption(
                        "repair",
                        STATUS_REPAIR,
                        available=bool(failing),
                        note="" if failing else STATUS_NOTHING_FAILING,
                    ),
                    MenuOption("edit", STATUS_EDIT),
                    MenuOption("exit", STATUS_EXIT),
                ),
            )
            if action == "exit":
                return SetupOutcome(
                    status="managed", data_dir=self._paths.data_dir
                )
            if action == "verify-all":
                self._verify_all()
            elif action == "repair":
                if not failing:
                    self._ui.info(STATUS_NOTHING_FAILING)
                    continue
                target = self._ui.choose(
                    "repair-section",
                    STATUS_REPAIR_PROMPT,
                    tuple(
                        MenuOption(item.key, item.label) for item in failing
                    ),
                )
                self._edit_section(target)
            elif action == "edit":
                target = self._ui.choose(
                    "edit-section",
                    STATUS_EDIT_PROMPT,
                    tuple(
                        MenuOption(key, label)
                        for key, label in STATUS_EDIT_CHOICES
                    ),
                )
                self._edit_section(target)
            else:
                raise SetupInputError(f"Unknown status action: {action}")

    def _status_sections(self) -> tuple[SectionStatus, ...]:
        sections: list[SectionStatus] = []
        instance = read_json(self._paths.data_dir / "instance.json", default={})
        activation = instance.get("activation")
        placement = instance.get("placement_profile", "unknown")
        sections.append(
            SectionStatus("placement", STATUS_LABEL_STORAGE, True, f"{placement}")
        )
        sections.append(
            SectionStatus(
                "migration",
                STATUS_LABEL_FORMAT,
                isinstance(activation, dict),
                "current" if isinstance(activation, dict) else "required",
            )
        )
        if self._paths.draft_path.exists():
            try:
                draft = SetupDraft.load(self._paths.draft_path)
                draft_detail = (
                    f"incomplete; next step {draft.next_step or 'review'}"
                    if draft is not None
                    else "none"
                )
                sections.append(
                    SectionStatus(
                        "draft", STATUS_LABEL_DRAFT, True, draft_detail
                    )
                )
            except (OperationError, RuntimeError, ValueError) as error:
                sections.append(
                    SectionStatus("draft", STATUS_LABEL_DRAFT, False, str(error))
                )
        else:
            sections.append(
                SectionStatus("draft", STATUS_LABEL_DRAFT, True, "none")
            )
        try:
            persona = PersonaService(
                JsonFilePersonaRepository(self._paths.data_dir / "personas.json")
            ).active()
            sections.append(
                SectionStatus(
                    "persona", STATUS_LABEL_STYLE, True, persona.definition.name
                )
            )
        except (KeyError, LookupError, RuntimeError, ValueError) as error:
            sections.append(
                SectionStatus("persona", STATUS_LABEL_STYLE, False, str(error))
            )
        try:
            provider = RuntimeProviderSettings.load(
                self._paths.data_dir,
                credentials_path=self._paths.credentials_path,
                environ=self._services.environ,
            )
            verified_at = (
                str(activation.get("verified_at") or "never")
                if isinstance(activation, dict)
                else "never"
            )
            detail = (
                f"{provider.provider_name}, model {provider.model}, verified, "
                f"last verified {verified_at}"
            )
            if provider.provider_name == "fake":
                detail = f"fake (demo mode), model {provider.model}"
            sections.append(
                SectionStatus("provider", STATUS_LABEL_PROVIDER, True, detail)
            )
        except (KeyError, RuntimeError, ValueError) as error:
            sections.append(
                SectionStatus(
                    "provider", STATUS_LABEL_PROVIDER, False, str(error)
                )
            )
        try:
            runtime = load_installation_runtime(
                self._paths.data_dir,
                server_data_dir=self._paths.server_data_dir,
            )
            context_repo = JsonFilePersonalContextRepository(
                runtime.state_path("personal-context", "personal-context.json")
            )
            proposed = PersonalContextService(context_repo).list_proposed()
            sections.append(
                SectionStatus(
                    "profile",
                    STATUS_LABEL_ABOUT,
                    True,
                    f"{len(proposed)} unconfirmed proposals",
                )
            )
        except (OperationError, RuntimeError, ValueError) as error:
            sections.append(
                SectionStatus("profile", STATUS_LABEL_ABOUT, False, str(error))
            )
        sections.append(
            SectionStatus(
                "interfaces",
                STATUS_LABEL_INTERFACES,
                True,
                "Terminal ready; Web ready",
            )
        )
        telegram = telegram_connector_status(self._paths.telegram_config_path)
        sections.append(
            SectionStatus(
                "connectors",
                STATUS_LABEL_TELEGRAM,
                telegram not in ("error",),
                TELEGRAM_STATUS_LABELS.get(telegram, telegram),
            )
        )
        gateway = gateway_runtime_status(self._paths.data_dir)
        gateway_detail = (
            GATEWAY_RUNNING.format(pid=gateway.pid)
            if gateway.running and gateway.pid is not None
            else GATEWAY_STOPPED
        )
        sections.append(
            SectionStatus(
                "gateway", STATUS_LABEL_GATEWAY, True, gateway_detail
            )
        )
        return tuple(sections)

    def _verify_all(self) -> None:
        try:
            provider = RuntimeProviderSettings.load(
                self._paths.data_dir,
                credentials_path=self._paths.credentials_path,
                environ=self._services.environ,
            )
            definition = find_provider(provider.provider_name)
        except (KeyError, RuntimeError, ValueError) as error:
            self._ui.failure(f"Provider check failed: {error}")
        else:
            if provider.provider_name == "fake":
                self._ui.info("Fake is deterministic demo mode; no live check ran.")
            elif definition.group in ("direct", "gateway") or (
                definition.group == "custom" and provider.api_key
            ):
                if not self._ui.confirm(
                    "verify-paid-refresh", STATUS_PAID_REFRESH, default=False
                ):
                    self._ui.info("Skipped the paid provider check.")
                else:
                    self._verify_active_provider(provider)
            else:
                self._verify_active_provider(provider)
        if self._paths.telegram_config_path.exists():
            try:
                config = TelegramConnectorConfig.load(
                    self._paths.telegram_config_path
                )
                username = verify_telegram_bot(
                    config.bot_token, api_factory=self._services.api_factory()
                )
                self._ui.success(f"Telegram bot verified: @{username}")
            except (ValueError, RuntimeError) as error:
                self._ui.failure(f"Telegram check failed: {error}")

    def _verify_active_provider(self, provider: RuntimeProviderSettings) -> None:
        definition = find_provider(provider.provider_name)
        config = AdapterConfig(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=provider.model,
            protocol=provider.protocol,
            context_window=provider.context_window,
            headers=provider.headers,
        )
        try:
            result = self._services.adapter_for(provider.provider_name).verify(
                config, transport=self._services.transport
            )
        except (ProviderVerificationError, KeyError) as error:
            self._ui.failure(f"{definition.display_name}: {error}")
            return
        credential = self._store.credential_for(provider.provider_name)
        if credential is not None:
            self._store.mark_verified(provider.provider_name)
            self._store.save(self._paths.credentials_path)
        instance = read_json(self._paths.data_dir / "instance.json", default={})
        activation = instance.get("activation")
        if isinstance(activation, dict):
            atomic_write_json(
                self._paths.data_dir / "instance.json",
                {
                    **instance,
                    "activation": {
                        **activation,
                        "verified_at": datetime.now(UTC).isoformat(),
                    },
                },
            )
        self._ui.success(
            f"{definition.display_name} verified in {result.latency_ms} ms."
        )

    def _edit_section(self, section: str) -> None:
        if section == "persona":
            self._edit_installed_persona()
        elif section == "provider":
            self._step_provider()
            self._commit_installed_provider()
            self._paths.draft_path.unlink(missing_ok=True)
        elif section == "profile":
            self._step_profile()
            self._append_installed_profile_entries()
        elif section == "connectors":
            self._setup_telegram()
        elif section == "migration":
            self._maybe_offer_migration()
        elif section == "draft":
            if self._ui.confirm(
                "repair-draft-remove",
                "Remove the invalid setup draft?",
                default=False,
            ):
                self._paths.draft_path.unlink(missing_ok=True)
                self._ui.success("The invalid setup draft was removed.")
            else:
                self._ui.info("The invalid setup draft was left unchanged.")
        else:
            raise SetupInputError(f"Unknown section: {section}")

    def _commit_installed_provider(self) -> None:
        provider_id = self._draft.provider_id
        if provider_id is None:
            raise OperationError("No provider was selected.")
        definition = find_provider(provider_id)
        instance_path = self._paths.data_dir / "instance.json"
        instance = read_json(instance_path, default={})
        activation = instance.get("activation")
        if not isinstance(activation, dict):
            raise OperationError("The installation has no activation record.")
        snapshots = (
            _FileSnapshot.capture(instance_path),
            _FileSnapshot.capture(self._paths.credentials_path),
            _FileSnapshot.capture(self._paths.telegram_config_path),
        )
        try:
            self._store.default_provider = (
                provider_id
                if definition.auth in ("api-key", "custom-endpoint")
                else None
            )
            if self._store.providers or self._paths.credentials_path.exists():
                self._store.save(self._paths.credentials_path)
            self._update_telegram_provider(provider_id)
            atomic_write_json(
                instance_path,
                {
                    **instance,
                    "activation": {
                        **activation,
                        "provider": provider_id,
                        "model": self._draft.provider_model,
                        "base_url": self._draft.provider_base_url,
                        "protocol": self._draft.provider_protocol,
                        "context_window": self._draft.provider_context_window,
                        "header_env": self._draft.provider_header_env,
                        "demo": self._draft.provider_demo,
                        "verified_at": self._draft.provider_verified_at,
                    },
                },
            )
            reopened = RuntimeProviderSettings.load(
                self._paths.data_dir,
                credentials_path=self._paths.credentials_path,
                environ=self._services.environ,
            )
            if reopened.provider_name != provider_id:
                raise OperationError(
                    "The installed provider does not match the verified provider."
                )
        except (OSError, RuntimeError, ValueError) as error:
            rollback_errors: list[str] = []
            for snapshot in snapshots:
                try:
                    snapshot.restore()
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            self._store = ProviderCredentialStore.load(
                self._paths.credentials_path
            )
            rollback_note = (
                f" Rollback also failed: {'; '.join(rollback_errors)}."
                if rollback_errors
                else ""
            )
            raise OperationError(
                f"Provider update failed and was rolled back: {error}."
                f"{rollback_note}"
            ) from error

    def _edit_installed_persona(self) -> None:
        service = PersonaService(
            JsonFilePersonaRepository(self._paths.data_dir / "personas.json")
        )
        action = self._ui.choose(
            "persona-manage",
            PERSONA_MANAGE,
            (
                MenuOption("select", "Select a persona"),
                MenuOption("author", PERSONA_AUTHOR),
                MenuOption("edit", "Edit an authored persona"),
                MenuOption("duplicate", "Duplicate a persona"),
                MenuOption("rename", "Rename an authored persona"),
                MenuOption("remove", "Remove an authored persona"),
            ),
        )
        known = {item.id: item for item in service.list_defaults()}
        known.update({item.id: item for item in service.list_authored()})
        if action == "select":
            chosen = self._ui.choose(
                "persona-select",
                PERSONA_TITLE,
                tuple(
                    MenuOption(item.id, item.name) for item in known.values()
                ),
            )
            if chosen not in known:
                raise SetupInputError(f"Unknown persona: {chosen}")
            service.select(chosen)
            self._ui.success(f"Active persona: {known[chosen].name}")
            return
        if action == "author":
            self._author_persona()
            authored = self._draft.authored_persona
            assert authored is not None
            service.author_and_select(self._persona_definition())
            self._ui.success(f"Active persona: {authored['name']}")
            return
        chosen = self._ui.choose(
            f"persona-{action}-target",
            "Which persona? ",
            tuple(MenuOption(item.id, item.name) for item in known.values()),
        )
        if chosen not in known:
            raise SetupInputError(f"Unknown persona: {chosen}")
        if action == "duplicate":
            name = self._ui.ask(
                "persona-duplicate-name", "Name for the copy: ", allow_empty=False
            )
            new_id = _slugify(name)
            copy = service.duplicate(chosen, new_id, name)
            self._ui.success(f"Duplicated as {copy.name} ({copy.id}).")
            return
        if action == "rename":
            name = self._ui.ask(
                "persona-rename", "New display name: ", allow_empty=False
            )
            renamed = service.rename_authored(chosen, name)
            self._ui.success(f"Renamed to {renamed.name}.")
            return
        if action == "remove":
            service.remove_authored(chosen)
            self._ui.success(f"Removed {chosen}.")
            return
        if action == "edit":
            updated = self._edit_persona_axes(known[chosen])
            service.update_authored(persona_from_data(updated))
            self._ui.success(f"Updated {updated['name']}.")
            return
        raise SetupInputError(f"Unknown persona action: {action}")

    def _append_installed_profile_entries(self) -> None:
        if not self._profile_entries:
            return
        runtime = load_installation_runtime(
            self._paths.data_dir, server_data_dir=self._paths.server_data_dir
        )
        repository = JsonFilePersonalContextRepository(
            runtime.state_path("personal-context", "personal-context.json")
        )
        service = PersonalContextService(repository)
        processing = runtime.application_placement.processing_location
        now = datetime.now(UTC)
        for entry in self._profile_entries:
            service.propose(
                record_id=entry.record_id,
                original_text=entry.text,
                language="und",
                canonical_meaning=f"{entry.section}: {entry.text}",
                source=entry.source,
                created_at=now,
                processing_location=processing,
            )
        self._ui.info(PROFILE_SAVED_AS_PROPOSALS)

    # --------------------------------------------------------------- migration

    def _maybe_offer_migration(self) -> None:
        instance = read_json(self._paths.data_dir / "instance.json", default={})
        if not instance or "activation" in instance:
            return
        try:
            plan = build_migration_plan(self._paths)
        except (OperationError, RuntimeError, ValueError, KeyError) as error:
            self._ui.failure(
                f"This installation cannot be migrated safely: {error}"
            )
            self._ui.info("No files were changed.")
            return
        self._ui.warning(
            "This installation predates the current setup format and needs "
            "migration."
        )
        for line in plan.preview:
            self._ui.info(line)
        action = self._ui.choose(
            "migrate",
            "Migrate now?",
            (
                MenuOption("migrate", "Preview and migrate (creates backups)"),
                MenuOption("skip", "Continue without migrating"),
                MenuOption("exit", "Exit"),
            ),
        )
        if action == "exit":
            raise SetupExit
        if action == "skip":
            return
        if action != "migrate":
            raise SetupInputError(f"Unknown migration action: {action}")
        if not self._ui.confirm(
            "migrate-confirm", "Apply these changes?", default=True
        ):
            self._ui.info("Migration cancelled; no files were changed.")
            return
        commit_migration_plan(self._paths, plan)
        self._ui.success("Migration committed; backups sit next to the files.")


@dataclass(frozen=True)
class MigrationWrite:
    """One validated file the migration will replace."""

    kind: Literal["credentials", "telegram", "instance"]
    label: str
    path: Path
    payload: bytes
    mode: int
    activation: bool = False


@dataclass(frozen=True)
class MigrationPlan:
    """A read-only migration inspection; nothing is written until commit."""

    writes: tuple[MigrationWrite, ...]
    preview: tuple[str, ...]


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _migration_paths(paths: SetupPaths) -> tuple[Path, Path, Path]:
    data_dir = paths.data_dir.expanduser().resolve()
    return (
        paths.credentials_path.expanduser(),
        paths.telegram_config_path.expanduser(),
        data_dir / "instance.json",
    )


def build_migration_plan(paths: SetupPaths) -> MigrationPlan:
    """Validate every supported setup format before proposing any change."""
    credentials_path, telegram_path, instance_path = _migration_paths(paths)
    if not instance_path.exists():
        raise OperationError("there is no installation to migrate")
    instance = read_json(instance_path, default={})
    if not instance:
        raise OperationError("the installation record is empty")
    if "activation" in instance:
        raise OperationError("the installation is already migrated")
    placement = instance.get("placement_profile")
    if placement not in ("local", "personal-server", "hybrid"):
        raise OperationError("the installation has no supported storage placement")
    roots = instance.get("storage_roots")
    if not isinstance(roots, dict) or roots.get("local") != "local-data-dir":
        raise OperationError("the installation has no enforceable storage roots")

    store, credentials_payload = _inspect_credentials(credentials_path)
    telegram_payload = _inspect_telegram(telegram_path)
    _inspect_personas(instance_path.parent)
    _inspect_runtime_state(paths, instance, roots)

    provider_id = store.default_provider or "fake"
    try:
        definition = find_provider(provider_id)
    except KeyError as error:
        raise OperationError(
            "the primary provider in the credential store is not supported"
        ) from error
    credential = store.credential_for(provider_id)
    model = (
        (credential.model if credential is not None else None)
        or definition.recommended_model
        or "deterministic-fake"
    )
    base_url = (
        (credential.base_url if credential is not None else None)
        or definition.base_url
    )
    activation: dict[str, Any] = {
        "status": "activated",
        "provider": provider_id,
        "model": model,
        "base_url": base_url,
        "protocol": "openai-chat-completions",
        "context_window": None,
        "header_env": {},
        "demo": provider_id == "fake",
        "verified_at": credential.verified_at if credential is not None else None,
        "activated_at": instance.get("created_at"),
        "migrated_at": datetime.now(UTC).isoformat(),
    }
    migrated = {**instance, "activation": activation}

    writes: list[MigrationWrite] = []
    if credentials_payload is not None:
        writes.append(
            MigrationWrite(
                "credentials",
                "provider credentials",
                credentials_path,
                credentials_payload,
                0o600,
            )
        )
    if telegram_payload is not None:
        writes.append(
            MigrationWrite(
                "telegram",
                "Telegram connector",
                telegram_path,
                telegram_payload,
                0o600,
            )
        )
    writes.append(
        MigrationWrite(
            "instance",
            "installation activation",
            instance_path,
            _json_bytes(migrated),
            0o600,
            activation=True,
        )
    )
    preview = [
        "Migration preview (nothing changes until you confirm):",
        f"- installation: {instance_path} gains an activation record",
        f"- primary provider: {provider_id}; model: {model}",
        "- provider credentials rewrite to schema v2 (backup kept)",
        "- Telegram connector rewrites to schema v2 (backup kept)",
        (
            "- the activation record is written last so an interruption "
            "leaves a valid legacy or migrated installation"
        ),
    ]
    return MigrationPlan(tuple(writes), tuple(preview))


def _inspect_credentials(
    path: Path,
) -> tuple[ProviderCredentialStore, bytes | None]:
    if not path.exists():
        return ProviderCredentialStore(), None
    raw = read_json(path, default={})
    known = set(_registry_ids())
    raw_providers = raw.get("providers")
    if raw_providers is not None:
        if not isinstance(raw_providers, dict):
            raise OperationError("the provider credential store is malformed")
        named = {str(name).strip().casefold() for name in raw_providers}
    else:
        legacy = str(raw.get("provider_name", "")).strip().casefold()
        named = {legacy} if legacy else set()
    unknown = named - known
    if unknown:
        raise OperationError(
            "the provider credential store names an unsupported provider"
        )
    store = ProviderCredentialStore.load(path)
    payload = store.payload()
    if raw == payload:
        return store, None
    return store, _json_bytes(payload)


def _inspect_telegram(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        config = TelegramConnectorConfig.load(path)
    except (ValueError, RuntimeError) as error:
        raise OperationError(f"the Telegram connector state is unreadable: {error}")
    payload = config.payload()
    if read_json(path, default={}) == payload:
        return None
    return _json_bytes(payload)


def _inspect_personas(data_dir: Path) -> None:
    try:
        PersonaService(
            JsonFilePersonaRepository(data_dir / "personas.json")
        ).active()
    except (KeyError, LookupError, RuntimeError, ValueError) as error:
        raise OperationError(f"the persona configuration is invalid: {error}")


def _inspect_runtime_state(
    paths: SetupPaths, instance: dict[str, Any], roots: dict[str, Any]
) -> None:
    from reckoning.interfaces import JsonFileInterfaceRepository

    raw_server = roots.get("server")
    server_root = (
        Path(raw_server).expanduser()
        if isinstance(raw_server, str) and raw_server
        else None
    )
    try:
        runtime = load_installation_runtime(
            paths.data_dir, server_data_dir=server_root
        )
    except (OperationError, RuntimeError, ValueError) as error:
        raise OperationError(f"the installation state is invalid: {error}")
    try:
        context_repository = JsonFilePersonalContextRepository(
            runtime.state_path("personal-context", "personal-context.json")
        )
        context_repository.all_versions()
        confirmed = next(
            route for route in runtime.routes if route.category == "confirmed-state"
        )
        JsonFileInterfaceRepository(confirmed.root / "interfaces.json").load()
    except (OSError, RuntimeError, ValueError) as error:
        raise OperationError(f"the durable conversation state is invalid: {error}")


def _registry_ids() -> tuple[str, ...]:
    from reckoning.provider_registry import PROVIDER_REGISTRY

    return tuple(item.id for item in PROVIDER_REGISTRY)


def _stage_migration_write(write: MigrationWrite) -> Path:
    write.path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "wb",
            dir=write.path.parent,
            prefix=f".{write.path.name}.migrate-",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(write.payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    temporary_path.chmod(write.mode)
    return temporary_path


def _validate_staged_migration(write: MigrationWrite, staged: Path) -> None:
    if write.kind == "credentials":
        ProviderCredentialStore.load(staged)
    elif write.kind == "telegram":
        TelegramConnectorConfig.load(staged)
    else:
        staged_instance = read_json(staged, default={})
        if "activation" not in staged_instance:
            raise OperationError(
                "the staged installation lost its activation record"
            )


def commit_migration_plan(paths: SetupPaths, plan: MigrationPlan) -> None:
    """Write backups, stage every file, then activate the marker last."""
    del paths
    snapshots = tuple(
        _FileSnapshot.capture(write.path) for write in plan.writes
    )
    staged: list[tuple[MigrationWrite, Path]] = []
    try:
        for write in plan.writes:
            if write.path.exists():
                shutil.copy2(
                    write.path, write.path.with_suffix(write.path.suffix + ".bak")
                )
        for write in plan.writes:
            staged.append((write, _stage_migration_write(write)))
        for write, temporary_path in staged:
            _validate_staged_migration(write, temporary_path)
        for write, temporary_path in staged:
            os.replace(temporary_path, write.path)
            write.path.chmod(write.mode)
    except (OSError, OperationError, RuntimeError, ValueError) as error:
        for _write, temporary_path in staged:
            temporary_path.unlink(missing_ok=True)
        rollback_errors: list[str] = []
        for snapshot in snapshots:
            try:
                snapshot.restore()
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        detail = (
            f" Rollback also failed: {'; '.join(rollback_errors)}."
            if rollback_errors
            else ""
        )
        raise OperationError(
            f"Migration failed and live state was restored: {error}.{detail}"
        ) from error
