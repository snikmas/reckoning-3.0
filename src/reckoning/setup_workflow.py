"""The setup workflow: steps, transitions, drafts, validation, activation.

This module owns setup policy and never prints terminal UI directly; a
``SetupUI`` adapter owns prompts, navigation, colors, and fallbacks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from reckoning.config import (
    DEFAULT_PROVIDER_CREDENTIALS,
    ProviderCredentialStore,
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
    ProviderGroup,
    coming_soon_providers,
    find_provider,
    providers_in_group,
)
from reckoning.setup_copy import (
    AUTONOMY_FLOOR_TITLE,
    CONNECTOR_COMING_SOON,
    CONNECTOR_TELEGRAM,
    CONNECTOR_TITLE,
    FIRST_MESSAGE_INTRO,
    FIRST_MESSAGE_TITLE,
    MODE_CUSTOM,
    MODE_QUICK,
    MODE_TITLE,
    PERSONA_AUTHOR,
    PERSONA_BLANK,
    PERSONA_BUILDER_START,
    PERSONA_MANAGE,
    PERSONA_PRESET_SIMON,
    PERSONA_PRESET_STEADY,
    PERSONA_PREVIEW_TITLE,
    PERSONA_TITLE,
    PLACEMENT_CLOUD_NOTE,
    PLACEMENT_LOCAL_DEFAULT,
    PLACEMENT_OPTIONS,
    PLACEMENT_TITLE,
    PROFILE_GUIDED,
    PROFILE_IMPORT,
    PROFILE_IMPORT_PREVIEW,
    PROFILE_SAVED_AS_PROPOSALS,
    PROFILE_SECTIONS,
    PROFILE_SKIP,
    PROFILE_TITLE,
    PROVIDER_ASK_TEST,
    PROVIDER_COMING_SOON,
    PROVIDER_DETECTED_TITLE,
    PROVIDER_FAILURE_ACTIONS,
    PROVIDER_LIVE_SAMPLE,
    PROVIDER_LOCAL_MISSING,
    PROVIDER_MODEL_MANUAL,
    PROVIDER_MODEL_TITLE,
    PROVIDER_PREFERENCE_CLOUD,
    PROVIDER_PREFERENCE_GATEWAY,
    PROVIDER_PREFERENCE_LOCAL,
    PROVIDER_PREFERENCE_TITLE,
    PROVIDER_RECOMMENDED,
    PROVIDER_TITLE,
    PROVIDER_UNVERIFIED_SAVED,
    PROVIDER_VERIFY_CHARGE,
    PROVIDER_VERIFY_FREE,
    PROVIDER_VERIFY_INTRO,
    QUICK_EXPLANATION,
    RESUME_EXIT,
    RESUME_RESUME,
    RESUME_START_OVER,
    RESUME_TITLE,
    REVIEW_CONFIRM,
    REVIEW_TITLE,
    START_OVER_PREVIEW,
    STATUS_EDIT,
    STATUS_EXIT,
    STATUS_PAID_REFRESH,
    STATUS_REPAIR,
    STATUS_TITLE,
    STATUS_VERIFY_ALL,
    TELEGRAM_GATEWAY_NOTE,
    TELEGRAM_PAIRING_PROMPT,
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

SETUP_DRAFT_SCHEMA = 1

SetupMode = Literal["quick", "custom"]
SetupStep = Literal[
    "mode",
    "placement",
    "persona",
    "provider",
    "profile",
    "connectors",
    "first-conversation",
    "review",
]

QUICK_STEPS: tuple[SetupStep, ...] = (
    "persona",
    "provider",
    "profile",
    "connectors",
    "first-conversation",
    "review",
)

CUSTOM_STEPS: tuple[SetupStep, ...] = (
    "placement",
    "persona",
    "provider",
    "profile",
    "connectors",
    "first-conversation",
    "review",
)

STEP_TITLES: dict[SetupStep, str] = {
    "mode": MODE_TITLE,
    "placement": PLACEMENT_TITLE,
    "persona": PERSONA_TITLE,
    "provider": PROVIDER_TITLE,
    "profile": PROFILE_TITLE,
    "connectors": CONNECTOR_TITLE,
    "first-conversation": FIRST_MESSAGE_TITLE,
    "review": REVIEW_TITLE,
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

    mode: SetupMode | None = None
    completed: list[str] = field(default_factory=list)
    placement: str | None = None
    server_data_dir: str | None = None
    persona_id: str | None = None
    authored_persona: dict[str, Any] | None = None
    provider_id: str | None = None
    provider_model: str | None = None
    provider_base_url: str | None = None
    provider_credential: str | None = None
    provider_demo: bool = False
    profile_choice: str | None = None
    profile_proposal_count: int = 0
    telegram_status: str | None = None
    schema_version: int = SETUP_DRAFT_SCHEMA

    def mark_completed(self, step: SetupStep) -> None:
        if step not in self.completed:
            self.completed.append(step)

    @classmethod
    def load(cls, path: Path) -> SetupDraft | None:
        data = read_json(path, default={})
        if not data:
            return None
        if data.get("schema_version") != SETUP_DRAFT_SCHEMA:
            raise OperationError("The setup draft uses an unsupported schema.")
        return cls(
            mode=data.get("mode"),
            completed=list(data.get("completed", [])),
            placement=data.get("placement"),
            server_data_dir=data.get("server_data_dir"),
            persona_id=data.get("persona_id"),
            authored_persona=data.get("authored_persona"),
            provider_id=data.get("provider_id"),
            provider_model=data.get("provider_model"),
            provider_base_url=data.get("provider_base_url"),
            provider_credential=data.get("provider_credential"),
            provider_demo=bool(data.get("provider_demo", False)),
            profile_choice=data.get("profile_choice"),
            profile_proposal_count=int(data.get("profile_proposal_count", 0)),
            telegram_status=data.get("telegram_status"),
        )

    def save(self, path: Path) -> None:
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
            for step in (QUICK_STEPS if self.mode == "quick" else CUSTOM_STEPS)
            if step not in self.completed
        ]
        return (
            f"Mode: {self.mode or 'not chosen'}",
            f"Completed steps: {done}",
            f"Remaining steps: {', '.join(remaining) or 'none'}",
            f"Placement: {self.placement or 'not chosen'}",
            f"Persona: {self.persona_id or 'not chosen'}",
            f"Provider: {self.provider_id or 'not chosen'}",
            f"Profile: {self.profile_choice or 'not started'}",
            f"Telegram: {self.telegram_status or 'not started'}",
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
    name: str
    ok: bool
    detail: str


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
        self._store = ProviderCredentialStore.load(paths.credentials_path)
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
        existing: SetupDraft | None = None
        if self._paths.draft_path.exists():
            existing = SetupDraft.load(self._paths.draft_path)
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
            if self._draft.mode is None:
                self._step_mode()
                self._save_draft()
            steps = QUICK_STEPS if self._draft.mode == "quick" else CUSTOM_STEPS
            if self._draft.mode == "quick" and self._draft.placement is None:
                self._ui.info(QUICK_EXPLANATION)
                if "placement" in self._preselected:
                    chosen = self._preselected["placement"]
                    server_dir = self._preselected.get("server-data-dir") or None
                    if chosen != "local" and not server_dir:
                        raise SetupInputError(
                            f"server-data-dir is required for {chosen} placement"
                        )
                    self._draft.placement = chosen
                    self._draft.server_data_dir = server_dir
                else:
                    self._ui.info(PLACEMENT_LOCAL_DEFAULT)
                    self._draft.placement = "local"
            index = 0
            while index < len(steps):
                step = steps[index]
                if step in self._draft.completed:
                    index += 1
                    continue
                self._ui.step(index + 1, len(steps), STEP_TITLES[step])
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

    # -------------------------------------------------------------------- mode

    def _step_mode(self) -> None:
        if "mode" in self._preselected:
            mode = self._preselected["mode"]
        else:
            mode = self._ui.choose(
                "mode",
                MODE_TITLE,
                (
                    MenuOption("quick", MODE_QUICK),
                    MenuOption("custom", MODE_CUSTOM),
                ),
            )
        if mode not in ("quick", "custom"):
            raise SetupInputError(f"Unknown setup mode: {mode}")
        self._draft.mode = cast(SetupMode, mode)

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
            if self._ui.confirm(
                "provider-test-detected", PROVIDER_ASK_TEST, default=False
            ):
                options = tuple(
                    MenuOption(definition.id, definition.display_name)
                    for definition, _ in detected
                ) + (MenuOption("another", "Choose another provider"),)
                chosen = self._ui.choose(
                    "provider-detected", PROVIDER_TITLE, options
                )
                if chosen == "another":
                    return self._choose_provider_from_catalog()
                return find_provider(chosen)
        else:
            preference = self._ui.choose(
                "provider-preference",
                PROVIDER_PREFERENCE_TITLE,
                (
                    MenuOption("direct", PROVIDER_PREFERENCE_CLOUD),
                    MenuOption("gateway", PROVIDER_PREFERENCE_GATEWAY),
                    MenuOption("local", PROVIDER_PREFERENCE_LOCAL),
                    MenuOption("browse", "Browse the full catalog"),
                ),
            )
            if preference != "browse":
                group: ProviderGroup = (
                    "local"
                    if preference == "local"
                    else "gateway"
                    if preference == "gateway"
                    else "direct"
                )
                candidates = [
                    item for item in providers_in_group(group) if item.available
                ]
                if candidates:
                    recommended = candidates[0]
                    self._ui.info(
                        f"Recommended: {recommended.display_name} — "
                        f"{recommended.summary}"
                    )
                    if self._ui.confirm(
                        "provider-use-recommendation",
                        f"Use {recommended.display_name}?",
                        default=True,
                    ):
                        return recommended
        return self._choose_provider_from_catalog()

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

    def _choose_provider_from_catalog(self) -> ProviderDefinition:
        while True:
            options: list[MenuOption] = []
            for group in GROUP_ORDER:
                for item in providers_in_group(group):
                    if not item.available:
                        continue
                    badges = f" ({'; '.join(item.badges)})" if item.badges else ""
                    options.append(
                        MenuOption(
                            item.id,
                            f"{item.display_name}{badges}",
                            note=GROUP_LABELS[group],
                        )
                    )
            coming = coming_soon_providers()
            if coming:
                options.append(
                    MenuOption(
                        "coming-soon",
                        f"{PROVIDER_COMING_SOON}: "
                        + ", ".join(item.display_name for item in coming),
                        available=False,
                        dim=True,
                        note="These pass the provider contract in later waves.",
                    )
                )
            chosen = self._ui.choose(
                "provider", PROVIDER_TITLE, tuple(options), allow_back=True
            )
            if chosen == "coming-soon":
                continue
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
                        "Demo mode: the deterministic fake provider is ready."
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
            raw_key = self._ui.ask(
                "provider-custom-key",
                "API key (input hidden; leave empty for keyless): ",
                secret=True,
            )
            api_key = raw_key or None
            credential_kind = "store" if api_key else "none"
        if definition.id == "fake":
            model = "deterministic-fake"
        else:
            model = self._choose_model(definition, api_key, base_url)
        self._draft.provider_credential = credential_kind
        self._pending_key = api_key
        return AdapterConfig(api_key=api_key, base_url=base_url, model=model)

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
        api_key: str | None,
        base_url: str | None,
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
                AdapterConfig(api_key=api_key, base_url=base_url),
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
            f"Real provider ready: {definition.display_name} answered in "
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
        if definition.id == "fake" or definition.auth == "keyless-local":
            return
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
        if entries:
            self._ui.info(PROFILE_SAVED_AS_PROPOSALS)

    # -------------------------------------------------------------- connectors

    def _step_connectors(self) -> None:
        while True:
            choice = self._ui.choose(
                "connectors",
                CONNECTOR_TITLE,
                (
                    MenuOption("telegram", CONNECTOR_TELEGRAM),
                    MenuOption(
                        "coming-soon",
                        f"{CONNECTOR_COMING_SOON}: Discord, WhatsApp, Slack, "
                        "Signal, Matrix",
                        available=False,
                        dim=True,
                    ),
                    MenuOption("skip", "Skip — set up connectors later"),
                ),
                allow_back=True,
            )
            if choice == "skip":
                self._draft.telegram_status = telegram_connector_status(
                    self._paths.telegram_config_path
                )
                return
            if choice == "coming-soon":
                continue
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
                    "What do you want to do with this exchange?",
                    (
                        MenuOption("accept", "Accept this exchange"),
                        MenuOption("retry", "Retry with a new message"),
                        MenuOption("edit-persona", "Edit persona"),
                        MenuOption("change-model", "Change model"),
                        MenuOption("change-provider", "Change provider"),
                        MenuOption("exit", "Exit (keep the verified provider)"),
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
                    definition, config.api_key, config.base_url
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
        return AdapterConfig(api_key=api_key, base_url=base_url, model=model)

    # ------------------------------------------------------------------ review

    def _step_review(self) -> None:
        definition = find_provider(self._draft.provider_id or "fake")
        persona = self._persona_definition()
        lines = [
            f"Placement: {self._draft.placement or 'local'}",
            f"Persona: {persona.name} ({persona.id})",
            f"Provider: {definition.display_name}",
            f"Model: {self._draft.provider_model or 'deterministic-fake'}",
            f"Profile proposals: {self._draft.profile_proposal_count}",
            f"Telegram: {self._draft.telegram_status or 'not configured'}",
        ]
        if definition.group in ("direct", "gateway") and self._draft.placement == "local":
            lines.append(PLACEMENT_CLOUD_NOTE)
        for line in lines:
            self._ui.info(line)
        if not self._ui.confirm("review-confirm", REVIEW_CONFIRM, default=True):
            raise SetupBack

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
            "mode": self._draft.mode or "quick",
            "provider": provider_id,
            "model": model_name,
            "demo": demo,
            "activated_at": datetime.now(UTC).isoformat(),
        }
        if self._accepted is None:
            raise OperationError(
                "Activation requires an accepted first conversation."
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
        except (OperationError, OSError, ValueError) as error:
            raise OperationError(
                f"Activation failed at the installation step: {error}. "
                "Nothing was activated; run reckoning setup to resume."
            ) from error
        if provider_id != "fake":
            self._store.default_provider = provider_id
            self._store.save(self._paths.credentials_path)
        self._update_telegram_provider(provider_id)
        self._prove_reopen()
        self._paths.draft_path.unlink(missing_ok=True)
        if demo:
            self._ui.success(
                "Demo mode: setup completed with the deterministic fake "
                "provider. No paid provider is active."
            )
        else:
            self._ui.success(
                f"Real provider ready: {definition.display_name} is active."
            )
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
            session_path = runtime.state_path("confirmed-state", "interfaces.json")
            state = JsonFileInterfaceRepository(session_path).load()
        except (OperationError, RuntimeError, ValueError) as error:
            raise OperationError(
                f"Activation failed at the reopen check: {error}."
            ) from error
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

    def manage_section(self, section: str) -> SetupOutcome:
        """Focused entry point for `reckoning provider/persona/channel`."""
        if section not in ("persona", "provider", "profile", "connectors"):
            raise SetupInputError(f"Unknown section: {section}")
        if not (self._paths.data_dir / "instance.json").exists():
            raise OperationError(
                "No installation is configured; run reckoning setup first."
            )
        self._ui.banner()
        self._edit_section(section)
        return SetupOutcome(status="managed", data_dir=self._paths.data_dir)

    # ------------------------------------------------------- configured state

    def _run_configured(self) -> SetupOutcome:
        self._maybe_offer_migration()
        while True:
            sections = self._status_sections()
            self._ui.info(STATUS_TITLE)
            for section in sorted(sections, key=lambda item: item.ok):
                marker = "ok" if section.ok else "FAILING"
                render = f"[{marker}] {section.name}: {section.detail}"
                if section.ok:
                    self._ui.info(render)
                else:
                    self._ui.failure(render)
            failing = [item.name for item in sections if not item.ok]
            action = self._ui.choose(
                "status-action",
                "What next?",
                (
                    MenuOption("verify-all", STATUS_VERIFY_ALL),
                    MenuOption("repair", STATUS_REPAIR, available=bool(failing)),
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
                    self._ui.info("Nothing is failing.")
                    continue
                target = self._ui.choose(
                    "repair-section",
                    "Repair which section?",
                    tuple(MenuOption(name, name) for name in failing),
                )
                self._edit_section(target)
            elif action == "edit":
                target = self._ui.choose(
                    "edit-section",
                    "Edit which section?",
                    tuple(
                        MenuOption(name, name)
                        for name in (
                            "persona",
                            "provider",
                            "profile",
                            "connectors",
                        )
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
            SectionStatus("placement", True, f"{placement}")
        )
        try:
            persona = PersonaService(
                JsonFilePersonaRepository(self._paths.data_dir / "personas.json")
            ).active()
            sections.append(
                SectionStatus("persona", True, persona.definition.name)
            )
        except (KeyError, LookupError, RuntimeError, ValueError) as error:
            sections.append(SectionStatus("persona", False, str(error)))
        default = self._store.default_provider
        credential = (
            self._store.credential_for(default) if default else None
        )
        if isinstance(activation, dict) and activation.get("provider") == "fake":
            sections.append(SectionStatus("provider", True, "fake (demo mode)"))
        elif default and credential is not None:
            state = "verified" if credential.verified else "INACTIVE (unverified)"
            model = credential.model or "default model"
            when = credential.verified_at or "never"
            sections.append(
                SectionStatus(
                    "provider",
                    credential.verified,
                    f"{default}, model {model}, {state}, last verified {when}",
                )
            )
        else:
            sections.append(
                SectionStatus("provider", False, "no active provider credential")
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
                    "profile", True, f"{len(proposed)} unconfirmed proposals"
                )
            )
        except (OperationError, RuntimeError, ValueError) as error:
            sections.append(SectionStatus("profile", False, str(error)))
        telegram = telegram_connector_status(self._paths.telegram_config_path)
        sections.append(
            SectionStatus(
                "connectors",
                telegram not in ("error",),
                f"telegram {telegram}; gateway is not started by setup "
                "(reckoning gateway runs it)",
            )
        )
        return tuple(sections)

    def _verify_all(self) -> None:
        default = self._store.default_provider
        credential = (
            self._store.credential_for(default) if default else None
        )
        if default and credential is not None:
            definition = find_provider(default)
            if definition.group in ("direct", "gateway"):
                if not self._ui.confirm(
                    "verify-paid-refresh", STATUS_PAID_REFRESH, default=False
                ):
                    self._ui.info("Skipped the paid provider check.")
                else:
                    self._verify_saved_provider(default, credential)
            else:
                self._verify_saved_provider(default, credential)
        else:
            self._ui.info("No stored provider credential to verify.")
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

    def _verify_saved_provider(
        self, provider_id: str, credential: StoredCredential
    ) -> None:
        definition = find_provider(provider_id)
        api_key = credential.resolve_secret(self._services.environ)
        if api_key == "endpoint-only":
            api_key = None
        config = AdapterConfig(
            api_key=api_key,
            base_url=credential.base_url,
            model=credential.model,
        )
        try:
            result = self._services.adapter_for(provider_id).verify(
                config, transport=self._services.transport
            )
        except (ProviderVerificationError, KeyError) as error:
            self._ui.failure(f"{definition.display_name}: {error}")
            return
        self._store.mark_verified(provider_id)
        self._store.save(self._paths.credentials_path)
        self._ui.success(
            f"{definition.display_name} verified in {result.latency_ms} ms."
        )

    def _edit_section(self, section: str) -> None:
        if section == "persona":
            self._edit_installed_persona()
        elif section == "provider":
            self._step_provider()
            provider_id = self._draft.provider_id
            if provider_id and provider_id != "fake":
                self._store.default_provider = provider_id
                self._store.save(self._paths.credentials_path)
                self._update_telegram_provider(provider_id)
            self._paths.draft_path.unlink(missing_ok=True)
        elif section == "profile":
            self._step_profile()
            self._append_installed_profile_entries()
        elif section == "connectors":
            self._setup_telegram()
        else:
            raise SetupInputError(f"Unknown section: {section}")

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
        self._ui.warning(
            "This installation predates the current setup format and needs "
            "migration."
        )
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
        changes = [
            "instance.json gains an activation record",
            "provider credentials rewrite to schema v2 (backup kept)",
            "telegram config rewrites to schema v2 (backup kept)",
        ]
        for change in changes:
            self._ui.info(f"- {change}")
        if not self._ui.confirm(
            "migrate-confirm", "Apply these changes?", default=True
        ):
            return
        self._migrate_installation(instance)
        self._ui.success("Migration committed; backups sit next to the files.")

    def _migrate_installation(self, instance: dict[str, Any]) -> None:
        import shutil

        for path in (
            self._paths.credentials_path,
            self._paths.telegram_config_path,
            self._paths.data_dir / "instance.json",
        ):
            if path.exists():
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        store = ProviderCredentialStore.load(self._paths.credentials_path)
        migrated = {
            **instance,
            "activation": {
                "status": "activated",
                "mode": "migrated",
                "provider": store.default_provider or "fake",
                "model": None,
                "demo": store.default_provider is None,
                "activated_at": instance.get("created_at"),
                "migrated_at": datetime.now(UTC).isoformat(),
            },
        }
        atomic_write_json(self._paths.data_dir / "instance.json", migrated)
        if store.providers:
            store.save(self._paths.credentials_path)
        if self._paths.telegram_config_path.exists():
            try:
                config = TelegramConnectorConfig.load(
                    self._paths.telegram_config_path
                )
            except ValueError:
                return
            config.save(self._paths.telegram_config_path)
