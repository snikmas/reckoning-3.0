from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from reckoning.automation import JsonFileAutomationRepository, RoutineService
from reckoning.connectors import ExternalWriteService, WriteResult
from reckoning.interfaces import (
    ChannelMessage,
    ChannelSession,
    InterfaceState,
    JsonFileInterfaceRepository,
)
from reckoning.processing import (
    JsonFileProcessingGrantRepository,
    ProcessingDestination,
    ProcessingGrantConflict,
    ProcessingScope,
)


NOW = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)


def _routine(service: RoutineService, identifier: str) -> None:
    service.propose(
        proposal_id=identifier,
        routine_id=identifier,
        source_request=f"Run synthetic routine {identifier}.",
        created_at=NOW,
        trigger="manual synthetic trigger",
        source_scope=(f"source:{identifier}",),
        context_scope=(f"context:{identifier}",),
        tools=(f"tool:{identifier}",),
        permissions=(f"permission:{identifier}",),
        delivery="private-web",
        model_policy="no model",
        cost_ceiling=0,
        retry_limit=0,
        delegation_policy="direct execution only",
        failure_behavior="record failure",
    )


def _probe_interfaces(root: Path) -> dict[str, object]:
    path = root / "interfaces.json"
    first = JsonFileInterfaceRepository(path)
    second = JsonFileInterfaceRepository(path)
    first_snapshot = first.load()
    second_snapshot = second.load()
    first.save(
        replace(
            first_snapshot,
            sessions=(
                ChannelSession(
                    "web",
                    "web-session",
                    (ChannelMessage("user", "synthetic web message"),),
                ),
            ),
        )
    )
    second.save(
        replace(
            second_snapshot,
            sessions=(
                ChannelSession(
                    "telegram",
                    "telegram-session",
                    (ChannelMessage("user", "synthetic Telegram message"),),
                ),
            ),
        )
    )
    surviving = [
        f"{session.channel}:{session.session_id}"
        for session in JsonFileInterfaceRepository(path).load().sessions
    ]
    expected = ["web:web-session", "telegram:telegram-session"]
    return {
        "result": (
            "preserved" if surviving == expected else "lost-update-confirmed"
        ),
        "expected": expected,
        "surviving": surviving,
    }


def _probe_automation(root: Path) -> dict[str, object]:
    path = root / "automation.json"
    seeded = RoutineService(JsonFileAutomationRepository(path))
    _routine(seeded, "routine-a")
    _routine(seeded, "routine-b")
    first = RoutineService(JsonFileAutomationRepository(path))
    second = RoutineService(JsonFileAutomationRepository(path))
    first.confirm("routine-a")
    second.confirm("routine-b")
    current = JsonFileAutomationRepository(path)
    statuses = {
        item.id: item.status
        for item in current.list_proposals()
    }
    expected = {"routine-a": "confirmed", "routine-b": "confirmed"}
    return {
        "result": (
            "preserved"
            if statuses == expected
            else "confirmed-authority-rollback"
        ),
        "expected": expected,
        "observed": statuses,
    }


