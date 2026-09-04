from __future__ import annotations

from collections.abc import Collection
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from reckoning.json_store import atomic_write_json, read_json

if TYPE_CHECKING:
    from reckoning.application import PersonaSettings

Voice = Literal["composed", "reflective", "formal", "candid", "energetic"]
Directness = Literal["gentle", "balanced", "direct", "severe"]
Warmth = Literal["reserved", "balanced", "warm"]
Humor = Literal["none", "dry", "light"]
Challenge = Literal["supportive", "probing", "demanding", "uncomfortable"]
SensitiveTopicHandling = Literal["warm", "calm", "practical"]

_PERSONA_FILE_FIELDS = {
    "schema_version",
    "authored",
    "active_persona_id",
}
_PERSONA_DEFINITION_FIELDS = {
    "id",
    "name",
    "voice",
    "directness",
    "warmth",
    "humor",
    "challenge",
    "sensitive_topic_handling",
}

_VOICE_INSTRUCTIONS: dict[Voice, str] = {
    "composed": "Use a composed voice.",
    "reflective": "Use a reflective voice.",
    "formal": "Use a formal voice.",
    "candid": "Use a candid voice.",
    "energetic": "Use an energetic voice.",
}
_DIRECTNESS_INSTRUCTIONS: dict[Directness, str] = {
    "gentle": "Make recommendations gently.",
    "balanced": "Give balanced, plain recommendations.",
    "direct": "Give direct recommendations.",
    "severe": "Be severe about weak reasoning while staying precise.",
}
_WARMTH_INSTRUCTIONS: dict[Warmth, str] = {
    "reserved": "Use reserved warmth.",
    "balanced": "Use balanced warmth.",
    "warm": "Use an openly warm tone.",
}
_HUMOR_INSTRUCTIONS: dict[Humor, str] = {
    "none": "Do not add humor.",
    "dry": "Use dry humor sparingly.",
    "light": "Use light humor when it helps clarity.",
}
_CHALLENGE_INSTRUCTIONS: dict[Challenge, str] = {
    "supportive": "Apply supportive challenge to the user's reasoning.",
    "probing": "Apply probing challenge to the user's reasoning.",
    "demanding": "Apply demanding challenge to the user's reasoning.",
    "uncomfortable": "Allow uncomfortable challenge to the user's reasoning.",
}
_SENSITIVE_INSTRUCTIONS: dict[SensitiveTopicHandling, str] = {
    "warm": "On sensitive topics, use warm and concrete language.",
    "calm": "On sensitive topics, use a calm and steady tone.",
    "practical": "On sensitive topics, focus on practical next steps.",
}


@dataclass(frozen=True)
class PersonaAxisChoice:
    value: str
    explanation: str
    example: str


@dataclass(frozen=True)
class PersonaAxis:
    field: str
    label: str
    choices: tuple[PersonaAxisChoice, ...]


