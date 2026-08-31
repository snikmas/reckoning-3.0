from __future__ import annotations

from pathlib import Path

from reckoning.config import OrcaRouterSettings


def test_orcarouter_settings_load_only_allowed_values_from_env_file(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORCAROUTER_API_KEY='test-key'\n"
        'MODEL="orcarouter/auto"\n'
        'BASE_URL="https://api.orcarouter.ai/v1"\n'
        "UNRELATED_SECRET=must-not-load\n",
        encoding="utf-8",
    )

    settings = OrcaRouterSettings.load(env_file, environ={})

    assert settings.api_key == "test-key"
    assert settings.model == "orcarouter/auto"
    assert settings.base_url == "https://api.orcarouter.ai/v1"
    assert "UNRELATED_SECRET" not in settings.__dict__


def test_process_environment_overrides_file_without_requiring_all_values(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ORCAROUTER_API_KEY=file-key\nMODEL=file-model\n",
        encoding="utf-8",
    )

    settings = OrcaRouterSettings.load(
        env_file,
        environ={"ORCAROUTER_API_KEY": "process-key"},
    )

    assert settings.api_key == "process-key"
    assert settings.model == "file-model"
    assert settings.base_url == "https://api.orcarouter.ai/v1"
