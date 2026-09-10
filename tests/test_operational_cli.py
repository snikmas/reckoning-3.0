from __future__ import annotations

import io
import json
import os
import stat
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Self
from unittest import mock
from urllib.error import HTTPError

import pytest

from reckoning import operations
from reckoning.command import main as command_main
from reckoning.config import ProviderCredentialStore
from reckoning.operations import (
    NODE_MARKER,
    PASSPHRASE_ENV,
    OperationError,
    PlacementProfile,
    load_installation_runtime,
    setup_instance,
)
from reckoning.personas import PersonaDefinition
from reckoning.trials import (
    JsonFileTrialRepository,
    REQUIRED_REAL_USE_METRICS,
    TrialEvidence,
    TrialRecorder,
)
from reckoning.web import validate_bind_host

PASSPHRASE = "correct-horse-battery-staple"


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str


def run_cli(*arguments: str, passphrase: str | None = None) -> CliResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    environment = {PASSPHRASE_ENV: passphrase} if passphrase is not None else {}
    with (
        mock.patch.dict(os.environ, environment),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        try:
            returncode = command_main(list(arguments))
        except SystemExit as error:
            returncode = int(error.code or 0)
    return CliResult(returncode, stdout.getvalue(), stderr.getvalue())


def isolated_setup_args(tmp_path: Path, name: str = "isolated") -> tuple[str, ...]:
    root = tmp_path / name
    return (
        "--credentials",
        str(root / "provider.json"),
        "--telegram-config",
        str(root / "telegram.json"),
        "--draft-path",
        str(root / "setup-draft.json"),
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_state(data_dir: Path) -> dict[str, object]:
    return {
        path.relative_to(data_dir).as_posix(): json.loads(
            path.read_text(encoding="utf-8")
        )
        for path in sorted(data_dir.rglob("*.json"))
    }


def build_transfer_state(data_dir: Path) -> None:
    write_json(
        data_dir / "continuity.json",
        {
            "schema_version": 1,
            "reckonings": [
                {
                    "id": "decision-1",
                    "version": 3,
                    "status": "confirmed",
                    "provenance": ["message-1", "correction-1"],
                }
            ],
            "check_ins": [],
        },
    )
    write_json(
        data_dir / "personal-context.json",
        {
            "schema_version": 1,
            "versions": [
                {
                    "record_id": "fact-active",
                    "version": 2,
                    "source": "direct user correction",
                    "retrieval_permitted": False,
                }
            ],
            "suppressions": [
                {
                    "deleted_record_id": "fact-deleted",
                    "deleted_at": "2026-08-31T00:00:00+00:00",
                }
            ],
        },
    )
    write_json(
        data_dir / "routines.json",
        {
            "schema_version": 1,
            "routines": [{"id": "routine-1", "permission_id": "permission-1"}],
        },
    )
    write_json(
        data_dir / "receipts.json",
        {
            "schema_version": 1,
            "receipts": [{"id": "receipt-1", "routine_id": "routine-1"}],
        },
    )
    write_json(
        data_dir / "permissions.json",
        {
            "schema_version": 1,
            "permissions": [
                {
                    "id": "permission-1",
                    "action": "calendar.write",
                    "status": "revoked",
                }
            ],
        },
    )


def test_encrypted_backup_restores_the_exact_visible_contract(tmp_path: Path) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "state.reckoning"
    build_transfer_state(source)
    expected = read_state(source)

    backed_up = run_cli(
        "backup",
        "--data-dir",
        str(source),
        "--output",
        str(archive),
        passphrase=PASSPHRASE,
    )

    assert backed_up.returncode == 0, backed_up.stderr
    assert "encrypted backup created" in backed_up.stdout
    raw_archive = archive.read_bytes()
    assert b"fact-active" not in raw_archive
    assert b"permission-1" not in raw_archive

    restored_result = run_cli(
        "restore",
        "--input",
        str(archive),
        "--data-dir",
        str(restored),
        passphrase=PASSPHRASE,
    )

    assert restored_result.returncode == 0, restored_result.stderr
    assert "restore complete" in restored_result.stdout
    assert read_state(restored) == expected


def test_failed_restore_reports_failure_without_changing_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    archive = tmp_path / "state.reckoning"
    build_transfer_state(source)
    result = run_cli(
        "backup",
        "--data-dir",
        str(source),
        "--output",
        str(archive),
        passphrase=PASSPHRASE,
    )
    assert result.returncode == 0, result.stderr
    encoded = json.loads(archive.read_text(encoding="utf-8"))
    encoded["ciphertext"] = "AAAA" + encoded["ciphertext"][4:]
    write_json(archive, encoded)

    restored = run_cli(
        "restore",
        "--input",
        str(archive),
        "--data-dir",
        str(target),
        passphrase=PASSPHRASE,
    )

    assert restored.returncode == 2
    assert "restore failed" in restored.stderr
    assert not target.exists()


def test_restore_reports_an_operating_system_failure_without_a_traceback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "state.reckoning"
    blocked_parent = tmp_path / "not-a-directory"
    build_transfer_state(source)
    result = run_cli(
        "backup",
        "--data-dir",
        str(source),
        "--output",
        str(archive),
        passphrase=PASSPHRASE,
    )
    assert result.returncode == 0, result.stderr
    blocked_parent.write_text("occupied", encoding="utf-8")

    restored = run_cli(
        "restore",
        "--input",
        str(archive),
        "--data-dir",
        str(blocked_parent / "target"),
        passphrase=PASSPHRASE,
    )

    assert restored.returncode == 2
    assert restored.stderr.startswith("restore failed:")
    assert "Traceback" not in restored.stderr
    assert blocked_parent.read_text(encoding="utf-8") == "occupied"


def test_setup_creates_a_single_user_instance_and_proves_the_continuity_loop(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "new-instance"
    server_dir = tmp_path / "new-instance-server"

    result = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(data_dir),
        "--placement",
        "hybrid",
        "--server-data-dir",
        str(server_dir),
        "--mode",
        "custom",
        *isolated_setup_args(tmp_path, "continuity-cli"),
    )

    assert result.returncode == 0, result.stderr
    assert "Demo mode" in result.stdout
    assert "Setup complete" in result.stdout
    assert "reopened" in result.stdout
    configuration = json.loads(
        (data_dir / "instance.json").read_text(encoding="utf-8")
    )
    assert configuration["instance_type"] == "single-user"
    assert configuration["hosted_tenancy"] is False
    assert configuration["public_admin"] is False
    assert configuration["placement_profile"] == "hybrid"
    assert configuration["installation_check"]["core_continuity_loop"] == "passed"
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert configuration["storage_roots"] == {
        "local": "local-data-dir",
        "server": str(server_dir),
    }
    assert not tuple(tmp_path.glob(".new-instance.setup-*"))
    assert not tuple(tmp_path.glob(".new-instance-server.setup-*"))


def test_setup_commit_failure_preserves_existing_empty_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_root = tmp_path / "atomic-local"
    server_root = tmp_path / "atomic-server"
    local_root.mkdir()
    server_root.mkdir()
    real_replace = operations.os.replace

    def fail_server_commit(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        if (
            Path(destination) == server_root
            and source_path.parent == tmp_path
            and source_path.name.startswith(".atomic-server.setup-")
        ):
            raise OSError("injected server commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(operations.os, "replace", fail_server_commit)

    with pytest.raises(OperationError, match="no installation was created"):
        setup_instance(
            local_root,
            "hybrid",
            server_data_dir=server_root,
        )

    assert local_root.is_dir()
    assert server_root.is_dir()
    assert tuple(local_root.iterdir()) == ()
    assert tuple(server_root.iterdir()) == ()
    assert not tuple(tmp_path.glob(".atomic-local.setup-*"))
    assert not tuple(tmp_path.glob(".atomic-server.setup-*"))
    assert not tuple(tmp_path.glob(".atomic-local.original-*"))
    assert not tuple(tmp_path.glob(".atomic-server.original-*"))


def test_personal_server_setup_records_its_real_placement_and_release_block(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "personal-server-instance"
    server_dir = tmp_path / "personal-server-state"

    result = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(data_dir),
        "--placement",
        "personal-server",
        "--server-data-dir",
        str(server_dir),
        "--mode",
        "custom",
        *isolated_setup_args(tmp_path, "personal-server-cli"),
    )

    assert result.returncode == 0, result.stderr
    assert "Setup complete" in result.stdout
    evidence = json.loads(
        (data_dir / "release-evidence.json").read_text(encoding="utf-8")
    )
    assert evidence == {
        "schema_version": 1,
        "repeated_value": False,
        "clean_install": False,
        "recovery": False,
        "privacy": False,
        "deletion": False,
        "external_action": False,
        "core_continuity_without_developer": False,
    }


def test_cli_requires_a_separate_explicit_server_root_and_reports_probe_health(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "health-local"
    server_root = tmp_path / "health-server"

    missing_root = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(tmp_path / "missing-root"),
        "--placement",
        "hybrid",
        *isolated_setup_args(tmp_path, "missing-root-cli"),
    )
    shared_root = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(tmp_path / "shared-root"),
        "--placement",
        "hybrid",
        "--server-data-dir",
        str(tmp_path / "shared-root"),
        *isolated_setup_args(tmp_path, "shared-root-cli"),
    )
    setup = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(local_root),
        "--placement",
        "hybrid",
        "--server-data-dir",
        str(server_root),
        "--mode",
        "custom",
        *isolated_setup_args(tmp_path, "health-cli"),
    )

    assert missing_root.returncode == 2
    assert "server-data-dir is required" in missing_root.stderr
    assert shared_root.returncode == 2
    assert "roots must be separate" in shared_root.stderr
    assert setup.returncode == 0, setup.stderr

    healthy = run_cli(
        "doctor",
        "--data-dir",
        str(local_root),
        "--server-data-dir",
        str(server_root),
    )
    marker = server_root / NODE_MARKER
    offline_marker = server_root / ".reckoning-node.offline"
    marker.replace(offline_marker)
    try:
        degraded = run_cli(
            "doctor",
            "--data-dir",
            str(local_root),
            "--server-data-dir",
            str(server_root),
        )
    finally:
        offline_marker.replace(marker)

    assert healthy.returncode == 0, healthy.stderr
    assert "healthy" in healthy.stdout
    assert "server node" in healthy.stdout
    assert "available" in healthy.stdout
    assert degraded.returncode == 0, degraded.stderr
    assert "degraded" in degraded.stdout
    assert "offline" in degraded.stdout
    assert "approved-remote-sources" in degraded.stdout


def test_builtin_web_server_rejects_non_loopback_bind_addresses() -> None:
    for allowed in ("localhost", "127.0.0.1", "127.12.3.4", "::1"):
        assert validate_bind_host(allowed) == allowed

    for exposed in ("0.0.0.0", "192.168.1.10", "example.com", "::"):
        try:
            validate_bind_host(exposed)
        except ValueError as error:
            assert "loopback" in str(error)
        else:
            raise AssertionError(f"non-loopback host was accepted: {exposed}")


def test_non_interactive_setup_rejects_an_original_persona_without_a_prompt(
    tmp_path: Path,
) -> None:
    result = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(tmp_path / "original-persona-instance"),
        "--persona",
        "original",
        *isolated_setup_args(tmp_path, "original-persona-cli"),
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert not (tmp_path / "original-persona-instance").exists()


def test_runtime_uses_installed_persona_and_hybrid_placement(tmp_path: Path) -> None:
    data_dir = tmp_path / "installed-runtime"
    server_dir = tmp_path / "installed-runtime-server"
    persona = PersonaDefinition(
        id="clear-eyed",
        name="Clear Eyed",
        voice="candid",
        directness="direct",
        warmth="balanced",
        humor="dry",
        challenge="uncomfortable",
        sensitive_topic_handling="practical",
    )
    setup_instance(
        data_dir,
        "hybrid",
        persona,
        server_data_dir=server_dir,
    )

    runtime = load_installation_runtime(
        data_dir,
        server_data_dir=server_dir,
        local_node_available=True,
        server_node_available=False,
    )

    assert runtime.persona.name == "Clear Eyed"
    assert "direct recommendations" in runtime.persona.instructions
    assert runtime.application_placement.storage_location == "local"
    outcome = runtime.interface_placement.current_outcome()
    assert outcome.status == "limited"
    assert outcome.unavailable_categories == ("approved-remote-sources",)
    instance = json.loads((data_dir / "instance.json").read_text(encoding="utf-8"))
    assert instance["active_persona_id"] == "clear-eyed"


def test_each_installation_profile_reports_the_correct_node_outage(
    tmp_path: Path,
) -> None:
    cases: tuple[
        tuple[PlacementProfile, bool, bool, tuple[str, ...]], ...
    ] = (
        (
            "local",
            False,
            True,
            ("personal-context", "confirmed-state", "approved-remote-sources"),
        ),
        (
            "personal-server",
            True,
            False,
            ("personal-context", "confirmed-state", "approved-remote-sources"),
        ),
        (
            "hybrid",
            False,
            True,
            ("personal-context", "confirmed-state"),
        ),
    )

    for profile, local_available, server_available, unavailable in cases:
        data_dir = tmp_path / profile
        server_dir = (
            tmp_path / f"{profile}-server" if profile != "local" else None
        )
        setup_instance(data_dir, profile, server_data_dir=server_dir)
        runtime = load_installation_runtime(
            data_dir,
            server_data_dir=server_dir,
            local_node_available=local_available,
            server_node_available=server_available,
        )

        outcome = runtime.interface_placement.current_outcome()
        assert runtime.interface_placement.profile == profile
        assert outcome.status == "limited"
        assert outcome.unavailable_categories == unavailable

    personal_server = load_installation_runtime(
        tmp_path / "personal-server",
        server_data_dir=tmp_path / "personal-server-server",
        local_node_available=True,
        server_node_available=True,
    )
    private_categories = tuple(
        category
        for category in personal_server.interface_placement.categories
        if category.sensitivity == "private"
    )
    assert private_categories
    assert all(category.remote_approved for category in private_categories)


def test_release_readiness_remains_blocked_until_every_required_gate_exists(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "release-evidence.json"
    trial_path = tmp_path / "trials.json"
    instant = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
    recorder = TrialRecorder(JsonFileTrialRepository(trial_path))
    trial = recorder.start(
        "release-trial", "first-slice", instant, assessed_revision="r1"
    )
    trial = recorder.record(
        trial.id,
        TrialEvidence(
            "required-check",
            "automated_check",
            None,
            "passed",
            instant,
            check_outcome="passed",
        ),
        expected_revision=trial.version,
    )
    for metric in REQUIRED_REAL_USE_METRICS:
        trial = recorder.record(
            trial.id,
            TrialEvidence(
                f"observation-{metric}",
                "real_use",
                metric,
                "positive",
                instant,
                real_use_outcome="positive",
            ),
            expected_revision=trial.version,
        )
    recorder.decide(
        trial.id,
        "accepted",
        reason="The first user accepted the assessed revision.",
        decided_at=instant,
        assessed_revision="r1",
        expected_revision=trial.version,
    )
    write_json(
        evidence,
        {
            "schema_version": 1,
            "repeated_value": True,
            "clean_install": True,
            "recovery": False,
            "privacy": True,
            "deletion": True,
            "external_action": True,
            "core_continuity_without_developer": True,
            "trial_acceptance": {
                "path": trial_path.name,
                "trial_id": trial.id,
            },
        },
    )

    blocked = run_cli("doctor", "--release-evidence", str(evidence))

    assert blocked.returncode == 2
    assert "BLOCKED" in blocked.stdout
    assert "recovery" in blocked.stdout

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["recovery"] = True
    write_json(evidence, payload)
    ready = run_cli("doctor", "--release-evidence", str(evidence))

    assert ready.returncode == 0
    assert "READY" in ready.stdout


def test_export_can_be_migrated_and_diagnosed_after_restore(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source_server = tmp_path / "source-server"
    restored = tmp_path / "restored"
    restored_server = tmp_path / "restored-server"
    exported = tmp_path / "state-export.reckoning"
    migrated = tmp_path / "state-migrated.reckoning"
    setup = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(source),
        "--placement",
        "personal-server",
        "--server-data-dir",
        str(source_server),
        *isolated_setup_args(tmp_path, "export-cli"),
    )
    assert setup.returncode == 0, setup.stderr
    write_json(source / "local-state.json", {"location": "local"})
    write_json(
        source_server / "personal-context" / "private.json",
        {"location": "private"},
    )
    write_json(
        source_server / "confirmed-state" / "confirmations.json",
        {"location": "confirmed"},
    )
    write_json(
        source_server / "approved-remote-sources" / "connectors.json",
        {"location": "connector"},
    )
    write_json(
        source_server / "server-owned.json",
        {"location": "server"},
    )

    export = run_cli(
        "export",
        "--data-dir",
        str(source),
        "--server-data-dir",
        str(source_server),
        "--output",
        str(exported),
        passphrase=PASSPHRASE,
    )
    assert export.returncode == 0, export.stderr
    migration = run_cli(
        "migrate",
        "--input",
        str(exported),
        "--output",
        str(migrated),
        passphrase=PASSPHRASE,
    )
    assert migration.returncode == 0, migration.stderr
    assert "transfer format 1 -> 1" in migration.stdout

    restore = run_cli(
        "restore",
        "--input",
        str(migrated),
        "--data-dir",
        str(restored),
        "--server-data-dir",
        str(restored_server),
        passphrase=PASSPHRASE,
    )
    assert restore.returncode == 0, restore.stderr
    diagnosis = run_cli(
        "doctor",
        "--data-dir",
        str(restored),
        "--server-data-dir",
        str(restored_server),
    )

    assert diagnosis.returncode == 0, diagnosis.stderr
    assert "healthy" in diagnosis.stdout
    assert "single-user" in diagnosis.stdout
    assert "personal-server" in diagnosis.stdout
    assert json.loads(
        (restored / "local-state.json").read_text(encoding="utf-8")
    ) == {"location": "local"}
    assert json.loads(
        (restored_server / "personal-context" / "private.json").read_text(
            encoding="utf-8"
        )
    ) == {"location": "private"}
    assert json.loads(
        (restored_server / "confirmed-state" / "confirmations.json").read_text(
            encoding="utf-8"
        )
    ) == {"location": "confirmed"}
    assert json.loads(
        (
            restored_server
            / "approved-remote-sources"
            / "connectors.json"
        ).read_text(encoding="utf-8")
    ) == {"location": "connector"}
    assert json.loads(
        (restored_server / "server-owned.json").read_text(encoding="utf-8")
    ) == {"location": "server"}
    assert not (restored / "personal-context" / "private.json").exists()
    assert not (restored / "confirmed-state" / "confirmations.json").exists()
    assert not (
        restored / "approved-remote-sources" / "connectors.json"
    ).exists()
    restored_instance = json.loads(
        (restored / "instance.json").read_text(encoding="utf-8")
    )
    assert restored_instance["storage_roots"]["server"] == str(
        restored_server.resolve()
    )
    old_server_path = str(source_server.resolve())
    restored_json = tuple(restored.rglob("*.json")) + tuple(
        restored_server.rglob("*.json")
    )
    assert all(
        old_server_path not in path.read_text(encoding="utf-8")
        for path in restored_json
    )


def test_top_level_help_groups_every_command_and_drops_the_old_binaries() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    for group in ("Run", "Setup", "Data"):
        assert group in result.stdout
    for command in (
        "reckoning gateway",
        "reckoning setup",
        "reckoning doctor",
        "reckoning backup",
        "reckoning restore",
        "reckoning export",
        "reckoning migrate",
    ):
        assert command in result.stdout
    assert "reckoning-ops" not in result.stdout
    assert "reckoning-telegram" not in result.stdout


def test_unknown_commands_fail_with_a_pointer_to_help() -> None:
    result = run_cli("diagnose")

    assert result.returncode == 2
    assert "unknown command 'diagnose'" in result.stderr
    assert "reckoning --help" in result.stderr


def _seed_pingable_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ping-instance"
    setup = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(data_dir),
        *isolated_setup_args(tmp_path, "ping-cli"),
    )
    assert setup.returncode == 0, setup.stderr
    store = ProviderCredentialStore()
    store.set_key("deepseek", "sk-live", make_default=True)
    credentials_path = tmp_path / "provider.json"
    store.save(credentials_path)
    monkeypatch.setattr(
        "reckoning.command.DEFAULT_PROVIDER_CREDENTIALS", credentials_path
    )
    return data_dir


def test_doctor_makes_no_network_calls_without_ping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _seed_pingable_install(tmp_path, monkeypatch)

    def forbidden_network(request: object, timeout: float) -> bytes:
        raise AssertionError("doctor contacted the network without --ping")

    monkeypatch.setattr("reckoning.providers.urlopen", forbidden_network)

    result = run_cli("doctor", "--data-dir", str(data_dir))

    assert result.returncode == 0, result.stderr
    assert "healthy" in result.stdout
    assert "provider ping" not in result.stdout


def test_doctor_ping_verifies_the_saved_key_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _seed_pingable_install(tmp_path, monkeypatch)
    requests: list[object] = []

    class FakeResponse:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"data": []}'

    def fake_network(request: object, timeout: float) -> FakeResponse:
        requests.append(request)
        return FakeResponse()

    monkeypatch.setattr("reckoning.providers.urlopen", fake_network)

    result = run_cli("doctor", "--data-dir", str(data_dir), "--ping")

    assert result.returncode == 0, result.stderr
    assert len(requests) == 1
    assert "provider ping: deepseek verified the saved API key" in result.stdout


def test_doctor_ping_reports_a_rejected_key_with_a_fix_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _seed_pingable_install(tmp_path, monkeypatch)

    def rejecting_network(request: object, timeout: float) -> bytes:
        raise HTTPError("https://api.deepseek.com/models", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("reckoning.providers.urlopen", rejecting_network)

    result = run_cli("doctor", "--data-dir", str(data_dir), "--ping")

    assert result.returncode == 2
    assert "rejected the API key" in result.stdout
    assert "fix: run reckoning setup" in result.stdout


def test_doctor_ping_skips_the_fake_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "fake-instance"
    setup = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(data_dir),
        *isolated_setup_args(tmp_path, "fake-ping-cli"),
    )
    assert setup.returncode == 0, setup.stderr
    monkeypatch.setattr(
        "reckoning.command.DEFAULT_PROVIDER_CREDENTIALS",
        tmp_path / "missing-provider.json",
    )

    def forbidden_network(request: object, timeout: float) -> bytes:
        raise AssertionError("pinging the fake provider must not use the network")

    monkeypatch.setattr("reckoning.providers.urlopen", forbidden_network)

    result = run_cli("doctor", "--data-dir", str(data_dir), "--ping")

    assert result.returncode == 0, result.stderr
    assert "provider ping: skipped" in result.stdout


def test_doctor_renders_a_structured_table_with_fix_pointers(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty-instance"
    empty.mkdir()

    result = run_cli("doctor", "--data-dir", str(empty))

    assert result.returncode == 0, result.stderr
    assert "╭" in result.stdout
    assert "Check" in result.stdout
    assert "Result" in result.stdout
    assert "degraded" in result.stdout
    assert (
        "instance.json is missing; run setup or restore a backup" in result.stdout
    )


def test_setup_never_accepts_secrets_as_command_line_flags(tmp_path: Path) -> None:
    result = run_cli(
        "setup",
        "--non-interactive",
        "--data-dir",
        str(tmp_path / "flag-secret-instance"),
        "--provider",
        "deepseek",
        "--api-key",
        "sk-flag-secret",
        *isolated_setup_args(tmp_path, "flag-secret-cli"),
    )

    assert result.returncode == 2
    assert "--api-key is not supported" in result.stderr
    assert "sk-flag-secret" not in result.stderr
    assert not (tmp_path / "flag-secret-instance").exists()