PERSONA_AXES: tuple[PersonaAxis, ...] = (
    PersonaAxis(
        "voice",
        "Voice — how the agent sounds overall",
        (
            PersonaAxisChoice("composed", "Calm and measured.", "“Here is the situation, plainly.”"),
            PersonaAxisChoice("reflective", "Thinks out loud with you.", "“Let us look at what changed.”"),
            PersonaAxisChoice("formal", "Precise and structured.", "“Your options are as follows.”"),
            PersonaAxisChoice("candid", "Casual and unvarnished.", "“Honestly, that plan is thin.”"),
            PersonaAxisChoice("energetic", "Upbeat and forward-moving.", "“Good — now the next move.”"),
        ),
    ),
    PersonaAxis(
        "directness",
        "Directness — how plainly it states recommendations",
        (
            PersonaAxisChoice("gentle", "Softens recommendations.", "“You might consider waiting.”"),
            PersonaAxisChoice("balanced", "Plain without pressure.", "“Waiting is the safer option.”"),
            PersonaAxisChoice("direct", "States the recommendation.", "“Wait. Do not commit yet.”"),
            PersonaAxisChoice("severe", "Attacks weak reasoning hard.", "“That argument does not hold.”"),
        ),
    ),
    PersonaAxis(
        "warmth",
        "Warmth — how much personal warmth it shows",
        (
            PersonaAxisChoice("reserved", "Keeps distance.", "“Noted. Continue.”"),
            PersonaAxisChoice("balanced", "Friendly but focused.", "“Good progress. Continue.”"),
            PersonaAxisChoice("warm", "Openly supportive.", "“That was a hard week — well held.”"),
        ),
    ),
    PersonaAxis(
        "humor",
        "Humor — whether it jokes",
        (
            PersonaAxisChoice("none", "No humor.", "“The deadline moved to Friday.”"),
            PersonaAxisChoice("dry", "Occasional dry wit.", "“Friday, again. Predictably.”"),
            PersonaAxisChoice("light", "Light humor when it helps.", "“Friday has escaped once more.”"),
        ),
    ),
    PersonaAxis(
        "challenge",
        "Challenge — how hard it pushes your reasoning",
        (
            PersonaAxisChoice("supportive", "Encourages first.", "“You have thought this through.”"),
            PersonaAxisChoice("probing", "Asks pointed questions.", "“What evidence changed?”"),
            PersonaAxisChoice("demanding", "Requires solid reasons.", "“Show me why this works.”"),
            PersonaAxisChoice("uncomfortable", "Names what you avoid.", "“You are avoiding the real choice.”"),
        ),
    ),
    PersonaAxis(
        "sensitive_topic_handling",
        "Sensitive topics — how it handles hard personal subjects",
        (
            PersonaAxisChoice("warm", "Warm and concrete.", "“That sounds painful; here is what helps.”"),
            PersonaAxisChoice("calm", "Calm and steady.", "“We can take this one step at a time.”"),
            PersonaAxisChoice("practical", "Focuses on next steps.", "“The practical next step is this.”"),
        ),
    ),
)

AUTONOMY_FLOOR: tuple[str, ...] = (
    "Truthfulness: the agent does not lie to you, whatever the persona.",
    "Permissions: no persona can widen what the agent may do.",
    "Memory confirmation: personal records are confirmed only by you.",
    "User authority: you always keep the final decision.",
    "Relational rules: no dependence, exclusivity, or isolation — ever.",
)


def blank_persona_template(persona_id: str, name: str) -> PersonaDefinition:
    """A balanced starting point for an authored persona."""
    return PersonaDefinition(
        id=persona_id,
        name=name,
        voice="composed",
        directness="balanced",
        warmth="balanced",
        humor="none",
        challenge="probing",
        sensitive_topic_handling="calm",
    )


def persona_from_preset(
    preset_id: str,
    new_id: str,
    new_name: str,
    *,
    defaults: tuple[PersonaDefinition, ...] | None = None,
) -> PersonaDefinition:
    presets = DEFAULT_PERSONAS if defaults is None else defaults
    preset = next((item for item in presets if item.id == preset_id), None)
    if preset is None:
        raise KeyError(f"Unknown persona preset: {preset_id}")
    return replace(preset, id=new_id, name=new_name)


def describe_persona(definition: PersonaDefinition) -> tuple[str, ...]:
    """A deterministic plain-language preview; no provider is involved."""
    lines = [f"{definition.name} speaks like this:"]
    for axis in PERSONA_AXES:
        value = getattr(definition, axis.field)
        choice = next(item for item in axis.choices if item.value == value)
        lines.append(f"- {axis.label.split(' — ')[0]}: {choice.explanation} {choice.example}")
    return tuple(lines)


def _allowed(value: str, options: Collection[str], label: str) -> None:
    if value not in options:
        raise ValueError(
            f"Persona {label} must be one of: {', '.join(sorted(options))}."
        )


@dataclass(frozen=True)
class PersonaDefinition:
    """Installer-configurable style only; protected rules are not fields."""

    id: str
    name: str
    voice: Voice
    directness: Directness
    warmth: Warmth
    humor: Humor
    challenge: Challenge
    sensitive_topic_handling: SensitiveTopicHandling

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.id):
            raise ValueError("Persona id must be a lowercase slug.")
        if (
            not self.name.strip()
            or len(self.name) > 60
            or not self.name.isprintable()
        ):
            raise ValueError("Persona name must be printable and at most 60 characters.")
        _allowed(self.voice, _VOICE_INSTRUCTIONS, "voice")
        _allowed(self.directness, _DIRECTNESS_INSTRUCTIONS, "directness")
        _allowed(self.warmth, _WARMTH_INSTRUCTIONS, "warmth")
        _allowed(self.humor, _HUMOR_INSTRUCTIONS, "humor")
        _allowed(self.challenge, _CHALLENGE_INSTRUCTIONS, "challenge")
        _allowed(
            self.sensitive_topic_handling,
            _SENSITIVE_INSTRUCTIONS,
            "sensitive topic handling",
        )


