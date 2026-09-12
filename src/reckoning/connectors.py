from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
from types import MappingProxyType
from collections.abc import Callable, Mapping
from typing import Literal, Protocol
from uuid import uuid4

from reckoning.external_content import (
    ExternalContentBoundary,
    ExternalContentResult,
    UntrustedContent,
)
from reckoning.json_store import atomic_write_json, read_json

ConnectorStatus = Literal["connected", "revoked", "disconnected"]
DeletionState = Literal["active", "suppressed", "deleted"]


@dataclass(frozen=True)
class ConnectorItem:
    external_id: str
    content: str
    provenance: str


@dataclass(frozen=True)
class ImportedConnectorItem:
    connector_id: str
    external_id: str
    content: str
    provenance: str
    deletion_state: DeletionState = "active"


@dataclass(frozen=True)
class ConnectorConnection:
    id: str
    identity: str
    granted_read_scope: tuple[str, ...]
    available_write_scope: tuple[str, ...]
    granted_write_scope: tuple[str, ...] = ()
    status: ConnectorStatus = "connected"


@dataclass(frozen=True)
class ConnectorHealth:
    connector_id: str
    status: Literal["healthy", "revoked", "disconnected", "failed"]
    checked_at: datetime | None
    detail: str


@dataclass(frozen=True)
class SynchronizationResult:
    connector_id: str
    synchronized_at: datetime
    items: tuple[ImportedConnectorItem, ...]


class ReadConnector(Protocol):
    connector_id: str
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]

    def verify_identity(self) -> str: ...

    def synchronize(self, read_scope: tuple[str, ...]) -> tuple[ConnectorItem, ...]: ...


class JsonFileReadConnector:
    """Deterministic read-only connector for an exported JSON source."""

    read_scopes = ("documents:read",)
    write_scopes: tuple[str, ...] = ()

    def __init__(self, path: Path, *, connector_id: str = "json-export") -> None:
        self.connector_id = connector_id
        self._path = path

    def verify_identity(self) -> str:
        return str(self._load()["identity"])

    def synchronize(self, read_scope: tuple[str, ...]) -> tuple[ConnectorItem, ...]:
        if read_scope != self.read_scopes:
            raise PermissionError("The JSON connector supports documents:read only.")
        data = self._load()
        items = data.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise RuntimeError("The JSON connector item list is invalid.")
        return tuple(
            ConnectorItem(
                external_id=str(item["id"]),
                content=str(item["content"]),
                provenance=f"{self.connector_id}:{item['id']}",
            )
            for item in items
        )

    def _load(self) -> dict[str, object]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("The JSON connector source is invalid.") from error
        if not isinstance(data, dict) or not str(data.get("identity", "")).strip():
            raise RuntimeError("The JSON connector identity is invalid.")
        return data


