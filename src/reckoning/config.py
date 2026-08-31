from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

ORCAROUTER_DEFAULT_MODEL = "orcarouter/auto"
ORCAROUTER_DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_ALLOWED_ENV_NAMES = (
    "ORCAROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MODEL",
    "BASE_URL",
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
    ) -> OrcaRouterSettings:
        file_values = _read_allowed_env_file(env_file)
        process_values = os.environ if environ is None else environ

        def value(name: str) -> str | None:
            raw = process_values.get(name, file_values.get(name))
            cleaned = raw.strip() if raw else ""
            return cleaned or None

        return cls(
            api_key=value("ORCAROUTER_API_KEY"),
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
    ) -> DeepSeekSettings:
        file_values = _read_allowed_env_file(env_file)
        process_values = os.environ if environ is None else environ

        def value(name: str) -> str | None:
            raw = process_values.get(name, file_values.get(name))
            cleaned = raw.strip() if raw else ""
            return cleaned or None

        return cls(
            api_key=value("DEEPSEEK_API_KEY"),
            model=value("MODEL") or DEEPSEEK_DEFAULT_MODEL,
            base_url=value("BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL,
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