@dataclass(frozen=True)
class SelectedPersona:
    """One style expression of the single accountable Reckoning agent."""

    definition: PersonaDefinition

    @property
    def prompt_instructions(self) -> str:
        style = self.definition
        return " ".join(
            (
                _VOICE_INSTRUCTIONS[style.voice],
                _DIRECTNESS_INSTRUCTIONS[style.directness],
                _WARMTH_INSTRUCTIONS[style.warmth],
                _HUMOR_INSTRUCTIONS[style.humor],
                _CHALLENGE_INSTRUCTIONS[style.challenge],
                _SENSITIVE_INSTRUCTIONS[style.sensitive_topic_handling],
            )
        )

    def to_application_settings(self) -> PersonaSettings:
        from reckoning.application import PersonaSettings

        return PersonaSettings(
            name=self.definition.name,
            instructions=self.prompt_instructions,
        )

    @property
    def accountable_agent_id(self) -> str:
        return "reckoning-agent"

    @property
    def memory_owner(self) -> str:
        return self.accountable_agent_id

    @property
    def authority_owner(self) -> str:
        return self.accountable_agent_id

    @property
    def persona_owns_memory(self) -> bool:
        return False

    @property
    def persona_owns_authority(self) -> bool:
        return False

    @property
    def persona_can_confirm_records(self) -> bool:
        return False

    @property
    def persona_can_answer_independently(self) -> bool:
        return False


DEFAULT_PERSONAS: tuple[PersonaDefinition, ...] = (
    PersonaDefinition(
        id="simon",
        name="Simon",
        voice="composed",
        directness="direct",
        warmth="reserved",
        humor="dry",
        challenge="demanding",
        sensitive_topic_handling="warm",
    ),
    PersonaDefinition(
        id="steady",
        name="Steady",
        voice="reflective",
        directness="balanced",
        warmth="warm",
        humor="light",
        challenge="probing",
        sensitive_topic_handling="calm",
    ),
)


class PersonaRepository(Protocol):
    def save_authored(self, definition: PersonaDefinition) -> None: ...

    def list_authored(self) -> tuple[PersonaDefinition, ...]: ...

    def remove_authored(self, persona_id: str) -> bool: ...

    def set_active(self, persona_id: str) -> None: ...

    def active_id(self) -> str | None: ...


class InMemoryPersonaRepository:
    def __init__(self) -> None:
        self._authored: dict[str, PersonaDefinition] = {}
        self._active_id: str | None = None

    def save_authored(self, definition: PersonaDefinition) -> None:
        self._authored[definition.id] = definition

    def list_authored(self) -> tuple[PersonaDefinition, ...]:
        return tuple(self._authored.values())

    def remove_authored(self, persona_id: str) -> bool:
        return self._authored.pop(persona_id, None) is not None

    def set_active(self, persona_id: str) -> None:
        self._active_id = persona_id

    def active_id(self) -> str | None:
        return self._active_id


class JsonFilePersonaRepository(InMemoryPersonaRepository):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        data = read_json(
            path,
            default={
                "schema_version": 1,
                "authored": [],
                "active_persona_id": None,
            },
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported persona storage schema.")
        if set(data) != _PERSONA_FILE_FIELDS:
            raise RuntimeError("Stored persona configuration is invalid.")
        authored = data.get("authored")
        if not isinstance(authored, list):
            raise RuntimeError("Stored persona configuration is invalid.")
        try:
            definitions = tuple(
                _definition_from_data(item)
                for item in authored
                if isinstance(item, dict)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Stored persona configuration is invalid.") from error
        if len(definitions) != len(authored):
            raise RuntimeError("Stored persona configuration is invalid.")
        self._authored = {item.id: item for item in definitions}
        active_id = data.get("active_persona_id")
        if active_id is not None and not isinstance(active_id, str):
            raise RuntimeError("Stored active persona is invalid.")
        self._active_id = active_id

    def save_authored(self, definition: PersonaDefinition) -> None:
        super().save_authored(definition)
        self._flush()

    def remove_authored(self, persona_id: str) -> bool:
        removed = super().remove_authored(persona_id)
        if removed:
            self._flush()
        return removed

    def set_active(self, persona_id: str) -> None:
        super().set_active(persona_id)
        self._flush()

    def _flush(self) -> None:
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "authored": [asdict(item) for item in self._authored.values()],
                "active_persona_id": self._active_id,
            },
        )


