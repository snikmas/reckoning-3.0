from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from reckoning.json_store import atomic_write_json, read_json
from reckoning.provider_registry import available_providers, find_provider
from reckoning.provider_validation import (
    is_safe_header_value,
    is_valid_env_name,
    is_valid_header_name,
)

ORCAROUTER_DEFAULT_MODEL = "orcarouter/auto"
ORCAROUTER_DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_ALLOWED_ENV_NAMES = (
    "ORCAROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "BASE_DEEPSEEK_URL",
    "MODEL",
    "BASE_URL",
)

DEFAULT_PROVIDER_CREDENTIALS = Path.home() / ".config" / "reckoning" / "provider.json"

ENV_REFERENCE_PREFIX = "env:"

CREDENTIAL_PROVIDER_NAMES = tuple(item.id for item in available_providers())


def env_reference(env_name: str) -> str:
    """The stored form of a credential that lives in the environment."""
    return f"{ENV_REFERENCE_PREFIX}{env_name.strip()}"


def is_env_reference(secret: str) -> bool:
    return secret.startswith(ENV_REFERENCE_PREFIX)


def _credential_provider_ids() -> frozenset[str]:
    return frozenset(CREDENTIAL_PROVIDER_NAMES)


@dataclass(frozen=True)
class StoredCredential:
    """One provider credential entry; secrets never appear in reprs."""

    secret: str = field(repr=False)
    model: str | None = None
    base_url: str | None = None
    verified: bool = False
    verified_at: str | None = None

    def resolve_secret(self, environ: Mapping[str, str] | None = None) -> str | None:
        """Resolve an environment reference; stored keys return as-is."""
        if not is_env_reference(self.secret):
            return self.secret or None
        env_name = self.secret[len(ENV_REFERENCE_PREFIX) :]
        values = os.environ if environ is None else environ
        resolved = values.get(env_name, "").strip()
        return resolved or None


@dataclass
class ProviderCredentialStore:
    """All configured provider credentials plus the default-provider marker.

    The constructor accepts the legacy ``dict[str, str]`` shape; plain keys
    migrate to verified credential entries.
    """

    providers: dict[str, StoredCredential] = field(default_factory=dict)
    default_provider: str | None = None

    def __post_init__(self) -> None:
        normalized: dict[str, StoredCredential] = {}
        for name, entry in self.providers.items():
            if isinstance(entry, StoredCredential):
                normalized[name] = entry
            else:
                normalized[name] = StoredCredential(
                    secret=str(entry), verified=True
                )
        self.providers = normalized

    @classmethod
    def load(
        cls,
        path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    ) -> ProviderCredentialStore:
        data = read_json(path, default={})
        providers: dict[str, StoredCredential] = {}
        raw_providers = data.get("providers")
        entries: tuple[tuple[object, object], ...]
        if isinstance(raw_providers, dict):
            entries = tuple(raw_providers.items())
        else:
            # Legacy single-slot format: {"provider_name": ..., "api_key": ...}
            entries = ((data.get("provider_name", ""), data.get("api_key", "")),)
        known_ids = _credential_provider_ids()
        for raw_name, raw_entry in entries:
            name = str(raw_name).strip().casefold()
            if name not in known_ids:
                continue
            credential = _credential_from_data(raw_entry)
            if credential is not None:
                providers[name] = credential
        raw_default = data.get("default_provider")
        default: str | None
        if raw_default is None and "default_provider" in data:
            default = None
        else:
            default = str(raw_default or "").strip().casefold() or None
        if default is not None and default not in providers:
            default = next(iter(providers), None)
        elif default is None and "default_provider" not in data:
            default = next(iter(providers), None)
        return cls(providers=providers, default_provider=default)

    def set_key(
        self,
        provider_name: str,
        api_key: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        verified: bool = True,
        make_default: bool = False,
    ) -> None:
        self.providers[provider_name] = StoredCredential(
            secret=api_key,
            model=model,
            base_url=base_url,
            verified=verified,
            verified_at=_timestamp() if verified else None,
        )
        if make_default or self.default_provider not in self.providers:
            self.default_provider = provider_name

    def set_env_reference(
        self,
        provider_name: str,
        env_name: str,
        *,
        model: str | None = None,
        verified: bool = True,
        make_default: bool = False,
    ) -> None:
        """Store a reference to an environment variable, never its value."""
        self.providers[provider_name] = StoredCredential(
            secret=env_reference(env_name),
            model=model,
            verified=verified,
            verified_at=_timestamp() if verified else None,
        )
        if make_default or self.default_provider not in self.providers:
            self.default_provider = provider_name

    def mark_verified(self, provider_name: str) -> None:
        entry = self.providers.get(provider_name)
        if isinstance(entry, StoredCredential):
            self.providers[provider_name] = StoredCredential(
                secret=entry.secret,
                model=entry.model,
                base_url=entry.base_url,
                verified=True,
                verified_at=_timestamp(),
            )

    def credential_for(self, provider_name: str) -> StoredCredential | None:
        entry = self.providers.get(provider_name)
        return entry if isinstance(entry, StoredCredential) else None

    def is_active(self, provider_name: str) -> bool:
        """An unverified credential stays inactive and cannot be a default."""
        entry = self.credential_for(provider_name)
        return entry is not None and entry.verified

    def remove(self, provider_name: str) -> bool:
        if provider_name not in self.providers:
            return False
        del self.providers[provider_name]
        if self.default_provider == provider_name:
            self.default_provider = next(iter(self.providers), None)
        return True

    def api_key_for(
        self,
        provider_name: str,
        environ: Mapping[str, str] | None = None,
    ) -> str | None:
        entry = self.credential_for(provider_name)
        if entry is None:
            return None
        return entry.resolve_secret(environ)

    def save(self, path: Path = DEFAULT_PROVIDER_CREDENTIALS) -> None:
        atomic_write_json(
            path,
            {
                "schema_version": 2,
                "default_provider": self.default_provider,
                "providers": {
                    name: {
                        "secret": entry.secret,
                        "model": entry.model,
                        "base_url": entry.base_url,
                        "verified": entry.verified,
                        "verified_at": entry.verified_at,
                    }
                    for name, entry in self.providers.items()
                },
            },
        )
        path.chmod(0o600)