class ConnectorService:
    def __init__(
        self,
        content_boundary: ExternalContentBoundary | None = None,
        *,
        path: Path | None = None,
    ) -> None:
        self._path = path
        self._connections: dict[str, ConnectorConnection] = {}
        self._adapters: dict[str, ReadConnector] = {}
        self._items: dict[tuple[str, str], ImportedConnectorItem] = {}
        self._health: dict[str, ConnectorHealth] = {}
        self._content_boundary = content_boundary or ExternalContentBoundary()
        self._content_results: dict[tuple[str, str], ExternalContentResult] = {}
        if path is not None:
            data = read_json(
                path,
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
            self._connections = {
                str(item["id"]): _connection_from_data(item)
                for item in data["connections"]
            }
            self._items = {
                (str(item["connector_id"]), str(item["external_id"])):
                    _imported_item_from_data(item)
                for item in data["items"]
            }
            self._health = {
                str(item["connector_id"]): _health_from_data(item)
                for item in data["health"]
            }
            self._content_results = {
                (str(item["connector_id"]), str(item["external_id"])):
                    _content_result_from_data(item)
                for item in data.get("content_results", [])
            }

    def connect(
        self, adapter: ReadConnector, *, read_scope: tuple[str, ...]
    ) -> ConnectorConnection:
        if not read_scope or not set(read_scope).issubset(adapter.read_scopes):
            raise PermissionError("Selected read scope is not offered by the connector.")
        identity = adapter.verify_identity().strip()
        if not identity:
            raise RuntimeError("The connector did not verify an identity.")
        connection = ConnectorConnection(
            adapter.connector_id,
            identity,
            read_scope,
            adapter.write_scopes,
        )
        self._connections[connection.id] = connection
        self._adapters[connection.id] = adapter
        self._health[connection.id] = ConnectorHealth(
            connection.id, "healthy", None, "Identity verified; synchronization pending."
        )
        self._flush()
        return connection

    def synchronize(
        self, connector_id: str, *, synchronized_at: datetime
    ) -> SynchronizationResult:
        connection = self._connection(connector_id)
        if connection.status != "connected":
            raise PermissionError(f"Connector access is {connection.status}.")
        adapter = self._adapters[connector_id]
        try:
            incoming = adapter.synchronize(connection.granted_read_scope)
        except Exception as error:
            self._health[connector_id] = ConnectorHealth(
                connector_id, "failed", synchronized_at, str(error)
            )
            self._flush()
            raise
        for item in incoming:
            key = (connector_id, item.external_id)
            existing = self._items.get(key)
            if existing is not None and existing.deletion_state != "active":
                self._content_results.pop(key, None)
                continue
            self._content_results[key] = self._content_boundary.inspect(
                UntrustedContent(item.external_id, item.provenance, item.content),
                protected_prompt_layers=(
                    "protected_product_contract",
                    "product_identity",
                ),
                allowed_tools=connection.granted_read_scope,
                permissions=tuple(
                    f"connector:{connector_id}:{scope}"
                    for scope in connection.granted_read_scope
                ),
            )
            if existing is None:
                self._items[key] = ImportedConnectorItem(
                    connector_id,
                    item.external_id,
                    item.content,
                    item.provenance,
                )
            elif existing.deletion_state == "active":
                self._items[key] = replace(
                    existing, content=item.content, provenance=item.provenance
                )
        visible = self.imported_items(connector_id)
        self._health[connector_id] = ConnectorHealth(
            connector_id,
            "healthy",
            synchronized_at,
            f"Synchronized {len(incoming)} source item(s).",
        )
        self._flush()
        return SynchronizationResult(connector_id, synchronized_at, visible)

    def imported_items(self, connector_id: str) -> tuple[ImportedConnectorItem, ...]:
        return tuple(
            item
            for (owner, _), item in sorted(self._items.items())
            if owner == connector_id and item.deletion_state == "active"
        )

    def health(self, connector_id: str) -> ConnectorHealth:
        self._connection(connector_id)
        return self._health[connector_id]

    def content_result(
        self, connector_id: str, external_id: str
    ) -> ExternalContentResult:
        item = self._items.get((connector_id, external_id))
        if item is None or item.deletion_state != "active":
            raise KeyError(
                f"Synchronized connector item is not available: "
                f"{connector_id}:{external_id}"
            )
        try:
            return self._content_results[(connector_id, external_id)]
        except KeyError as error:
            raise KeyError(
                f"Unknown synchronized connector item: {connector_id}:{external_id}"
            ) from error

    def revoke(self, connector_id: str, *, revoked_at: datetime) -> ConnectorConnection:
        connection = replace(self._connection(connector_id), status="revoked")
        self._connections[connector_id] = connection
        self._health[connector_id] = ConnectorHealth(
            connector_id, "revoked", revoked_at, "Future connector access is revoked."
        )
        self._flush()
        return connection

    def disconnect(
        self, connector_id: str, *, disconnected_at: datetime
    ) -> ConnectorConnection:
        connection = replace(self._connection(connector_id), status="disconnected")
        self._connections[connector_id] = connection
        self._health[connector_id] = ConnectorHealth(
            connector_id,
            "disconnected",
            disconnected_at,
            "The connector is disconnected and cannot be accessed.",
        )
        self._flush()
        return connection

    def remove_imported_data(
        self, connector_id: str, *, mode: Literal["suppress", "delete"]
    ) -> tuple[ImportedConnectorItem, ...]:
        self._connection(connector_id)
        changed: list[ImportedConnectorItem] = []
        for key, item in tuple(self._items.items()):
            if key[0] != connector_id:
                continue
            updated = replace(
                item,
                content="" if mode == "delete" else item.content,
                deletion_state="suppressed" if mode == "suppress" else "deleted",
            )
            self._items[key] = updated
            self._content_results.pop(key, None)
            changed.append(updated)
        self._flush()
        return tuple(changed)

    def _connection(self, connector_id: str) -> ConnectorConnection:
        try:
            return self._connections[connector_id]
        except KeyError as error:
            raise KeyError(f"Unknown connector: {connector_id}") from error

    def _flush(self) -> None:
        if self._path is None:
            return
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "connections": [
                    _connection_to_data(item) for item in self._connections.values()
                ],
                "items": [asdict(item) for item in self._items.values()],
                "health": [
                    _health_to_data(item) for item in self._health.values()
                ],
                "content_results": [
                    _content_result_to_data(connector_id, external_id, item)
                    for (connector_id, external_id), item in self._content_results.items()
                ],
            },
        )