class PersonaService:
    def __init__(
        self,
        repository: PersonaRepository,
        defaults: tuple[PersonaDefinition, ...] = DEFAULT_PERSONAS,
    ) -> None:
        self._repository = repository
        self._defaults = {item.id: item for item in defaults}
        if len(self._defaults) != len(defaults):
            raise ValueError("Default persona ids must be unique.")

    def list_defaults(self) -> tuple[PersonaDefinition, ...]:
        return tuple(self._defaults.values())

    def list_authored(self) -> tuple[PersonaDefinition, ...]:
        return self._repository.list_authored()

    def author_and_select(self, definition: PersonaDefinition) -> SelectedPersona:
        if definition.id in self._defaults:
            raise ValueError("An authored persona cannot replace a default persona.")
        self._repository.save_authored(definition)
        return self.select(definition.id)

    def is_default(self, persona_id: str) -> bool:
        return persona_id in self._defaults

    def duplicate(
        self, source_id: str, new_id: str, new_name: str
    ) -> PersonaDefinition:
        """Copy any persona into an authored one; presets stay immutable."""
        source = self._find(source_id)
        if new_id in self._defaults:
            raise ValueError("A duplicate cannot replace a default persona.")
        authored = {item.id: item for item in self._repository.list_authored()}
        if new_id in authored:
            raise ValueError(f"A persona already exists with id: {new_id}")
        copy = replace(source, id=new_id, name=new_name)
        self._repository.save_authored(copy)
        return copy

    def update_authored(self, definition: PersonaDefinition) -> None:
        if definition.id in self._defaults:
            raise ValueError("Built-in persona presets are immutable.")
        authored = {item.id: item for item in self._repository.list_authored()}
        if definition.id not in authored:
            raise KeyError(f"Unknown persona: {definition.id}")
        self._repository.save_authored(definition)

    def rename_authored(self, persona_id: str, new_name: str) -> PersonaDefinition:
        definition = self._find(persona_id)
        if persona_id in self._defaults:
            raise ValueError("Built-in persona presets are immutable.")
        renamed = replace(definition, name=new_name)
        self._repository.save_authored(renamed)
        return renamed

    def remove_authored(self, persona_id: str) -> None:
        if persona_id in self._defaults:
            raise ValueError("Built-in persona presets cannot be removed.")
        if self._repository.active_id() == persona_id:
            raise ValueError(
                "The active persona must be replaced before it can be removed."
            )
        if not self._repository.remove_authored(persona_id):
            raise KeyError(f"Unknown persona: {persona_id}")

    def select(self, persona_id: str) -> SelectedPersona:
        definition = self._find(persona_id)
        self._repository.set_active(definition.id)
        return SelectedPersona(definition)

    def active(self) -> SelectedPersona:
        active_id = self._repository.active_id()
        if active_id is None:
            raise LookupError("No persona has been selected.")
        return SelectedPersona(self._find(active_id))

    def _find(self, persona_id: str) -> PersonaDefinition:
        if persona_id in self._defaults:
            return self._defaults[persona_id]
        authored = {
            item.id: item for item in self._repository.list_authored()
        }
        try:
            return authored[persona_id]
        except KeyError as error:
            raise KeyError(f"Unknown persona: {persona_id}") from error


def _definition_from_data(data: dict[str, Any]) -> PersonaDefinition:
    if set(data) != _PERSONA_DEFINITION_FIELDS:
        raise ValueError("A stored persona may contain only style fields.")
    return PersonaDefinition(
        id=str(data["id"]),
        name=str(data["name"]),
        voice=cast(Voice, data["voice"]),
        directness=cast(Directness, data["directness"]),
        warmth=cast(Warmth, data["warmth"]),
        humor=cast(Humor, data["humor"]),
        challenge=cast(Challenge, data["challenge"]),
        sensitive_topic_handling=cast(
            SensitiveTopicHandling, data["sensitive_topic_handling"]
        ),
    )
