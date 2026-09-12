from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

import pytest

from reckoning.connectors import ApprovalRequest, ExternalWriteService, WriteResult
from reckoning.external_write_store import (
    ExternalWriteClaimConflict,
    ExternalWriteConflict,
    payload_digest,
)
from reckoning.operations import create_transfer, restore_transfer, setup_instance
from reckoning.root_database import ROOT_DATABASE_FILENAME


NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
PASSPHRASE = "correct-horse-battery-staple"


class RecordingAdapter:
    def __init__(self, result: WriteResult | None = None) -> None:
        self.calls = 0
        self._result = result or WriteResult("success", "event-1", "Synthetic success.")

    def execute(self, payload: dict[str, str], idempotency_key: str) -> WriteResult:
        del payload, idempotency_key
        self.calls += 1
        return self._result


class ExplodingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, payload: dict[str, str], idempotency_key: str) -> WriteResult:
        del payload, idempotency_key
        self.calls += 1
        raise TimeoutError("synthetic ambiguous adapter timeout")


def _prepare(
    service: ExternalWriteService,
    write_id: str,
    *,
    title: str | None = None,
    target: str = "calendar:synthetic",
) -> None:
    service.prepare(
        write_id=write_id,
        connector_id="synthetic-calendar",
        action_type="create-event",
        target=target,
        trigger="manual",
        boundary="one event",
        payload={"title": title or write_id},
        prepared_at=NOW,
    )


def _grant(
    service: ExternalWriteService,
    permission_id: str = "permission-a",
    *,
    target: str = "calendar:synthetic",
) -> None:
    service.grant_standing_permission(
        permission_id=permission_id,
        connector_id="synthetic-calendar",
        action_type="create-event",
        target=target,
        trigger="manual",
        boundary="one event",
        granted_at=NOW,
    )