def _probe_external_writes(root: Path) -> dict[str, object]:
    prepared_path = root / "prepared-writes.json"
    first = ExternalWriteService(prepared_path)
    second = ExternalWriteService(prepared_path)
    for service, identifier in ((first, "write-a"), (second, "write-b")):
        service.prepare(
            write_id=identifier,
            connector_id="synthetic-calendar",
            action_type="create-event",
            target="calendar:synthetic",
            trigger="manual",
            boundary=identifier,
            payload={"title": identifier},
            prepared_at=NOW,
        )
    surviving = list(ExternalWriteService(prepared_path).pending_approval_ids())

    approval_path = root / "exact-approvals.json"
    seeded_approvals = ExternalWriteService(approval_path)
    for identifier in ("write-a", "write-b"):
        seeded_approvals.prepare(
            write_id=identifier,
            connector_id="synthetic-calendar",
            action_type="create-event",
            target="calendar:synthetic",
            trigger="manual",
            boundary=identifier,
            payload={"title": identifier},
            prepared_at=NOW,
        )
    first_approval = ExternalWriteService(approval_path)
    second_approval = ExternalWriteService(approval_path)
    first_approval.approve_exact(
        "write-a", approval_id="approval-a", approved_at=NOW
    )
    second_approval.approve_exact(
        "write-b", approval_id="approval-b", approved_at=NOW
    )
    unexpectedly_pending = list(
        ExternalWriteService(approval_path).pending_approval_ids()
    )

    authority_path = root / "external-authority.json"
    seeded = ExternalWriteService(authority_path)
    seeded.grant_standing_permission(
        permission_id="permission-a",
        connector_id="synthetic-calendar",
        action_type="create-event",
        target="calendar:synthetic",
        trigger="manual",
        boundary="one event",
        granted_at=NOW,
    )
    revoker = ExternalWriteService(authority_path)
    stale_writer = ExternalWriteService(authority_path)
    revoker.revoke_standing_permission("permission-a", revoked_at=NOW)
    stale_writer.prepare(
        write_id="unrelated-write",
        connector_id="synthetic-calendar",
        action_type="delete-event",
        target="calendar:other",
        trigger="manual",
        boundary="one other event",
        payload={"event_id": "other"},
        prepared_at=NOW,
    )
    reopened = ExternalWriteService(authority_path)
    reopened.prepare(
        write_id="authority-check",
        connector_id="synthetic-calendar",
        action_type="create-event",
        target="calendar:synthetic",
        trigger="manual",
        boundary="one event",
        payload={"title": "authority check"},
        prepared_at=NOW,
    )

    class SyntheticAdapter:
        def execute(
            self, payload: dict[str, str], idempotency_key: str
        ) -> WriteResult:
            del payload, idempotency_key
            return WriteResult("success", "synthetic-event", "synthetic success")

    authority_result = reopened.execute(
        "authority-check",
        SyntheticAdapter(),
        completed_at=NOW,
    )
    revoked_restored = (
        getattr(authority_result, "authorization_kind", None)
        == "standing_permission"
    )
    failed = (
        surviving != ["write-a", "write-b"]
        or unexpectedly_pending != []
        or revoked_restored
    )
    return {
        "result": (
            "preserved" if not failed else "lost-update-and-authority-rollback"
        ),
        "expected_prepared": ["write-a", "write-b"],
        "surviving_prepared": surviving,
        "expected_pending_after_two_approvals": [],
        "observed_pending_after_two_approvals": unexpectedly_pending,
        "revoked_permission_restored": revoked_restored,
    }


def _probe_processing_grants(root: Path) -> dict[str, object]:
    path = root / "processing-grants.json"
    repository = JsonFileProcessingGrantRepository(path)
    first = ProcessingScope(
        repository,
        ProcessingDestination("provider-a", "https://a.invalid/v1", "cloud"),
    )
    second = ProcessingScope(
        repository,
        ProcessingDestination("provider-b", "https://b.invalid/v1", "cloud"),
    )
    first.change(("current-request",), changed_at=NOW, expected_version=0)
    second.change(("current-request",), changed_at=NOW, expected_version=0)
    preserved = [
        scope.destination.id
        for scope in (first, second)
        if scope.review().grant_version == 1
    ]

    stale = ProcessingScope(
        repository,
        ProcessingDestination("provider-a", "https://a.invalid/v1", "cloud"),
    )
    conflict = False
    try:
        stale.change(("permissions",), changed_at=NOW, expected_version=0)
    except ProcessingGrantConflict:
        conflict = True
    return {
        "result": "preserved-with-conflict",
        "preserved": preserved,
        "stale_same_destination_rejected": conflict,
    }


def main() -> None:
    with TemporaryDirectory(prefix="reckoning-issue-113-") as temporary:
        root = Path(temporary)
        results = {
            "interfaces": _probe_interfaces(root / "interfaces"),
            "automation": _probe_automation(root / "automation"),
            "external_writes": _probe_external_writes(root / "writes"),
            "processing_grants": _probe_processing_grants(root / "processing"),
        }
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
