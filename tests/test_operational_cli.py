from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from reckoning.operations import (
    PlacementProfile,
    load_installation_runtime,
    setup_instance,
)
from reckoning.personas import PersonaDefinition


PASSPHRASE = "correct-horse-battery-staple"


def run_cli(
    *arguments: str, passphrase: str | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    if passphrase is not None:
        environment["RECKONING_TRANSFER_PASSPHRASE"] = passphrase
    return subprocess.run(
        [sys.executable, "-m", "reckoning.cli", *arguments],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
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


def test_setup_explains_single_user_operation_and_checks_the_continuity_loop(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "new-instance"

    result = run_cli(
        "setup",
        "--data-dir",
        str(data_dir),
        "--placement",
        "hybrid",
    )

    assert result.returncode == 0, result.stderr
    assert "single-user" in result.stdout.lower()
    assert "Private data stays local" in result.stdout
    assert "exact or standing permission" in result.stdout
    assert "limited mode" in result.stdout
    assert (
        "loopback, a private authenticated network, or an SSH tunnel"
        in result.stdout
    )
    assert "backup" in result.stdout
    assert "restore" in result.stdout
    configuration = json.loads(
        (data_dir / "instance.json").read_text(encoding="utf-8")
    )
    assert configuration["instance_type"] == "single-user"
    assert configuration["hosted_tenancy"] is False
    assert configuration["public_admin"] is False
    assert configuration["placement_profile"] == "hybrid"
    assert configuration["installation_check"]["core_continuity_loop"] == "passed"
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700


def test_setup_can_author_and_select_an_original_persona(tmp_path: Path) -> None:
    data_dir = tmp_path / "original-persona-instance"

    result = run_cli(
        "setup",
        "--data-dir",
        str(data_dir),
        "--persona",
        "original",
        "--persona-id",
        "clear-eyed",
        "--persona-name",
        "Clear Eyed",
        "--voice",
        "candid",
        "--directness",
        "direct",
        "--warmth",
        "balanced",
        "--humor",
        "dry",
        "--challenge",
        "uncomfortable",
        "--sensitive-topic-handling",
        "practical",
    )

    assert result.returncode == 0, result.stderr
    persona_state = json.loads(
        (data_dir / "personas.json").read_text(encoding="utf-8")
    )
    assert persona_state["active_persona_id"] == "clear-eyed"
    assert persona_state["authored"] == [
        {
            "id": "clear-eyed",
            "name": "Clear Eyed",
            "voice": "candid",
            "directness": "direct",
            "warmth": "balanced",
            "humor": "dry",
            "challenge": "uncomfortable",
            "sensitive_topic_handling": "practical",
        }
    ]


def test_runtime_uses_installed_persona_and_hybrid_placement(tmp_path: Path) -> None:
    data_dir = tmp_path / "installed-runtime"
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
    setup_instance(data_dir, "hybrid", persona)

    runtime = load_installation_runtime(
        data_dir,
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
        setup_instance(data_dir, profile)
        runtime = load_installation_runtime(
            data_dir,
            local_node_available=local_available,
            server_node_available=server_available,
        )

        outcome = runtime.interface_placement.current_outcome()
        assert runtime.interface_placement.profile == profile
        assert outcome.status == "limited"
        assert outcome.unavailable_categories == unavailable

    personal_server = load_installation_runtime(
        tmp_path / "personal-server",
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
        },
    )

    blocked = run_cli("diagnose", "--release-evidence", str(evidence))

    assert blocked.returncode == 2
    assert "BLOCKED" in blocked.stdout
    assert "recovery" in blocked.stdout

    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["recovery"] = True
    write_json(evidence, payload)
    ready = run_cli("diagnose", "--release-evidence", str(evidence))

    assert ready.returncode == 0
    assert "READY" in ready.stdout


def test_export_can_be_migrated_and_diagnosed_after_restore(tmp_path: Path) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    exported = tmp_path / "state-export.reckoning"
    migrated = tmp_path / "state-migrated.reckoning"
    setup = run_cli(
        "setup", "--data-dir", str(source), "--placement", "personal-server"
    )
    assert setup.returncode == 0, setup.stderr

    export = run_cli(
        "export",
        "--data-dir",
        str(source),
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
        passphrase=PASSPHRASE,
    )
    assert restore.returncode == 0, restore.stderr
    diagnosis = run_cli("diagnose", "--data-dir", str(restored))

    assert diagnosis.returncode == 0, diagnosis.stderr
    assert "status: healthy" in diagnosis.stdout
    assert "instance: single-user" in diagnosis.stdout
    assert "placement: personal-server" in diagnosis.stdout


def test_cli_contains_only_operational_commands() -> None:
    result = run_cli("--help")

    assert result.returncode == 0
    assert (
        "{backup,diagnose,export,migrate,restore,setup}"
        in result.stdout
    )
    assert "release-readiness" not in result.stdout