def test_two_stale_services_prepare_different_writes_and_both_survive(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    first = ExternalWriteService(path)
    second = ExternalWriteService(path)

    _prepare(first, "write-a")
    _prepare(second, "write-b")

    surviving = {item.id for item in ExternalWriteService(path).list_prepared()}
    assert surviving == {"write-a", "write-b"}


def test_two_stale_services_approve_different_writes_and_both_survive(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    seeded = ExternalWriteService(path)
    _prepare(seeded, "write-a")
    _prepare(seeded, "write-b")
    first = ExternalWriteService(path)
    second = ExternalWriteService(path)

    first.approve_exact("write-a", approval_id="approval-a", approved_at=NOW)
    second.approve_exact("write-b", approval_id="approval-b", approved_at=NOW)

    reopened = ExternalWriteService(path)
    assert reopened.pending_approval_ids() == ()
    assert reopened.list_prepared()[0].exact_approval_id == "approval-a"
    assert reopened.list_prepared()[1].exact_approval_id == "approval-b"


def test_prepare_rejects_reusing_an_identifier_with_other_content(
    tmp_path: Path,
) -> None:
    service = ExternalWriteService(tmp_path / "external-writes.json")
    _prepare(service, "write-a", title="first")

    with pytest.raises(ExternalWriteConflict):
        _prepare(service, "write-a", title="different")


def test_revocation_is_monotonic_against_an_unrelated_stale_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    seeded = ExternalWriteService(path)
    _grant(seeded)
    revoker = ExternalWriteService(path)
    stale_writer = ExternalWriteService(path)

    revoker.revoke_standing_permission("permission-a", revoked_at=NOW)
    _prepare(stale_writer, "unrelated-write", target="calendar:other")

    reopened = ExternalWriteService(path)
    permission = reopened.list_permissions()[0]
    assert permission.revoked_at == NOW

    _prepare(reopened, "authority-check")
    result = reopened.execute("authority-check", RecordingAdapter(), completed_at=NOW)
    assert isinstance(result, ApprovalRequest)


def test_reactivating_a_revoked_permission_identifier_is_rejected(
    tmp_path: Path,
) -> None:
    service = ExternalWriteService(tmp_path / "external-writes.json")
    _grant(service)
    service.revoke_standing_permission("permission-a", revoked_at=NOW)

    with pytest.raises(ExternalWriteConflict):
        _grant(service)


def test_revocation_before_dispatch_blocks_the_adapter(tmp_path: Path) -> None:
    path = tmp_path / "external-writes.json"
    service = ExternalWriteService(path)
    _grant(service)
    _prepare(service, "write-a")
    adapter = RecordingAdapter()

    def revoke_between_claim_and_dispatch(_owner: str) -> None:
        ExternalWriteService(path).revoke_standing_permission(
            "permission-a", revoked_at=NOW
        )

    result = service.execute(
        "write-a",
        adapter,
        completed_at=NOW,
        on_claimed=revoke_between_claim_and_dispatch,
    )

    assert isinstance(result, ApprovalRequest)
    assert adapter.calls == 0


def test_competing_workers_claim_one_write_and_call_the_adapter_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    first = ExternalWriteService(path)
    second = ExternalWriteService(path)
    _prepare(first, "write-a")
    first.approve_exact("write-a", approval_id="approval-a", approved_at=NOW)
    first_adapter = RecordingAdapter()
    second_adapter = RecordingAdapter()

    def competing_claim(_owner: str) -> None:
        with pytest.raises(ExternalWriteClaimConflict):
            second.execute("write-a", second_adapter, completed_at=NOW)

    receipt = first.execute(
        "write-a",
        first_adapter,
        completed_at=NOW,
        on_claimed=competing_claim,
    )

    assert receipt.status == "success"
    assert first_adapter.calls == 1
    assert second_adapter.calls == 0


def test_restart_returns_the_existing_receipt_and_idempotency_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    service = ExternalWriteService(path)
    _prepare(service, "write-a")
    service.approve_exact("write-a", approval_id="approval-a", approved_at=NOW)
    first_adapter = RecordingAdapter()
    receipt = service.execute("write-a", first_adapter, completed_at=NOW)

    restarted_adapter = RecordingAdapter()
    repeated = ExternalWriteService(path).execute(
        "write-a", restarted_adapter, completed_at=NOW
    )

    assert repeated == receipt
    assert repeated.idempotency_key == f"reckoning-write:write-a:{payload_digest({'title': 'write-a'})}"
    assert first_adapter.calls == 1
    assert restarted_adapter.calls == 0


def test_ambiguous_adapter_outcome_is_unknown_and_not_retried(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    service = ExternalWriteService(path)
    _prepare(service, "write-a")
    service.approve_exact("write-a", approval_id="approval-a", approved_at=NOW)
    adapter = ExplodingAdapter()

    receipt = service.execute(
        "write-a", adapter, completed_at=NOW, max_retries=3
    )

    assert receipt.status == "unknown"
    assert adapter.calls == 1

    retry_adapter = RecordingAdapter()
    repeated = ExternalWriteService(path).execute(
        "write-a", retry_adapter, completed_at=NOW
    )
    assert repeated == receipt
    assert retry_adapter.calls == 0


def test_explicit_recovery_records_the_previous_claim_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    service = ExternalWriteService(path)
    _prepare(service, "write-a")
    service.approve_exact("write-a", approval_id="approval-a", approved_at=NOW)
    repository = _repository(path)
    prepared = service.list_prepared()[0]
    authorization = repository.authorization(prepared)
    assert authorization is not None
    claim = repository.claim_execution(
        prepared,
        owner="interrupted-worker",
        idempotency_key="reckoning-write:write-a:interrupted",
        authorization=authorization,
        claimed_at=NOW,
    )

    recovered = repository.recover_execution(
        "write-a",
        expected_revision=claim.revision,
        new_owner="recovery-owner",
        reason="operator recovered an interrupted synthetic claim",
        recovered_at=NOW + timedelta(minutes=1),
    )

    assert recovered.claim_owner == "recovery-owner"
    assert recovered.claim_state == "claimed"
    with pytest.raises(ExternalWriteConflict):
        repository.recover_execution(
            "write-a",
            expected_revision=claim.revision,
            new_owner="stale-owner",
            reason="stale recovery",
            recovered_at=NOW,
        )


def _repository(path: Path):
    from reckoning.external_write_store import SQLiteExternalWriteRepository

    return SQLiteExternalWriteRepository(path)


def test_legacy_external_writes_migrate_once_with_rollback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    title_payload = {"title": "Legacy write"}
    legacy = {
        "schema_version": 1,
        "writes": [
            {
                "id": "legacy-write",
                "connector_id": "synthetic-calendar",
                "action_type": "create-event",
                "target": "calendar:synthetic",
                "trigger": "manual",
                "boundary": "one event",
                "payload": title_payload,
                "prepared_at": NOW.isoformat(),
                "payload_digest": payload_digest(title_payload),
                "exact_approval_id": "legacy-approval",
            }
        ],
        "permissions": [
            {
                "id": "legacy-permission",
                "connector_id": "synthetic-calendar",
                "action_type": "delete-event",
                "target": "calendar:synthetic",
                "trigger": "manual",
                "boundary": "one event",
                "granted_at": NOW.isoformat(),
                "revoked_at": NOW.isoformat(),
            }
        ],
        "receipts": [],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    service = ExternalWriteService(path)
    assert service.list_prepared()[0].exact_approval_id == "legacy-approval"
    assert service.list_permissions()[0].revoked_at == NOW
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": 2,
        "authority": "sqlite",
        "database": ROOT_DATABASE_FILENAME,
        "migration_state": "sqlite-authoritative",
    }
    rollback = path.with_name("external-writes.rollback.json")
    assert json.loads(rollback.read_text(encoding="utf-8")) == legacy

    restarted = ExternalWriteService(path)
    assert restarted.list_prepared()[0].exact_approval_id == "legacy-approval"


def test_invalid_legacy_external_writes_do_not_activate_sqlite_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "external-writes.json"
    duplicate = {
        "id": "duplicate",
        "connector_id": "synthetic-calendar",
        "action_type": "create-event",
        "target": "calendar:synthetic",
        "trigger": "manual",
        "boundary": "one event",
        "payload": {"title": "X"},
        "prepared_at": NOW.isoformat(),
        "payload_digest": payload_digest({"title": "X"}),
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "writes": [duplicate, duplicate],
                "permissions": [],
                "receipts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Invalid legacy external-write"):
        ExternalWriteService(path)


def test_concurrent_first_initialization_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "external-writes.json"
    barrier = threading.Barrier(6)

    def initialize() -> None:
        barrier.wait()
        ExternalWriteService(path)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda _index: initialize(), range(6)))

    _prepare(ExternalWriteService(path), "write-a")
    assert ExternalWriteService(path).list_prepared()[0].id == "write-a"


def test_clean_encrypted_restore_preserves_external_write_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    archive = tmp_path / "state.reckoning"
    setup_instance(source, "local")

    service = ExternalWriteService(source / "confirmed-state" / "external-writes.json")
    _grant(service, "revoked-permission", target="calendar:other")
    service.revoke_standing_permission("revoked-permission", revoked_at=NOW)
    _prepare(service, "approved-write")
    service.approve_exact("approved-write", approval_id="approval-a", approved_at=NOW)
    _prepare(service, "unknown-write")
    service.approve_exact("unknown-write", approval_id="approval-b", approved_at=NOW)
    service.execute("unknown-write", ExplodingAdapter(), completed_at=NOW)

    create_transfer(source, archive, PASSPHRASE, kind="backup")
    restore_transfer(archive, restored, PASSPHRASE)

    restored_service = ExternalWriteService(
        restored / "confirmed-state" / "external-writes.json"
    )
    permissions = {item.id: item for item in restored_service.list_permissions()}
    assert permissions["revoked-permission"].revoked_at == NOW
    assert restored_service.list_prepared()[0].exact_approval_id == "approval-a"
    unknown = restored_service.list_receipts()[0]
    assert unknown.status == "unknown"
    adapter = RecordingAdapter()
    repeated = restored_service.execute("unknown-write", adapter, completed_at=NOW)
    assert repeated == unknown
    assert adapter.calls == 0


def test_validate_database_accepts_external_write_tables(tmp_path: Path) -> None:
    from reckoning.model_run_store import validate_database

    path = tmp_path / "external-writes.json"
    _prepare(ExternalWriteService(path), "write-a")
    validate_database(tmp_path / ROOT_DATABASE_FILENAME)