def _connection_to_data(value: ConnectorConnection) -> dict[str, object]:
    return asdict(value)


def _connection_from_data(data: dict[str, object]) -> ConnectorConnection:
    return ConnectorConnection(
        id=str(data["id"]),
        identity=str(data["identity"]),
        granted_read_scope=_string_tuple(data["granted_read_scope"]),
        available_write_scope=_string_tuple(data["available_write_scope"]),
        granted_write_scope=_string_tuple(data.get("granted_write_scope", [])),
        status=data["status"],  # type: ignore[arg-type]
    )


def _imported_item_from_data(data: dict[str, object]) -> ImportedConnectorItem:
    return ImportedConnectorItem(
        connector_id=str(data["connector_id"]),
        external_id=str(data["external_id"]),
        content=str(data["content"]),
        provenance=str(data["provenance"]),
        deletion_state=data.get("deletion_state", "active"),  # type: ignore[arg-type]
    )


def _health_to_data(value: ConnectorHealth) -> dict[str, object]:
    return {
        **asdict(value),
        "checked_at": value.checked_at.isoformat() if value.checked_at else None,
    }


def _health_from_data(data: dict[str, object]) -> ConnectorHealth:
    return ConnectorHealth(
        connector_id=str(data["connector_id"]),
        status=data["status"],  # type: ignore[arg-type]
        checked_at=(
            datetime.fromisoformat(str(data["checked_at"]))
            if data.get("checked_at")
            else None
        ),
        detail=str(data["detail"]),
    )


def _content_result_to_data(
    connector_id: str,
    external_id: str,
    value: ExternalContentResult,
) -> dict[str, object]:
    return {
        "connector_id": connector_id,
        "external_id": external_id,
        "content_id": value.content_id,
        "source": value.source,
        "visible_content": value.visible_content,
        "prompt_layers": list(value.prompt_layers),
        "allowed_tools": list(value.allowed_tools),
        "permissions": list(value.permissions),
        "rejected_instructions": list(value.rejected_instructions),
    }