def _credential_from_data(raw_entry: object) -> StoredCredential | None:
    if isinstance(raw_entry, str):
        secret = raw_entry.strip()
        return StoredCredential(secret=secret, verified=True) if secret else None
    if not isinstance(raw_entry, dict):
        return None
    secret = str(raw_entry.get("secret", "")).strip()
    if not secret:
        return None
    verified = raw_entry.get("verified") is True
    verified_at = raw_entry.get("verified_at")
    return StoredCredential(
        secret=secret,
        model=_optional_text(raw_entry.get("model")),
        base_url=_optional_text(raw_entry.get("base_url")),
        verified=verified,
        verified_at=str(verified_at) if verified and verified_at else None,
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _credential_api_key(
    credential_file: Path | None,
    provider_name: str,
) -> str | None:
    if credential_file is None:
        return None
    return ProviderCredentialStore.load(credential_file).api_key_for(provider_name)


def default_provider_name(
    credential_file: Path = DEFAULT_PROVIDER_CREDENTIALS,
) -> str | None:
    """The setup-saved default provider, when it has a usable key."""
    store = ProviderCredentialStore.load(credential_file)
    if store.default_provider is None:
        return None
    if store.api_key_for(store.default_provider) is None:
        return None
    return store.default_provider


@dataclass(frozen=True)
class RuntimeProviderSettings:
    """The provider that an activated installation must use."""

    provider_name: str
    api_key: str | None
    model: str
    base_url: str | None
    protocol: str = "openai-chat-completions"
    context_window: int | None = None
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @classmethod
    def load(
        cls,
        data_dir: Path,
        *,
        credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
        environ: Mapping[str, str] | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> RuntimeProviderSettings:
        instance = read_json(data_dir / "instance.json", default={})
        activation = instance.get("activation")
        if not isinstance(activation, dict):
            raise ValueError("The installation has no active provider configuration.")
        installed_provider = str(activation.get("provider", "")).strip().casefold()
        selected_provider = (
            provider_name or installed_provider or "fake"
        ).strip().casefold()
        try:
            definition = find_provider(selected_provider)
        except KeyError:
            raise ValueError(
                f"The active provider is not recognized: {selected_provider}"
            ) from None
        if not definition.available:
            raise ValueError(f"The active provider is not available: {selected_provider}")
        store = ProviderCredentialStore.load(credentials_path)
        credential = store.credential_for(selected_provider)

        selected_model = (
            model
            or (
                _optional_text(activation.get("model"))
                if selected_provider == installed_provider
                else None
            )
            or (credential.model if credential else None)
            or definition.recommended_model
            or ("deterministic-fake" if selected_provider == "fake" else "")
        )
        if not selected_model:
            raise ValueError(f"The active {definition.display_name} model is missing.")

        selected_base_url = (
            base_url
            or (
                _optional_text(activation.get("base_url"))
                if selected_provider == installed_provider
                else None
            )
            or (credential.base_url if credential else None)
            or definition.base_url
        )
        api_key = credential.resolve_secret(environ) if credential else None
        if api_key == "endpoint-only":
            api_key = None
        if definition.needs_api_key:
            if credential is None or not credential.verified or not api_key:
                raise ValueError(
                    f"The active {definition.display_name} credential is not verified."
                )
        elif definition.auth == "custom-endpoint" and (
            credential is None or not credential.verified
        ):
            raise ValueError("The active custom endpoint is not verified.")

        uses_activation = selected_provider == installed_provider
        protocol = (
            str(activation.get("protocol", "openai-chat-completions"))
            if uses_activation
            else "openai-chat-completions"
        )
        if protocol != "openai-chat-completions":
            raise ValueError("The active provider protocol is not supported.")
        raw_context_window = (
            activation.get("context_window") if uses_activation else None
        )
        if raw_context_window is not None and (
            type(raw_context_window) is not int or raw_context_window <= 0
        ):
            raise ValueError("The active provider context size is invalid.")
        context_window = raw_context_window
        raw_header_env = activation.get("header_env", {}) if uses_activation else {}
        if not isinstance(raw_header_env, dict):
            raise ValueError("The active provider header references are invalid.")
        if raw_header_env and definition.auth != "custom-endpoint":
            raise ValueError("Custom headers require an active custom endpoint.")
        values = os.environ if environ is None else environ
        headers: list[tuple[str, str]] = []
        for raw_name, raw_env_name in raw_header_env.items():
            if not is_valid_header_name(raw_name) or not is_valid_env_name(
                raw_env_name
            ):
                raise ValueError("The active provider header references are invalid.")
            name = raw_name.strip()
            env_name = raw_env_name.strip()
            value = values.get(env_name, "").strip()
            if not is_safe_header_value(value):
                raise ValueError(
                    f"The active provider header environment variable {env_name} "
                    "is not set."
                )
            headers.append((name, value))

        return cls(
            provider_name=selected_provider,
            api_key=api_key,
            model=selected_model,
            base_url=selected_base_url,
            protocol=protocol,
            context_window=context_window,
            headers=tuple(headers),
        )


@dataclass(frozen=True)
class OrcaRouterSettings:
    api_key: str | None
    model: str
    base_url: str

    @classmethod
    def load(
        cls,
        env_file: Path = Path(".env"),
        environ: Mapping[str, str] | None = None,
        credential_file: Path | None = None,
    ) -> OrcaRouterSettings:
        file_values = _read_allowed_env_file(env_file)
        process_values = os.environ if environ is None else environ

        def value(name: str) -> str | None:
            raw = process_values.get(name, file_values.get(name))
            cleaned = raw.strip() if raw else ""
            return cleaned or None

        return cls(
            api_key=value("ORCAROUTER_API_KEY")
            or _credential_api_key(credential_file, "orcarouter"),
            model=value("MODEL") or ORCAROUTER_DEFAULT_MODEL,
            base_url=value("BASE_URL") or ORCAROUTER_DEFAULT_BASE_URL,
        )


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str | None
    model: str
    base_url: str

    @classmethod
    def load(
        cls,
        env_file: Path = Path(".env"),
        environ: Mapping[str, str] | None = None,
        credential_file: Path | None = None,
    ) -> DeepSeekSettings:
        file_values = _read_allowed_env_file(env_file)
        process_values = os.environ if environ is None else environ

        def value(*names: str) -> str | None:
            for values in (process_values, file_values):
                for name in names:
                    raw = values.get(name)
                    cleaned = raw.strip() if raw else ""
                    if cleaned:
                        return cleaned
            return None

        return cls(
            api_key=value("DEEPSEEK_API_KEY")
            or _credential_api_key(credential_file, "deepseek"),
            model=value("DEEPSEEK_MODEL", "MODEL") or DEEPSEEK_DEFAULT_MODEL,
            base_url=value("BASE_DEEPSEEK_URL", "BASE_URL")
            or DEEPSEEK_DEFAULT_BASE_URL,
        )


def _read_allowed_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeError(f"Could not read provider configuration from {path}.") from error
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        name, separator, raw_value = stripped.partition("=")
        name = name.strip()
        if not separator or name not in _ALLOWED_ENV_NAMES:
            continue
        values[name] = _unquote(raw_value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value.split(" #", 1)[0].rstrip()
