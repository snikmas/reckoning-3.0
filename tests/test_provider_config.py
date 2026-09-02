from __future__ import annotations

from pathlib import Path

from reckoning.config import (
    DeepSeekSettings,
    OrcaRouterSettings,
    ProviderCredentials,
)


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


def test_deepseek_settings_load_provider_specific_values_from_env_file(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY='test-key'\n"
        'DEEPSEEK_MODEL="deepseek-chat"\n'
        'BASE_DEEPSEEK_URL="https://api.deepseek.com"\n'
        "UNRELATED_SECRET=must-not-load\n",
        encoding="utf-8",
    )

    settings = DeepSeekSettings.load(env_file, environ={})

    assert settings.api_key == "test-key"
    assert settings.model == "deepseek-chat"
    assert settings.base_url == "https://api.deepseek.com"
    assert "UNRELATED_SECRET" not in settings.__dict__


def test_deepseek_settings_remain_available_for_existing_evidence_gate(
    tmp_path: Path,
) -> None:
    settings = DeepSeekSettings.load(
        tmp_path / "missing.env",
        environ={"DEEPSEEK_API_KEY": "test-key"},
    )

    assert settings.api_key == "test-key"
    assert settings.model == "deepseek-v4-flash"
    assert settings.base_url == "https://api.deepseek.com"


def test_settings_fall_back_to_the_saved_provider_credential(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    ProviderCredentials("orcarouter", "saved-key").save(credentials_path)

    settings = OrcaRouterSettings.load(
        tmp_path / "missing.env",
        environ={},
        credential_file=credentials_path,
    )

    assert settings.api_key == "saved-key"
    assert settings.model == "orcarouter/auto"


def test_environment_and_env_file_win_over_the_saved_credential(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    ProviderCredentials("deepseek", "saved-key").save(credentials_path)

    from_process = DeepSeekSettings.load(
        tmp_path / "missing.env",
        environ={"DEEPSEEK_API_KEY": "process-key"},
        credential_file=credentials_path,
    )
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
    from_file = DeepSeekSettings.load(
        env_file,
        environ={},
        credential_file=credentials_path,
    )

    assert from_process.api_key == "process-key"
    assert from_file.api_key == "file-key"


def test_a_credential_for_another_provider_is_ignored(tmp_path: Path) -> None:
    credentials_path = tmp_path / "provider.json"
    ProviderCredentials("deepseek", "saved-key").save(credentials_path)

    settings = OrcaRouterSettings.load(
        tmp_path / "missing.env",
        environ={},
        credential_file=credentials_path,
    )

    assert settings.api_key is None


def test_settings_do_not_read_a_credential_file_unless_one_is_given(
    tmp_path: Path,
) -> None:
    settings = DeepSeekSettings.load(tmp_path / "missing.env", environ={})

    assert settings.api_key is None
