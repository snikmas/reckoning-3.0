from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from reckoning.json_store import atomic_write_json, read_json

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


@dataclass(frozen=True)
class ProviderCredentials:
    """The verified provider API key saved by terminal setup."""

    provider_name: str
    api_key: str = field(repr=False)

    @classmethod
    def load(
        cls,
        path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    ) -> ProviderCredentials | None:
        data = read_json(path, default={})
        provider_name = str(data.get("provider_name", "")).strip().casefold()
        api_key = str(data.get("api_key", "")).strip()
        if provider_name not in ("deepseek", "orcarouter") or not api_key:
            return None
        return cls(provider_name, api_key)

    def save(self, path: Path = DEFAULT_PROVIDER_CREDENTIALS) -> None:
        atomic_write_json(
            path,
            {"provider_name": self.provider_name, "api_key": self.api_key},
        )
        path.chmod(0o600)


def _credential_api_key(
    credential_file: Path | None,
    provider_name: str,
) -> str | None:
    if credential_file is None:
        return None
    credentials = ProviderCredentials.load(credential_file)
    if credentials is None or credentials.provider_name != provider_name:
        return None
    return credentials.api_key


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
