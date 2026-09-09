from __future__ import annotations

import json
import stat
from pathlib import Path

from reckoning.config import (
    DeepSeekSettings,
    OrcaRouterSettings,
    ProviderCredentialStore,
    RuntimeProviderSettings,
    default_provider_name,
)
from reckoning.operations import setup_instance


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


def write_legacy_credentials(path: Path, provider_name: str, api_key: str) -> None:
    path.write_text(
        json.dumps({"provider_name": provider_name, "api_key": api_key}),
        encoding="utf-8",
    )


def test_settings_fall_back_to_the_saved_provider_credential(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    store = ProviderCredentialStore()
    store.set_key("orcarouter", "saved-key")
    store.save(credentials_path)

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
    store = ProviderCredentialStore()
    store.set_key("deepseek", "saved-key")
    store.save(credentials_path)

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
    store = ProviderCredentialStore()
    store.set_key("deepseek", "saved-key")
    store.save(credentials_path)

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


def test_credential_store_round_trips_providers_and_the_default(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-deepseek")
    store.set_key("orcarouter", "sk-orca")
    store.default_provider = "orcarouter"
    store.save(credentials_path)

    assert stat.S_IMODE(credentials_path.stat().st_mode) == 0o600
    saved = json.loads(credentials_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["default_provider"] == "orcarouter"
    assert saved["providers"]["deepseek"]["secret"] == "sk-deepseek"
    assert saved["providers"]["deepseek"]["verified"] is True
    assert saved["providers"]["orcarouter"]["secret"] == "sk-orca"

    loaded = ProviderCredentialStore.load(credentials_path)
    assert set(loaded.providers) == {"deepseek", "orcarouter"}
    assert loaded.default_provider == "orcarouter"
    assert loaded.api_key_for("deepseek") == "sk-deepseek"
    assert loaded.api_key_for("orcarouter") == "sk-orca"
    assert loaded.api_key_for("fake") is None

    orcarouter = OrcaRouterSettings.load(
        tmp_path / "missing.env", environ={}, credential_file=credentials_path
    )
    deepseek = DeepSeekSettings.load(
        tmp_path / "missing.env", environ={}, credential_file=credentials_path
    )
    assert orcarouter.api_key == "sk-orca"
    assert deepseek.api_key == "sk-deepseek"


def test_credential_store_loads_and_migrates_the_legacy_single_slot_file(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    write_legacy_credentials(credentials_path, "deepseek", "legacy-key")

    loaded = ProviderCredentialStore.load(credentials_path)
    assert loaded.api_key_for("deepseek") == "legacy-key"
    assert loaded.default_provider == "deepseek"

    loaded.set_key("orcarouter", "new-key")
    loaded.save(credentials_path)

    saved = json.loads(credentials_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["default_provider"] == "deepseek"
    assert saved["providers"]["deepseek"]["secret"] == "legacy-key"
    assert saved["providers"]["orcarouter"]["secret"] == "new-key"
    migrated = DeepSeekSettings.load(
        tmp_path / "missing.env", environ={}, credential_file=credentials_path
    )
    assert migrated.api_key == "legacy-key"


def test_credential_store_removes_a_provider_and_recovers_the_default(
    tmp_path: Path,
) -> None:
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-deepseek")
    store.set_key("orcarouter", "sk-orca")
    store.default_provider = "deepseek"

    assert store.remove("deepseek") is True
    assert store.remove("deepseek") is False
    assert set(store.providers) == {"orcarouter"}
    assert store.api_key_for("orcarouter") == "sk-orca"
    assert store.default_provider == "orcarouter"


def test_credential_store_ignores_unknown_providers_and_missing_keys(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    credentials_path.write_text(
        json.dumps(
            {
                "default_provider": "unknown",
                "providers": {
                    "unknown": "key",
                    "deepseek": "",
                    "orcarouter": " sk-orca ",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = ProviderCredentialStore.load(credentials_path)

    assert set(loaded.providers) == {"orcarouter"}
    assert loaded.api_key_for("orcarouter") == "sk-orca"
    assert loaded.default_provider == "orcarouter"


def test_an_empty_credential_store_saves_with_a_null_default(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"

    ProviderCredentialStore().save(credentials_path)

    assert json.loads(credentials_path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "default_provider": None,
        "providers": {},
    }


def test_an_explicit_null_default_does_not_reactivate_a_saved_credential(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-saved")
    store.default_provider = None
    store.save(credentials_path)

    loaded = ProviderCredentialStore.load(credentials_path)

    assert loaded.default_provider is None
    assert loaded.api_key_for("deepseek") == "sk-saved"


def test_provider_override_does_not_inherit_custom_endpoint_advanced_settings(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    credentials_path = tmp_path / "provider.json"
    setup_instance(data_dir, "local")
    instance_path = data_dir / "instance.json"
    instance = json.loads(instance_path.read_text(encoding="utf-8"))
    instance["activation"] = {
        "status": "activated",
        "provider": "custom",
        "model": "custom-model",
        "base_url": "https://llm.example.test/v1",
        "protocol": "openai-chat-completions",
        "context_window": 32_000,
        "header_env": {"X-Tenant": "CUSTOM_TENANT"},
    }
    instance_path.write_text(json.dumps(instance), encoding="utf-8")
    store = ProviderCredentialStore()
    store.set_key(
        "custom",
        "custom-key",
        model="custom-model",
        base_url="https://llm.example.test/v1",
    )
    store.set_key("deepseek", "deepseek-key", model="deepseek-chat")
    store.save(credentials_path)

    settings = RuntimeProviderSettings.load(
        data_dir,
        credentials_path=credentials_path,
        environ={"CUSTOM_TENANT": "tenant-secret"},
        provider_name="deepseek",
    )

    assert settings.provider_name == "deepseek"
    assert settings.protocol == "openai-chat-completions"
    assert settings.context_window is None
    assert settings.headers == ()


def test_default_provider_name_returns_the_saved_default(tmp_path: Path) -> None:
    credentials_path = tmp_path / "provider.json"
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-deepseek")
    store.set_key("orcarouter", "sk-orca")
    store.default_provider = "orcarouter"
    store.save(credentials_path)

    assert default_provider_name(credentials_path) == "orcarouter"


def test_default_provider_name_reads_the_legacy_single_slot_file(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    write_legacy_credentials(credentials_path, "deepseek", "legacy-key")

    assert default_provider_name(credentials_path) == "deepseek"


def test_default_provider_name_is_none_without_a_credential_file(
    tmp_path: Path,
) -> None:
    assert default_provider_name(tmp_path / "provider.json") is None


def test_an_environment_reference_is_stored_without_copying_its_value(
    tmp_path: Path,
) -> None:
    credentials_path = tmp_path / "provider.json"
    store = ProviderCredentialStore()
    store.set_env_reference("deepseek", "DEEPSEEK_API_KEY")
    store.save(credentials_path)

    raw = credentials_path.read_text(encoding="utf-8")
    assert "sk-real-secret" not in raw
    assert "env:DEEPSEEK_API_KEY" in raw

    loaded = ProviderCredentialStore.load(credentials_path)
    assert loaded.api_key_for("deepseek") is None
    assert (
        loaded.api_key_for("deepseek", environ={"DEEPSEEK_API_KEY": "sk-real-secret"})
        == "sk-real-secret"
    )


def test_an_unverified_credential_is_inactive_until_verified(tmp_path: Path) -> None:
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-pending", verified=False)

    assert store.api_key_for("deepseek") == "sk-pending"
    assert store.is_active("deepseek") is False

    store.mark_verified("deepseek")
    assert store.is_active("deepseek") is True

    store.save(tmp_path / "provider.json")
    loaded = ProviderCredentialStore.load(tmp_path / "provider.json")
    assert loaded.is_active("deepseek") is True
    assert loaded.credential_for("deepseek") is not None
    assert loaded.credential_for("deepseek").verified_at  # type: ignore[union-attr]


def test_an_unverified_saved_credential_survives_a_round_trip(tmp_path: Path) -> None:
    credentials_path = tmp_path / "provider.json"
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-pending", model="deepseek-chat", verified=False)
    store.save(credentials_path)

    loaded = ProviderCredentialStore.load(credentials_path)
    entry = loaded.credential_for("deepseek")
    assert entry is not None
    assert entry.verified is False
    assert entry.verified_at is None
    assert entry.model == "deepseek-chat"
    assert loaded.is_active("deepseek") is False
