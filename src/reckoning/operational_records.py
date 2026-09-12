from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from reckoning.interfaces import (
    OperationalFailure,
    OperationalSnapshot,
    RunReceipt,
)
from reckoning.json_store import read_json
from reckoning.root_database import ROOT_DATABASE_FILENAME, logical_root_for


class LocalOperationalRecordSource:
    """Project durable service records into Control without copying their state."""

    def __init__(
        self,
        data_dir: Path,
        *,
        connector_data_dir: Path | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._connector_data_dir = connector_data_dir or data_dir

    def snapshot(self) -> OperationalSnapshot:
        return _merge_snapshots(
            self._continuity_snapshot(),
            self._model_run_snapshot(),
            self._telegram_snapshot(),
            self._automation_snapshot(),
            self._connector_snapshot(),
            self._watch_snapshot(),
            self._briefing_snapshot(),
            self._external_write_snapshot(),
        )

    def _continuity_snapshot(self) -> OperationalSnapshot:
        continuity_path = self._data_dir / "continuity.json"
        data = read_json(
            continuity_path,
            default={"schema_version": 1, "reckonings": [], "check_ins": []},
        )
        if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
            database = logical_root_for(continuity_path) / ROOT_DATABASE_FILENAME
            try:
                with closing(
                    sqlite3.connect(
                        f"{database.resolve().as_uri()}?mode=ro", uri=True
                    )
                ) as connection:
                    reckonings = [
                        json.loads(str(row[0]))
                        for row in connection.execute(
                            "SELECT payload FROM continuity_reckonings "
                            "ORDER BY sequence"
                        ).fetchall()
                    ]
                data = {"schema_version": 1, "reckonings": reckonings}
            except (sqlite3.Error, json.JSONDecodeError) as error:
                raise RuntimeError(
                    "Continuity operational storage is invalid."
                ) from error
        elif data.get("schema_version") != 1:
            raise RuntimeError("Unsupported continuity storage schema.")
        memory: list[str] = []
        evidence: list[str] = []
        for reckoning in _objects(data.get("reckonings", [])):
            draft = _object(reckoning.get("draft", {}))
            evidence.extend(
                f"{item['id']}: {item['content']} ({item['source']})"
                for item in _objects(draft.get("evidence", []))
            )
            current: dict[str, dict[str, Any]] = {}
            for record in _objects(reckoning.get("record_versions", [])):
                current[str(record["record_id"])] = record
            memory.extend(
                f"{record['record_type']} {record['record_id']}: {record['meaning']}"
                for record in current.values()
                if record.get("status") == "confirmed"
            )
        return OperationalSnapshot(evidence=tuple(evidence), memory=tuple(memory))

    def _model_run_snapshot(self) -> OperationalSnapshot:
        model_run_path = self._data_dir / "model-runs.json"
        data = read_json(
            model_run_path,
            default={"schema_version": 1, "runs": []},
        )
        if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
            from reckoning.model_run_store import SQLiteModelRunRepository

            runs = SQLiteModelRunRepository(model_run_path).list_runs()
            data = {
                "schema_version": 1,
                "runs": [
                    {**asdict(run), "requested_at": run.requested_at.isoformat()}
                    for run in runs
                ],
            }
        elif data.get("schema_version") != 1:
            raise RuntimeError("Unsupported model-run storage schema.")
        receipts: list[RunReceipt] = []
        failures: list[OperationalFailure] = []
        for item in _objects(data.get("runs", [])):
            run_id = str(item["id"])
            occurred_at = datetime.fromisoformat(str(item["requested_at"]))
            provider = str(item["provider"])
            model = str(item["model"])
            status = str(item["status"])
            receipts.append(
                RunReceipt(
                    id=run_id,
                    occurred_at=occurred_at,
                    status=status,
                    summary=f"{provider} model run with {model}",
                    evidence=(
                        f"model calls: {item['model_calls']}",
                        f"latency: {item['latency_ms']} ms",
                        f"retries: {item['retries']}",
                        f"tokens: {item['input_tokens']} input, {item['output_tokens']} output",
                    ),
                    cost_units=int(item["billable_units"]),
                    tools=(f"model:{provider}",),
                    actions=("generate response",),
                )
            )
            if item.get("failure"):
                failures.append(
                    OperationalFailure(
                        id=f"model-failure:{run_id}",
                        run_id=run_id,
                        summary=str(item["failure"]),
                        occurred_at=occurred_at,
                    )
                )
        return OperationalSnapshot(tuple(receipts), tuple(failures))

    def _telegram_snapshot(self) -> OperationalSnapshot:
        from reckoning.telegram_delivery import inspect_telegram_updates

        records = inspect_telegram_updates(
            self._data_dir / "telegram-delivery.json"
        )
        receipts: list[RunReceipt] = []
        failures: list[OperationalFailure] = []
        connector_health: list[str] = []
        for record in records:
            problem = _telegram_problem(record)
            if problem is None:
                continue
            status, summary = problem
            run_id = f"telegram:update:{record.update_id}"
            connector_health.append(
                f"telegram update {record.update_id}: {status}"
            )
            receipts.append(
                RunReceipt(
                    id=run_id,
                    occurred_at=record.updated_at,
                    status=status,
                    summary=summary,
                    evidence=(
                        f"processing state: {record.processing_state}",
                        f"delivery state: {record.delivery_state}",
                        f"delivery attempts: {record.delivery_attempts}",
                    ),
                    connector_health=(connector_health[-1],),
                    tools=("telegram",),
                    actions=("process and deliver Telegram update",),
                )
            )
            failures.append(
                OperationalFailure(
                    id=f"telegram-failure:{record.update_id}",
                    run_id=run_id,
                    summary=summary,
                    occurred_at=record.updated_at,
                )
            )
        return OperationalSnapshot(
            tuple(receipts),
            tuple(failures),
            connector_health=tuple(connector_health),
        )

    def _automation_snapshot(self) -> OperationalSnapshot:
        data = read_json(
            self._data_dir / "automation.json",
            default={"schema_version": 1, "proposals": [], "runs": [], "receipts": []},
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported automation storage schema.")
        proposals = {
            str(item["id"]): item for item in _objects(data.get("proposals", []))
        }
        runs = {
            str(item["id"]): item for item in _objects(data.get("runs", []))
        }
        receipts: list[RunReceipt] = []
        failures: list[OperationalFailure] = []
        for item in _objects(data.get("receipts", [])):
            run_id = str(item["run_id"])
            run = runs.get(run_id, {})
            proposal = proposals.get(str(item["proposal_id"]), {})
            results = _objects(item.get("results", []))
            status = str(item["status"])
            occurred_at = datetime.fromisoformat(str(item["completed_at"]))
            details = tuple(str(result["detail"]) for result in results)
            tools = tuple(str(value) for value in proposal.get("tools", []))
            actions = tuple(
                str(step["action"]) for step in _objects(run.get("steps", []))
            )
            routine_id = str(proposal.get("routine_id", item["proposal_id"]))
            receipts.append(
                RunReceipt(
                    id=run_id,
                    occurred_at=occurred_at,
                    status=status,
                    summary=f"Routine {routine_id} finished {status}",
                    evidence=details,
                    memory=tuple(str(value) for value in proposal.get("context_scope", [])),
                    permissions=tuple(
                        str(value) for value in proposal.get("permissions", [])
                    ),
                    cost_units=sum(int(result.get("cost_units", 0)) for result in results),
                    routine_health=(f"{routine_id}: {status}",),
                    tools=tools,
                    actions=actions,
                )
            )
            failed_details = tuple(
                str(result["detail"])
                for result in results
                if result.get("status") in {"failed", "blocked"}
            )
            if failed_details:
                failures.append(
                    OperationalFailure(
                        id=f"routine-failure:{run_id}",
                        run_id=run_id,
                        summary="; ".join(failed_details),
                        occurred_at=occurred_at,
                    )
                )
        return OperationalSnapshot(tuple(receipts), tuple(failures))

    def _connector_snapshot(self) -> OperationalSnapshot:
        data = read_json(
            self._connector_data_dir / "connectors.json",
            default={
                "schema_version": 1,
                "connections": [],
                "items": [],
                "health": [],
                "content_results": [],
            },
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported connector storage schema.")
        connections = {
            str(item["id"]): item
            for item in _objects(data.get("connections", []))
        }
        health_values: list[str] = []
        receipts: list[RunReceipt] = []
        failures: list[OperationalFailure] = []
        for item in _objects(data.get("health", [])):
            connector_id = str(item["connector_id"])
            status = str(item["status"])
            detail = str(item["detail"])
            health_values.append(f"{connector_id}: {status} - {detail}")
            if not item.get("checked_at"):
                continue
            occurred_at = datetime.fromisoformat(str(item["checked_at"]))
            run_id = f"connector:{connector_id}:{occurred_at.isoformat()}"
            connection = connections.get(connector_id, {})
            read_scopes = tuple(
                str(value) for value in connection.get("granted_read_scope", [])
            )
            receipts.append(
                RunReceipt(
                    id=run_id,
                    occurred_at=occurred_at,
                    status=status,
                    summary=detail,
                    evidence=(detail,),
                    permissions=tuple(
                        f"connector:{connector_id}:{scope}" for scope in read_scopes
                    ),
                    connector_health=(
                        f"{connector_id}: {status} - {detail}",
                    ),
                    tools=read_scopes,
                    actions=(f"synchronize {connector_id}",),
                )
            )
            if status == "failed":
                failures.append(
                    OperationalFailure(
                        id=f"connector-failure:{connector_id}:{occurred_at.isoformat()}",
                        run_id=run_id,
                        summary=detail,
                        occurred_at=occurred_at,
                    )
                )
        return OperationalSnapshot(
            receipts=tuple(receipts),
            failures=tuple(failures),
            connector_health=tuple(health_values),
        )

    def _watch_snapshot(self) -> OperationalSnapshot:
        data = read_json(
            self._data_dir / "watches.json",
            default={"schema_version": 1, "watches": [], "receipts": []},
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported watch storage schema.")
        watches = {str(item["id"]): item for item in _objects(data.get("watches", []))}
        receipts: list[RunReceipt] = []
        failures: list[OperationalFailure] = []
        for item in _objects(data.get("receipts", [])):
            run_id = str(item["id"])
            watch_id = str(item["watch_id"])
            watch = watches.get(watch_id, {})
            occurred_at = datetime.fromisoformat(str(item["checked_at"]))
            notified = _objects(item.get("notified", []))
            suppressed = _objects(item.get("suppressed", []))
            status = str(item["status"])
            receipts.append(
                RunReceipt(
                    id=run_id,
                    occurred_at=occurred_at,
                    status=status,
                    summary=f"Watch {watch_id} finished {status}",
                    evidence=tuple(str(value["evidence"]) for value in notified),
                    memory=tuple(
                        str(value["goal_or_decision_id"]) for value in notified
                    ),
                    cost_units=int(item["total_cost_units"]),
                    routine_health=(f"watch {watch_id}: {status}",),
                    suppressed_findings=tuple(
                        f"{value['id']}: {value['summary']}" for value in suppressed
                    ),
                    tools=tuple(str(value) for value in watch.get("source_scope", [])),
                    actions=("observe approved sources",),
                )
            )
            if status == "blocked":
                failures.append(
                    OperationalFailure(
                        id=f"watch-failure:{run_id}",
                        run_id=run_id,
                        summary="Watch exceeded its confirmed budget.",
                        occurred_at=occurred_at,
                    )
                )
        return OperationalSnapshot(tuple(receipts), tuple(failures))

    def _briefing_snapshot(self) -> OperationalSnapshot:
        data = read_json(
            self._data_dir / "briefings.json",
            default={"schema_version": 1, "receipts": []},
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported briefing storage schema.")
        receipts = tuple(
            RunReceipt(
                id=str(item["run_id"]),
                occurred_at=datetime.fromisoformat(str(item["delivered_at"])),
                status=str(item["status"]),
                summary=(
                    "Briefing found no material change"
                    if item.get("no_material_change")
                    else "Selective briefing delivered"
                ),
                evidence=tuple(
                    str(_object(value["finding"])["summary"])
                    for value in _objects(item.get("items", []))
                ),
                cost_units=int(item["total_cost_units"]),
                routine_health=(f"briefing: {item['status']}",),
                suppressed_findings=tuple(
                    str(value) for value in item.get("suppressed_ids", [])
                ),
                actions=("deliver selective briefing",),
            )
            for item in _objects(data.get("receipts", []))
        )
        return OperationalSnapshot(receipts=receipts)

    def _external_write_snapshot(self) -> OperationalSnapshot:
        data = read_json(
            self._data_dir / "external-writes.json",
            default={
                "schema_version": 1,
                "writes": [],
                "permissions": [],
                "receipts": [],
            },
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported external-write storage schema.")
        writes = _objects(data.get("writes", []))
        permissions = _objects(data.get("permissions", []))
        active_permissions = tuple(
            str(item["id"]) for item in permissions if item.get("revoked_at") is None
        )
        receipt_ids = {str(item["write_id"]) for item in _objects(data.get("receipts", []))}
        approvals = tuple(
            f"write:{item['id']}"
            for item in writes
            if str(item["id"]) not in receipt_ids
            and not item.get("exact_approval_id")
            and not _standing_permission_matches(item, permissions)
        )
        receipts: list[RunReceipt] = []
        failures: list[OperationalFailure] = []
        by_id = {str(item["id"]): item for item in writes}
        for item in _objects(data.get("receipts", [])):
            write_id = str(item["write_id"])
            prepared = by_id.get(write_id, {})
            occurred_at = datetime.fromisoformat(str(item["completed_at"]))
            status = str(item["status"])
            authorization_kind = str(item.get("authorization_kind", "unknown"))
            authorization_id = str(item.get("authorization_id", "unknown"))
            authorization_scope = str(item.get("authorization_scope", ""))
            used_permissions = (
                (authorization_id,)
                if authorization_kind == "standing_permission"
                else ()
            )
            used_approvals = (
                (authorization_id,)
                if authorization_kind == "exact_approval"
                else ()
            )
            authority_evidence = (
                f"authorization: {authorization_kind}:{authorization_id}",
                f"authorization scope: {authorization_scope}",
            )
            receipts.append(
                RunReceipt(
                    id=write_id,
                    occurred_at=occurred_at,
                    status=status,
                    summary=str(item["detail"]),
                    evidence=(
                        f"external id: {item.get('external_id') or 'none'}",
                        *authority_evidence,
                    ),
                    permissions=used_permissions,
                    approvals=used_approvals,
                    connector_health=(f"{item['connector_id']}: {status}",),
                    tools=(f"connector:{item['connector_id']}",),
                    actions=(str(prepared.get("action_type", "external write")),),
                )
            )
            if status == "failed":
                failures.append(
                    OperationalFailure(
                        id=f"write-failure:{write_id}",
                        run_id=write_id,
                        summary=str(item["detail"]),
                        occurred_at=occurred_at,
                    )
                )
        return OperationalSnapshot(
            receipts=tuple(receipts),
            failures=tuple(failures),
            permissions=active_permissions,
            approvals=approvals,
        )


def _telegram_problem(record: Any) -> tuple[str, str] | None:
    if record.delivery_state == "unknown":
        return (
            "delivery-unknown",
            record.delivery_failure
            or "Telegram delivery outcome is unknown; automatic retry is disabled.",
        )
    if record.delivery_state == "failed":
        return (
            "delivery-failed",
            record.delivery_failure or "Telegram delivery failed.",
        )
    if record.processing_state == "processing-unknown":
        return (
            "processing-unknown",
            record.processing_failure or "Telegram processing outcome is unknown.",
        )
    if record.processing_state == "provider-failed":
        return (
            "provider-failed",
            record.processing_failure or "Telegram model processing failed.",
        )
    return None


def _objects(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError("Operational storage contains an invalid object list.")
    return tuple(value)


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("Operational storage contains an invalid object.")
    return value


def _standing_permission_matches(
    write: dict[str, Any], permissions: tuple[dict[str, Any], ...]
) -> bool:
    fields = ("connector_id", "action_type", "target", "trigger", "boundary")
    return any(
        permission.get("revoked_at") is None
        and all(permission.get(field) == write.get(field) for field in fields)
        for permission in permissions
    )


def _merge_snapshots(*snapshots: OperationalSnapshot) -> OperationalSnapshot:
    return OperationalSnapshot(
        receipts=tuple(item for value in snapshots for item in value.receipts),
        failures=tuple(item for value in snapshots for item in value.failures),
        evidence=_unique(item for value in snapshots for item in value.evidence),
        memory=_unique(item for value in snapshots for item in value.memory),
        permissions=_unique(item for value in snapshots for item in value.permissions),
        approvals=_unique(item for value in snapshots for item in value.approvals),
        routine_health=_unique(item for value in snapshots for item in value.routine_health),
        connector_health=_unique(
            item for value in snapshots for item in value.connector_health
        ),
        suppressed_findings=_unique(
            item for value in snapshots for item in value.suppressed_findings
        ),
        tools=_unique(item for value in snapshots for item in value.tools),
        actions=_unique(item for value in snapshots for item in value.actions),
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