def _content_result_from_data(data: dict[str, object]) -> ExternalContentResult:
    return ExternalContentResult(
        content_id=str(data["content_id"]),
        source=str(data["source"]),
        visible_content=str(data["visible_content"]),
        prompt_layers=_string_tuple(data["prompt_layers"]),
        allowed_tools=_string_tuple(data["allowed_tools"]),
        permissions=_string_tuple(data["permissions"]),
        rejected_instructions=_string_tuple(data["rejected_instructions"]),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError("Connector storage contains an invalid string list.")
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class PreparedWrite:
    id: str
    connector_id: str
    action_type: str
    target: str
    trigger: str
    boundary: str
    payload: Mapping[str, str]
    prepared_at: datetime
    payload_digest: str
    exact_approval_id: str | None = None


@dataclass(frozen=True)
class StandingPermission:
    id: str
    connector_id: str
    action_type: str
    target: str
    trigger: str
    boundary: str
    granted_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    write_id: str
    prepared_payload: dict[str, str]
    reason: str
    status: Literal["approval_required"] = "approval_required"


@dataclass(frozen=True)
class WriteResult:
    status: Literal["success", "failed", "unknown"]
    external_id: str | None
    detail: str


@dataclass(frozen=True)
class WriteReceipt:
    write_id: str
    connector_id: str
    status: Literal["success", "failed", "unknown"]
    external_id: str | None
    detail: str
    attempts: int
    idempotency_key: str
    completed_at: datetime
    authorization_kind: Literal[
        "exact_approval", "standing_permission", "unknown"
    ]
    authorization_id: str
    authorization_scope: str


class WriteConnector(Protocol):
    def execute(self, payload: dict[str, str], idempotency_key: str) -> WriteResult: ...


class ExternalWriteService:
    """Prepare, approve, and execute external writes through transactional storage."""

    def __init__(self, path: Path | None = None) -> None:
        from reckoning.external_write_store import (
            ExternalWriteRepository,
            InMemoryExternalWriteRepository,
            SQLiteExternalWriteRepository,
        )

        self._repository: ExternalWriteRepository
        if path is None:
            self._repository = InMemoryExternalWriteRepository()
        else:
            self._repository = SQLiteExternalWriteRepository(path)

    def prepare(
        self,
        *,
        write_id: str,
        connector_id: str,
        action_type: str,
        target: str,
        trigger: str,
        boundary: str,
        payload: dict[str, str],
        prepared_at: datetime,
    ) -> PreparedWrite:
        from reckoning.external_write_store import payload_digest

        _validate_authority_scope(
            connector_id=connector_id,
            action_type=action_type,
            target=target,
            trigger=trigger,
            boundary=boundary,
        )
        if not write_id.strip():
            raise ValueError("An external write identifier is required.")
        if not payload:
            raise ValueError("An external write payload cannot be empty.")
        stable_payload = dict(sorted(payload.items()))
        prepared = PreparedWrite(
            write_id,
            connector_id,
            action_type,
            target,
            trigger,
            boundary,
            MappingProxyType(stable_payload),
            prepared_at,
            payload_digest(stable_payload),
        )
        return self._repository.prepare(prepared)

    def approve_exact(
        self, write_id: str, *, approval_id: str, approved_at: datetime
    ) -> PreparedWrite:
        if not approval_id.strip():
            raise ValueError("An exact approval identifier is required.")
        prepared = self._repository.get_prepared(write_id)
        self._repository.approve_exact(
            write_id,
            prepared.payload_digest,
            approval_id=approval_id,
            approved_at=approved_at,
        )
        return self._repository.get_prepared(write_id)

    def grant_standing_permission(
        self,
        *,
        permission_id: str,
        connector_id: str,
        action_type: str,
        target: str,
        trigger: str,
        boundary: str,
        granted_at: datetime,
    ) -> StandingPermission:
        if not permission_id.strip():
            raise ValueError("A standing permission identifier is required.")
        _validate_authority_scope(
            connector_id=connector_id,
            action_type=action_type,
            target=target,
            trigger=trigger,
            boundary=boundary,
        )
        permission = StandingPermission(
            permission_id,
            connector_id,
            action_type,
            target,
            trigger,
            boundary,
            granted_at,
        )
        return self._repository.grant_permission(permission)

    def revoke_standing_permission(
        self, permission_id: str, *, revoked_at: datetime
    ) -> StandingPermission:
        return self._repository.revoke_permission(
            permission_id, revoked_at=revoked_at
        )

    def pending_approval_ids(self) -> tuple[str, ...]:
        """Return prepared writes that still require an exact user approval."""
        return self._repository.pending_approval_ids()

    def list_prepared(self) -> tuple[PreparedWrite, ...]:
        return self._repository.list_prepared()

    def list_permissions(self) -> tuple[StandingPermission, ...]:
        return self._repository.list_permissions()

    def list_receipts(self) -> tuple[WriteReceipt, ...]:
        return self._repository.list_receipts()

    def execute(
        self,
        write_id: str,
        adapter: WriteConnector,
        *,
        completed_at: datetime,
        max_retries: int = 1,
        on_claimed: Callable[[str], None] | None = None,
    ) -> WriteReceipt | ApprovalRequest:
        from reckoning.external_write_store import (
            ExternalWriteAuthorityRevoked,
            authorization_scope,
        )

        existing = self._repository.get_receipt(write_id)
        if existing is not None:
            return existing
        prepared = self._repository.get_prepared(write_id)
        authorization = self._repository.authorization(prepared)
        if authorization is None:
            return ApprovalRequest(
                prepared.id,
                dict(prepared.payload),
                "No exact approval or matching standing permission.",
            )
        if max_retries < 0:
            raise ValueError("A write retry limit cannot be negative.")
        idempotency_key = f"reckoning-write:{prepared.id}:{prepared.payload_digest}"
        owner = _claim_owner()
        self._repository.claim_execution(
            prepared,
            owner=owner,
            idempotency_key=idempotency_key,
            authorization=authorization,
            claimed_at=completed_at,
        )
        if on_claimed is not None:
            on_claimed(owner)
        try:
            self._repository.dispatch_execution(prepared.id, owner)
        except ExternalWriteAuthorityRevoked:
            self._repository.abort_execution(
                prepared.id, owner, reason="authority revoked before dispatch"
            )
            return ApprovalRequest(
                prepared.id,
                dict(prepared.payload),
                "No exact approval or matching standing permission.",
            )
        result, attempts = self._invoke_adapter(
            adapter, prepared, idempotency_key, max_retries
        )
        receipt = WriteReceipt(
            write_id=prepared.id,
            connector_id=prepared.connector_id,
            status=result.status,
            external_id=result.external_id,
            detail=result.detail,
            attempts=attempts,
            idempotency_key=idempotency_key,
            completed_at=completed_at,
            authorization_kind=authorization.kind,
            authorization_id=authorization.authorization_id,
            authorization_scope=authorization_scope(prepared),
        )
        return self._repository.finalize_execution(prepared.id, owner, receipt)

    @staticmethod
    def _invoke_adapter(
        adapter: WriteConnector,
        prepared: PreparedWrite,
        idempotency_key: str,
        max_retries: int,
    ) -> tuple[WriteResult, int]:
        result = WriteResult("failed", None, "Write adapter was not called.")
        attempts = 0
        for attempts in range(1, max_retries + 2):
            try:
                result = adapter.execute(dict(prepared.payload), idempotency_key)
            except Exception as error:  # an ambiguous outcome is never retried
                return (
                    WriteResult("unknown", None, f"Write outcome unknown: {error}"),
                    attempts,
                )
            if result.status in ("success", "unknown"):
                break
        return result, attempts


def _prepared_write_to_data(value: PreparedWrite) -> dict[str, object]:
    return {
        **value.__dict__,
        "payload": dict(value.payload),
        "prepared_at": value.prepared_at.isoformat(),
    }


def _prepared_write_from_data(data: dict[str, object]) -> PreparedWrite:
    raw_payload = data["payload"]
    if not isinstance(raw_payload, dict):
        raise RuntimeError("prepared write payload must be an object")
    return PreparedWrite(
        id=str(data["id"]),
        connector_id=str(data["connector_id"]),
        action_type=str(data["action_type"]),
        target=str(data["target"]),
        trigger=str(data["trigger"]),
        boundary=str(data["boundary"]),
        payload=MappingProxyType(
            {str(key): str(value) for key, value in raw_payload.items()}
        ),
        prepared_at=datetime.fromisoformat(str(data["prepared_at"])),
        payload_digest=str(data["payload_digest"]),
        exact_approval_id=(
            str(data["exact_approval_id"])
            if data.get("exact_approval_id") is not None
            else None
        ),
    )


def _standing_permission_to_data(value: StandingPermission) -> dict[str, object]:
    return {
        **value.__dict__,
        "granted_at": value.granted_at.isoformat(),
        "revoked_at": value.revoked_at.isoformat() if value.revoked_at else None,
    }


def _standing_permission_from_data(data: dict[str, object]) -> StandingPermission:
    return StandingPermission(
        id=str(data["id"]),
        connector_id=str(data["connector_id"]),
        action_type=str(data["action_type"]),
        target=str(data["target"]),
        trigger=str(data["trigger"]),
        boundary=str(data["boundary"]),
        granted_at=datetime.fromisoformat(str(data["granted_at"])),
        revoked_at=(
            datetime.fromisoformat(str(data["revoked_at"]))
            if data.get("revoked_at")
            else None
        ),
    )


def _write_receipt_to_data(value: WriteReceipt) -> dict[str, object]:
    return {
        **value.__dict__,
        "completed_at": value.completed_at.isoformat(),
    }


def _write_receipt_from_data(data: dict[str, object]) -> WriteReceipt:
    return WriteReceipt(
        write_id=str(data["write_id"]),
        connector_id=str(data["connector_id"]),
        status=data["status"],  # type: ignore[arg-type]
        external_id=(str(data["external_id"]) if data.get("external_id") else None),
        detail=str(data["detail"]),
        attempts=int(str(data["attempts"])),
        idempotency_key=str(data["idempotency_key"]),
        completed_at=datetime.fromisoformat(str(data["completed_at"])),
        authorization_kind=data.get("authorization_kind", "unknown"),  # type: ignore[arg-type]
        authorization_id=str(data.get("authorization_id", "unknown")),
        authorization_scope=str(data.get("authorization_scope", "")),
    )


def _claim_owner() -> str:
    return f"{os.getpid()}-{uuid4().hex}"


def _validate_authority_scope(
    *,
    connector_id: str,
    action_type: str,
    target: str,
    trigger: str,
    boundary: str,
) -> None:
    for label, value in (
        ("connector", connector_id),
        ("action type", action_type),
        ("target", target),
        ("trigger", trigger),
        ("boundary", boundary),
    ):
        if not value.strip():
            raise ValueError(f"An external-write {label} is required.")
