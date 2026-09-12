from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Literal, Protocol

from reckoning.connectors import (
    PreparedWrite,
    StandingPermission,
    WriteReceipt,
)
from reckoning.root_database import (
    ROOT_DATABASE_FILENAME,
    connect_database,
    ensure_root_schema,
    logical_root_for,
    metadata,
    set_metadata,
    table_exists,
)
from reckoning.store_migration import (
    finalize_legacy_authority,
    preserve_legacy_rollback,
    write_legacy_authority,
)


EXTERNAL_WRITE_SCHEMA_VERSION = "1"

_CLAIM_STATES = ("claimed", "dispatched", "finalized", "aborted", "unknown")
_AUTHORIZATION_KINDS = ("exact_approval", "standing_permission", "unknown")


class ExternalWriteConflict(RuntimeError):
    """A transactional external-write precondition no longer matches."""


class ExternalWriteClaimConflict(ExternalWriteConflict):
    """Another worker owns the active execution claim for this write."""


class ExternalWriteAuthorityRevoked(ExternalWriteConflict):
    """The authority recorded at claim time is no longer active."""


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    write_id: str
    payload_digest: str
    approved_at: datetime
    state: Literal["active", "revoked"] = "active"


@dataclass(frozen=True)
class WriteAuthorization:
    kind: Literal["exact_approval", "standing_permission"]
    authorization_id: str
    scope: str


@dataclass(frozen=True)
class ExecutionClaim:
    write_id: str
    payload_digest: str
    claim_owner: str
    claim_state: str
    idempotency_key: str
    authorization_kind: str
    authorization_id: str
    authorization_scope: str
    attempts: int
    claimed_at: datetime
    revision: int


class ExternalWriteRepository(Protocol):
    def prepare(self, prepared: PreparedWrite) -> PreparedWrite: ...

    def get_prepared(self, write_id: str) -> PreparedWrite: ...

    def list_prepared(self) -> tuple[PreparedWrite, ...]: ...

    def approve_exact(
        self,
        write_id: str,
        payload_digest: str,
        *,
        approval_id: str,
        approved_at: datetime,
    ) -> ApprovalRecord: ...

    def list_approvals(self) -> tuple[ApprovalRecord, ...]: ...

    def authorization(self, prepared: PreparedWrite) -> WriteAuthorization | None: ...

    def pending_approval_ids(self) -> tuple[str, ...]: ...

    def grant_permission(
        self, permission: StandingPermission
    ) -> StandingPermission: ...

    def get_permission(self, permission_id: str) -> StandingPermission: ...

    def list_permissions(self) -> tuple[StandingPermission, ...]: ...

    def revoke_permission(
        self, permission_id: str, *, revoked_at: datetime
    ) -> StandingPermission: ...

    def get_receipt(self, write_id: str) -> WriteReceipt | None: ...

    def list_receipts(self) -> tuple[WriteReceipt, ...]: ...

    def claim_execution(
        self,
        prepared: PreparedWrite,
        *,
        owner: str,
        idempotency_key: str,
        authorization: WriteAuthorization,
        claimed_at: datetime,
    ) -> ExecutionClaim: ...

    def dispatch_execution(self, write_id: str, owner: str) -> ExecutionClaim: ...

    def abort_execution(self, write_id: str, owner: str, *, reason: str) -> None: ...

    def recover_execution(
        self,
        write_id: str,
        *,
        expected_revision: int,
        new_owner: str,
        reason: str,
        recovered_at: datetime,
    ) -> ExecutionClaim: ...

    def finalize_execution(
        self, write_id: str, owner: str, receipt: WriteReceipt
    ) -> WriteReceipt: ...

    def execution(self, write_id: str) -> ExecutionClaim | None: ...


def authorization_scope(prepared: PreparedWrite) -> str:
    return "|".join(
        (
            prepared.connector_id,
            prepared.action_type,
            prepared.target,
            prepared.trigger,
            prepared.boundary,
        )
    )


def payload_digest(payload: dict[str, str]) -> str:
    stable_payload = dict(sorted(payload.items()))
    return sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class InMemoryExternalWriteRepository:
    """Reference implementation used when no durable path is configured."""

    def __init__(self) -> None:
        self._writes: dict[str, PreparedWrite] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._permissions: dict[str, StandingPermission] = {}
        self._receipts: dict[str, WriteReceipt] = {}
        self._executions: dict[str, ExecutionClaim] = {}

    def prepare(self, prepared: PreparedWrite) -> PreparedWrite:
        existing = self._writes.get(prepared.id)
        if existing is not None:
            if not _same_prepared(existing, prepared):
                raise ExternalWriteConflict(
                    f"Prepared write {prepared.id} already exists with other content."
                )
            return _with_approval(existing, self._active_approval_id(prepared.id))
        self._writes[prepared.id] = prepared
        return prepared

    def get_prepared(self, write_id: str) -> PreparedWrite:
        try:
            prepared = self._writes[write_id]
        except KeyError as error:
            raise KeyError(f"Unknown prepared write: {write_id}") from error
        return _with_approval(prepared, self._active_approval_id(write_id))

    def list_prepared(self) -> tuple[PreparedWrite, ...]:
        return tuple(
            _with_approval(item, self._active_approval_id(item.id))
            for item in self._writes.values()
        )

    def approve_exact(
        self,
        write_id: str,
        payload_digest: str,
        *,
        approval_id: str,
        approved_at: datetime,
    ) -> ApprovalRecord:
        self.get_prepared(write_id)
        existing = self._approvals.get(approval_id)
        if existing is not None:
            if (
                existing.write_id == write_id
                and existing.payload_digest == payload_digest
            ):
                return existing
            raise ExternalWriteConflict(
                f"Exact approval {approval_id} already binds other content."
            )
        for record in self._approvals.values():
            if record.write_id == write_id and record.state == "active":
                if record.payload_digest == payload_digest:
                    return record
                raise ExternalWriteConflict(
                    f"Prepared write {write_id} already has an exact approval."
                )
        record = ApprovalRecord(
            approval_id, write_id, payload_digest, approved_at, "active"
        )
        self._approvals[approval_id] = record
        return record

    def list_approvals(self) -> tuple[ApprovalRecord, ...]:
        return tuple(self._approvals.values())

    def authorization(self, prepared: PreparedWrite) -> WriteAuthorization | None:
        approval_id = self._active_approval_id(prepared.id)
        if approval_id is not None:
            return WriteAuthorization(
                "exact_approval", approval_id, authorization_scope(prepared)
            )
        for permission in self._permissions.values():
            if _permission_matches(permission, prepared):
                return WriteAuthorization(
                    "standing_permission", permission.id, authorization_scope(prepared)
                )
        return None

    def pending_approval_ids(self) -> tuple[str, ...]:
        return tuple(
            prepared.id
            for prepared in self._writes.values()
            if prepared.id not in self._receipts
            and self.authorization(prepared) is None
        )

    def grant_permission(
        self, permission: StandingPermission
    ) -> StandingPermission:
        if permission.id in self._permissions:
            raise ExternalWriteConflict(
                f"Standing permission {permission.id} already exists."
            )
        self._permissions[permission.id] = permission
        return permission

    def get_permission(self, permission_id: str) -> StandingPermission:
        try:
            return self._permissions[permission_id]
        except KeyError as error:
            raise KeyError(f"Unknown standing permission: {permission_id}") from error

    def list_permissions(self) -> tuple[StandingPermission, ...]:
        return tuple(self._permissions.values())

    def revoke_permission(
        self, permission_id: str, *, revoked_at: datetime
    ) -> StandingPermission:
        permission = self.get_permission(permission_id)
        if permission.revoked_at is not None:
            return permission
        revoked = StandingPermission(
            id=permission.id,
            connector_id=permission.connector_id,
            action_type=permission.action_type,
            target=permission.target,
            trigger=permission.trigger,
            boundary=permission.boundary,
            granted_at=permission.granted_at,
            revoked_at=revoked_at,
        )
        self._permissions[permission_id] = revoked
        return revoked

    def get_receipt(self, write_id: str) -> WriteReceipt | None:
        return self._receipts.get(write_id)

    def list_receipts(self) -> tuple[WriteReceipt, ...]:
        return tuple(self._receipts.values())

    def claim_execution(
        self,
        prepared: PreparedWrite,
        *,
        owner: str,
        idempotency_key: str,
        authorization: WriteAuthorization,
        claimed_at: datetime,
    ) -> ExecutionClaim:
        existing = self._executions.get(prepared.id)
        if existing is not None:
            if existing.payload_digest != prepared.payload_digest:
                raise ExternalWriteConflict(
                    f"Prepared write {prepared.id} changed after it was claimed."
                )
            if existing.claim_state == "finalized":
                raise ExternalWriteClaimConflict(
                    f"Prepared write {prepared.id} is already finalized."
                )
            if existing.claim_state == "aborted":
                pass
            elif existing.claim_owner != owner:
                raise ExternalWriteClaimConflict(
                    f"Prepared write {prepared.id} is claimed by another worker."
                )
            else:
                return existing
        claim = ExecutionClaim(
            prepared.id,
            prepared.payload_digest,
            owner,
            "claimed",
            idempotency_key,
            authorization.kind,
            authorization.authorization_id,
            authorization.scope,
            0,
            claimed_at,
            1,
        )
        self._executions[prepared.id] = claim
        return claim

    def dispatch_execution(self, write_id: str, owner: str) -> ExecutionClaim:
        claim = self._owned_claim(write_id, owner)
        prepared = self.get_prepared(write_id)
        if self.authorization(prepared) is None:
            raise ExternalWriteAuthorityRevoked(
                f"Authority for prepared write {write_id} was revoked before dispatch."
            )
        if claim.claim_state not in ("claimed",):
            raise ExternalWriteClaimConflict(
                f"Prepared write {write_id} is not dispatchable from {claim.claim_state}."
            )
        dispatched = _replace_claim(claim, claim_state="dispatched", revision=claim.revision + 1)
        self._executions[write_id] = dispatched
        return dispatched

    def abort_execution(self, write_id: str, owner: str, *, reason: str) -> None:
        claim = self._owned_claim(write_id, owner)
        self._executions[write_id] = _replace_claim(
            claim, claim_state="aborted", revision=claim.revision + 1
        )
        del reason

    def recover_execution(
        self,
        write_id: str,
        *,
        expected_revision: int,
        new_owner: str,
        reason: str,
        recovered_at: datetime,
    ) -> ExecutionClaim:
        try:
            claim = self._executions[write_id]
        except KeyError as error:
            raise KeyError(f"Unknown execution claim: {write_id}") from error
        if claim.revision != expected_revision:
            raise ExternalWriteConflict(
                f"Execution claim for {write_id} changed from revision "
                f"{expected_revision} to {claim.revision}."
            )
        if claim.claim_state == "finalized":
            raise ExternalWriteConflict(
                f"Execution claim for {write_id} is already finalized."
            )
        recovered = ExecutionClaim(
            claim.write_id,
            claim.payload_digest,
            new_owner,
            "claimed",
            claim.idempotency_key,
            claim.authorization_kind,
            claim.authorization_id,
            claim.authorization_scope,
            claim.attempts,
            recovered_at,
            claim.revision + 1,
        )
        self._executions[write_id] = recovered
        del reason
        return recovered

    def finalize_execution(
        self, write_id: str, owner: str, receipt: WriteReceipt
    ) -> WriteReceipt:
        claim = self._owned_claim(write_id, owner)
        self._executions[write_id] = _replace_claim(
            claim,
            claim_state="finalized",
            attempts=receipt.attempts,
            revision=claim.revision + 1,
        )
        self._receipts[write_id] = receipt
        return receipt

    def execution(self, write_id: str) -> ExecutionClaim | None:
        return self._executions.get(write_id)

    def _active_approval_id(self, write_id: str) -> str | None:
        for record in self._approvals.values():
            if record.write_id == write_id and record.state == "active":
                return record.approval_id
        return None

    def _owned_claim(self, write_id: str, owner: str) -> ExecutionClaim:
        try:
            claim = self._executions[write_id]
        except KeyError as error:
            raise KeyError(f"Unknown execution claim: {write_id}") from error
        if claim.claim_owner != owner:
            raise ExternalWriteClaimConflict(
                f"Prepared write {write_id} is owned by another worker."
            )
        return claim


class SQLiteExternalWriteRepository:
    """Persist external-write authority transactionally within one placement root."""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path
        self._root = logical_root_for(legacy_path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize_or_migrate()

    @property
    def database_path(self) -> Path:
        return self._path

    def prepare(self, prepared: PreparedWrite) -> PreparedWrite:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = _select_prepared(connection, prepared.id)
                if existing is not None:
                    if not _same_prepared(existing, prepared):
                        raise ExternalWriteConflict(
                            f"Prepared write {prepared.id} already exists with "
                            "other content."
                        )
                    result = _with_approval(
                        existing, _active_approval_id(connection, prepared.id)
                    )
                else:
                    _insert_prepared(connection, prepared)
                    result = prepared
                connection.commit()
                return result
            except BaseException:
                connection.rollback()
                raise

    def get_prepared(self, write_id: str) -> PreparedWrite:
        with closing(connect_database(self._path)) as connection:
            prepared = _select_prepared(connection, write_id)
            if prepared is None:
                raise KeyError(f"Unknown prepared write: {write_id}")
            return _with_approval(
                prepared, _active_approval_id(connection, write_id)
            )

    def list_prepared(self) -> tuple[PreparedWrite, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT write_id FROM external_prepared_writes ORDER BY rowid"
            ).fetchall()
            return tuple(
                _with_approval(
                    _select_prepared(connection, str(row[0])),
                    _active_approval_id(connection, str(row[0])),
                )
                for row in rows
            )

    def approve_exact(
        self,
        write_id: str,
        payload_digest: str,
        *,
        approval_id: str,
        approved_at: datetime,
    ) -> ApprovalRecord:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prepared = _select_prepared(connection, write_id)
                if prepared is None:
                    raise KeyError(f"Unknown prepared write: {write_id}")
                if prepared.payload_digest != payload_digest:
                    raise ExternalWriteConflict(
                        f"Prepared write {write_id} payload no longer matches."
                    )
                existing = _select_approval(connection, approval_id)
                if existing is not None:
                    if (
                        existing.write_id == write_id
                        and existing.payload_digest == payload_digest
                    ):
                        connection.commit()
                        return existing
                    raise ExternalWriteConflict(
                        f"Exact approval {approval_id} already binds other content."
                    )
                current = _active_approval_id(connection, write_id)
                if current is not None:
                    raise ExternalWriteConflict(
                        f"Prepared write {write_id} already has an exact approval."
                    )
                record = ApprovalRecord(
                    approval_id, write_id, payload_digest, approved_at, "active"
                )
                _insert_approval(connection, record)
                connection.commit()
                return record
            except BaseException:
                connection.rollback()
                raise

    def list_approvals(self) -> tuple[ApprovalRecord, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT approval_id FROM external_write_approvals ORDER BY rowid"
            ).fetchall()
            return tuple(
                _select_approval(connection, str(row[0])) for row in rows
            )

    def authorization(self, prepared: PreparedWrite) -> WriteAuthorization | None:
        with closing(connect_database(self._path)) as connection:
            return _authorization(connection, prepared)

    def pending_approval_ids(self) -> tuple[str, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT write_id FROM external_prepared_writes AS w
                WHERE NOT EXISTS (
                    SELECT 1 FROM external_write_receipts AS r
                    WHERE r.write_id = w.write_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM external_write_approvals AS a
                    WHERE a.write_id = w.write_id
                      AND a.payload_digest = w.payload_digest
                      AND a.state = 'active'
                )
                AND NOT EXISTS (
                    SELECT 1 FROM external_standing_permissions AS p
                    WHERE p.state = 'active'
                      AND p.connector_id = w.connector_id
                      AND p.action_type = w.action_type
                      AND p.target = w.target
                      AND p.trigger = w.trigger
                      AND p.boundary = w.boundary
                )
                ORDER BY w.rowid
                """
            ).fetchall()
            return tuple(str(row[0]) for row in rows)

    def grant_permission(
        self, permission: StandingPermission
    ) -> StandingPermission:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    connection.execute(
                        "SELECT 1 FROM external_standing_permissions "
                        "WHERE permission_id = ?",
                        (permission.id,),
                    ).fetchone()
                    is not None
                ):
                    raise ExternalWriteConflict(
                        f"Standing permission {permission.id} already exists."
                    )
                _insert_permission(connection, permission)
                connection.commit()
                return permission
            except BaseException:
                connection.rollback()
                raise

    def get_permission(self, permission_id: str) -> StandingPermission:
        with closing(connect_database(self._path)) as connection:
            permission = _select_permission(connection, permission_id)
            if permission is None:
                raise KeyError(f"Unknown standing permission: {permission_id}")
            return permission

    def list_permissions(self) -> tuple[StandingPermission, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT permission_id FROM external_standing_permissions "
                "ORDER BY rowid"
            ).fetchall()
            return tuple(
                _select_permission(connection, str(row[0])) for row in rows
            )

    def revoke_permission(
        self, permission_id: str, *, revoked_at: datetime
    ) -> StandingPermission:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                permission = _select_permission(connection, permission_id)
                if permission is None:
                    raise KeyError(f"Unknown standing permission: {permission_id}")
                if permission.revoked_at is None:
                    connection.execute(
                        """
                        UPDATE external_standing_permissions
                        SET state = 'revoked', revoked_at = ?, revision = revision + 1
                        WHERE permission_id = ? AND state = 'active'
                        """,
                        (revoked_at.isoformat(), permission_id),
                    )
                    permission = _select_permission(connection, permission_id)
                connection.commit()
                assert permission is not None
                return permission
            except BaseException:
                connection.rollback()
                raise

    def get_receipt(self, write_id: str) -> WriteReceipt | None:
        with closing(connect_database(self._path)) as connection:
            return _select_receipt(connection, write_id)

    def list_receipts(self) -> tuple[WriteReceipt, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                "SELECT write_id FROM external_write_receipts ORDER BY rowid"
            ).fetchall()
            return tuple(
                _select_receipt(connection, str(row[0])) for row in rows
            )

    def claim_execution(
        self,
        prepared: PreparedWrite,
        *,
        owner: str,
        idempotency_key: str,
        authorization: WriteAuthorization,
        claimed_at: datetime,
    ) -> ExecutionClaim:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = _select_execution(connection, prepared.id)
                if existing is not None:
                    if existing.payload_digest != prepared.payload_digest:
                        raise ExternalWriteConflict(
                            f"Prepared write {prepared.id} changed after it was "
                            "claimed."
                        )
                    if existing.claim_state == "finalized":
                        raise ExternalWriteClaimConflict(
                            f"Prepared write {prepared.id} is already finalized."
                        )
                    if existing.claim_state != "aborted" and (
                        existing.claim_owner != owner
                    ):
                        raise ExternalWriteClaimConflict(
                            f"Prepared write {prepared.id} is claimed by another "
                            "worker."
                        )
                    if existing.claim_owner == owner and existing.claim_state in (
                        "claimed",
                        "dispatched",
                    ):
                        connection.commit()
                        return existing
                    connection.execute(
                        """
                        UPDATE external_write_executions
                        SET claim_owner = ?, claim_state = 'claimed',
                            idempotency_key = ?, authorization_kind = ?,
                            authorization_id = ?, authorization_scope = ?,
                            claimed_at = ?, revision = revision + 1
                        WHERE write_id = ?
                        """,
                        (
                            owner,
                            idempotency_key,
                            authorization.kind,
                            authorization.authorization_id,
                            authorization.scope,
                            claimed_at.isoformat(),
                            prepared.id,
                        ),
                    )
                    claim = _select_execution(connection, prepared.id)
                    connection.commit()
                    assert claim is not None
                    return claim
                claim = ExecutionClaim(
                    prepared.id,
                    prepared.payload_digest,
                    owner,
                    "claimed",
                    idempotency_key,
                    authorization.kind,
                    authorization.authorization_id,
                    authorization.scope,
                    0,
                    claimed_at,
                    1,
                )
                _insert_execution(connection, claim)
                connection.commit()
                return claim
            except BaseException:
                connection.rollback()
                raise

    def dispatch_execution(self, write_id: str, owner: str) -> ExecutionClaim:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                claim = _owned_execution(connection, write_id, owner)
                prepared = _select_prepared(connection, write_id)
                if prepared is None:
                    raise KeyError(f"Unknown prepared write: {write_id}")
                if _authorization(connection, prepared) is None:
                    connection.execute(
                        """
                        UPDATE external_write_executions
                        SET claim_state = 'aborted', revision = revision + 1
                        WHERE write_id = ?
                        """,
                        (write_id,),
                    )
                    connection.commit()
                    raise ExternalWriteAuthorityRevoked(
                        f"Authority for prepared write {write_id} was revoked "
                        "before dispatch."
                    )
                if claim.claim_state != "claimed":
                    raise ExternalWriteClaimConflict(
                        f"Prepared write {write_id} is not dispatchable from "
                        f"{claim.claim_state}."
                    )
                connection.execute(
                    """
                    UPDATE external_write_executions
                    SET claim_state = 'dispatched', revision = revision + 1
                    WHERE write_id = ? AND claim_owner = ?
                    """,
                    (write_id, owner),
                )
                dispatched = _select_execution(connection, write_id)
                connection.commit()
                assert dispatched is not None
                return dispatched
            except ExternalWriteAuthorityRevoked:
                raise
            except BaseException:
                connection.rollback()
                raise

    def abort_execution(self, write_id: str, owner: str, *, reason: str) -> None:
        del reason
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _owned_execution(connection, write_id, owner)
                connection.execute(
                    """
                    UPDATE external_write_executions
                    SET claim_state = 'aborted', revision = revision + 1
                    WHERE write_id = ? AND claim_owner = ?
                    """,
                    (write_id, owner),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def recover_execution(
        self,
        write_id: str,
        *,
        expected_revision: int,
        new_owner: str,
        reason: str,
        recovered_at: datetime,
    ) -> ExecutionClaim:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                claim = _select_execution(connection, write_id)
                if claim is None:
                    raise KeyError(f"Unknown execution claim: {write_id}")
                if claim.revision != expected_revision:
                    raise ExternalWriteConflict(
                        f"Execution claim for {write_id} changed from revision "
                        f"{expected_revision} to {claim.revision}."
                    )
                if claim.claim_state == "finalized":
                    raise ExternalWriteConflict(
                        f"Execution claim for {write_id} is already finalized."
                    )
                connection.execute(
                    """
                    UPDATE external_write_executions
                    SET claim_owner = ?, claim_state = 'claimed',
                        revision = revision + 1, recovery_owner = ?,
                        recovery_reason = ?, recovered_at = ?
                    WHERE write_id = ?
                    """,
                    (
                        new_owner,
                        claim.claim_owner,
                        reason,
                        recovered_at.isoformat(),
                        write_id,
                    ),
                )
                recovered = _select_execution(connection, write_id)
                connection.commit()
                assert recovered is not None
                return recovered
            except BaseException:
                connection.rollback()
                raise

    def finalize_execution(
        self, write_id: str, owner: str, receipt: WriteReceipt
    ) -> WriteReceipt:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                claim = _owned_execution(connection, write_id, owner)
                if claim.claim_state not in ("dispatched", "claimed"):
                    raise ExternalWriteClaimConflict(
                        f"Prepared write {write_id} is not finalizable from "
                        f"{claim.claim_state}."
                    )
                connection.execute(
                    """
                    UPDATE external_write_executions
                    SET claim_state = 'finalized', attempts = ?,
                        revision = revision + 1
                    WHERE write_id = ? AND claim_owner = ?
                    """,
                    (receipt.attempts, write_id, owner),
                )
                _insert_receipt(connection, receipt)
                connection.commit()
                return receipt
            except BaseException:
                connection.rollback()
                raise

    def execution(self, write_id: str) -> ExecutionClaim | None:
        with closing(connect_database(self._path)) as connection:
            return _select_execution(connection, write_id)

    def _initialize_or_migrate(self) -> None:
        if _store_ready(self._path):
            finalize_legacy_authority(
                self._path,
                self._legacy_path,
                load_legacy=_load_legacy_authority,
                digest_key="external_writes_legacy_sha256",
                label="external-write",
            )
            return

        legacy_data: dict[str, Any] | None = None
        legacy: _LegacyExternalWrites | None = None
        legacy_digest: str | None = None
        if self._legacy_path.exists():
            try:
                legacy_data, legacy, legacy_digest = _load_legacy(self._legacy_path)
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                if _store_ready(self._path):
                    return
                raise RuntimeError("Invalid legacy external-write storage.") from None
            preserve_legacy_rollback(self._legacy_path, legacy_data)
            write_legacy_authority(self._legacy_path, state="migration-pending")

        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_version = metadata(
                    connection, "external_writes_schema_version"
                )
                if current_version is not None and not _tables_exist(connection):
                    raise RuntimeError(
                        "External-write schema authority has incomplete tables."
                    )
                _create_schema(connection)
                current_version = metadata(
                    connection, "external_writes_schema_version"
                )
                if current_version not in (None, EXTERNAL_WRITE_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported external-write storage schema.")
                if current_version is None:
                    if _record_count(connection):
                        raise RuntimeError(
                            "External-write storage has records without schema "
                            "authority."
                        )
                    if legacy is not None:
                        _import_legacy(connection, legacy)
                    set_metadata(
                        connection,
                        "external_writes_schema_version",
                        EXTERNAL_WRITE_SCHEMA_VERSION,
                    )
                    set_metadata(
                        connection,
                        "external_writes_legacy_sha256",
                        legacy_digest or "not-required",
                    )
                    _validate_migration(connection, legacy)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        write_legacy_authority(self._legacy_path, state="sqlite-authoritative")


def validate_external_write_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, "external_writes_schema_version")
    has_tables = _tables_exist(connection)
    if version not in (None, EXTERNAL_WRITE_SCHEMA_VERSION):
        raise RuntimeError("Unsupported external-write storage schema.")
    if version is None and _any_table_exists(connection):
        raise RuntimeError("Incomplete external-write storage schema.")
    if version is not None and not has_tables:
        raise RuntimeError("Incomplete external-write storage schema.")


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS external_prepared_writes (
            write_id TEXT PRIMARY KEY,
            payload_digest TEXT NOT NULL,
            connector_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target TEXT NOT NULL,
            trigger TEXT NOT NULL,
            boundary TEXT NOT NULL,
            payload TEXT NOT NULL,
            prepared_at TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS external_write_approvals (
            approval_id TEXT PRIMARY KEY,
            write_id TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
            revision INTEGER NOT NULL,
            UNIQUE (write_id, payload_digest)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS external_standing_permissions (
            permission_id TEXT PRIMARY KEY,
            connector_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target TEXT NOT NULL,
            trigger TEXT NOT NULL,
            boundary TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
            revoked_at TEXT,
            revision INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS external_write_executions (
            write_id TEXT PRIMARY KEY,
            payload_digest TEXT NOT NULL,
            claim_owner TEXT NOT NULL,
            claim_state TEXT NOT NULL CHECK (
                claim_state IN (
                    'claimed', 'dispatched', 'finalized', 'aborted', 'unknown'
                )
            ),
            idempotency_key TEXT NOT NULL,
            authorization_kind TEXT NOT NULL,
            authorization_id TEXT NOT NULL,
            authorization_scope TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            claimed_at TEXT NOT NULL,
            revision INTEGER NOT NULL,
            recovery_owner TEXT,
            recovery_reason TEXT,
            recovered_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS external_write_receipts (
            write_id TEXT PRIMARY KEY,
            payload_digest TEXT NOT NULL,
            connector_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'unknown')),
            external_id TEXT,
            detail TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            authorization_kind TEXT NOT NULL,
            authorization_id TEXT NOT NULL,
            authorization_scope TEXT NOT NULL,
            revision INTEGER NOT NULL
        )
        """
    )


def _tables_exist(connection: sqlite3.Connection) -> bool:
    return all(
        table_exists(connection, table)
        for table in (
            "external_prepared_writes",
            "external_write_approvals",
            "external_standing_permissions",
            "external_write_executions",
            "external_write_receipts",
        )
    )


def _any_table_exists(connection: sqlite3.Connection) -> bool:
    return any(
        table_exists(connection, table)
        for table in (
            "external_prepared_writes",
            "external_write_approvals",
            "external_standing_permissions",
            "external_write_executions",
            "external_write_receipts",
        )
    )


def _record_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM external_prepared_writes) +
                (SELECT COUNT(*) FROM external_write_approvals) +
                (SELECT COUNT(*) FROM external_standing_permissions) +
                (SELECT COUNT(*) FROM external_write_executions) +
                (SELECT COUNT(*) FROM external_write_receipts)
            """
        ).fetchone()[0]
    )


def _store_ready(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return (
                metadata(connection, "external_writes_schema_version")
                == EXTERNAL_WRITE_SCHEMA_VERSION
                and _tables_exist(connection)
            )
        finally:
            connection.close()
    except sqlite3.Error:
        return False


def _same_prepared(first: PreparedWrite, second: PreparedWrite) -> bool:
    return (
        first.payload_digest == second.payload_digest
        and first.connector_id == second.connector_id
        and first.action_type == second.action_type
        and first.target == second.target
        and first.trigger == second.trigger
        and first.boundary == second.boundary
    )


def _with_approval(
    prepared: PreparedWrite, approval_id: str | None
) -> PreparedWrite:
    return PreparedWrite(
        prepared.id,
        prepared.connector_id,
        prepared.action_type,
        prepared.target,
        prepared.trigger,
        prepared.boundary,
        prepared.payload,
        prepared.prepared_at,
        prepared.payload_digest,
        approval_id,
    )


def _replace_claim(claim: ExecutionClaim, **changes: object) -> ExecutionClaim:
    values: dict[str, object] = {
        "write_id": claim.write_id,
        "payload_digest": claim.payload_digest,
        "claim_owner": claim.claim_owner,
        "claim_state": claim.claim_state,
        "idempotency_key": claim.idempotency_key,
        "authorization_kind": claim.authorization_kind,
        "authorization_id": claim.authorization_id,
        "authorization_scope": claim.authorization_scope,
        "attempts": claim.attempts,
        "claimed_at": claim.claimed_at,
        "revision": claim.revision,
    }
    values.update(changes)
    return ExecutionClaim(**values)  # type: ignore[arg-type]


def _permission_matches(
    permission: StandingPermission, prepared: PreparedWrite
) -> bool:
    return (
        permission.revoked_at is None
        and permission.connector_id == prepared.connector_id
        and permission.action_type == prepared.action_type
        and permission.target == prepared.target
        and permission.trigger == prepared.trigger
        and permission.boundary == prepared.boundary
    )


def _authorization(
    connection: sqlite3.Connection, prepared: PreparedWrite
) -> WriteAuthorization | None:
    approval_id = _active_approval_id(connection, prepared.id)
    if approval_id is not None:
        return WriteAuthorization(
            "exact_approval", approval_id, authorization_scope(prepared)
        )
    row = connection.execute(
        """
        SELECT permission_id FROM external_standing_permissions
        WHERE state = 'active'
          AND connector_id = ? AND action_type = ? AND target = ?
          AND trigger = ? AND boundary = ?
        ORDER BY rowid LIMIT 1
        """,
        (
            prepared.connector_id,
            prepared.action_type,
            prepared.target,
            prepared.trigger,
            prepared.boundary,
        ),
    ).fetchone()
    if row is None:
        return None
    return WriteAuthorization(
        "standing_permission", str(row[0]), authorization_scope(prepared)
    )


def _active_approval_id(
    connection: sqlite3.Connection, write_id: str
) -> str | None:
    row = connection.execute(
        """
        SELECT approval_id FROM external_write_approvals
        WHERE write_id = ? AND state = 'active'
        ORDER BY rowid LIMIT 1
        """,
        (write_id,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _select_prepared(
    connection: sqlite3.Connection, write_id: str
) -> PreparedWrite | None:
    row = connection.execute(
        """
        SELECT write_id, payload_digest, connector_id, action_type, target,
               trigger, boundary, payload, prepared_at
        FROM external_prepared_writes WHERE write_id = ?
        """,
        (write_id,),
    ).fetchone()
    if row is None:
        return None
    return _prepared_from_row(row)


def _prepared_from_row(row: sqlite3.Row | tuple[Any, ...]) -> PreparedWrite:
    raw_payload = json.loads(str(row[7]))
    if not isinstance(raw_payload, dict):
        raise RuntimeError("Stored prepared-write payload is invalid.")
    return PreparedWrite(
        id=str(row[0]),
        connector_id=str(row[2]),
        action_type=str(row[3]),
        target=str(row[4]),
        trigger=str(row[5]),
        boundary=str(row[6]),
        payload=MappingProxyType(
            {str(key): str(value) for key, value in raw_payload.items()}
        ),
        prepared_at=datetime.fromisoformat(str(row[8])),
        payload_digest=str(row[1]),
    )


def _insert_prepared(
    connection: sqlite3.Connection, prepared: PreparedWrite
) -> None:
    connection.execute(
        """
        INSERT INTO external_prepared_writes (
            write_id, payload_digest, connector_id, action_type, target,
            trigger, boundary, payload, prepared_at, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            prepared.id,
            prepared.payload_digest,
            prepared.connector_id,
            prepared.action_type,
            prepared.target,
            prepared.trigger,
            prepared.boundary,
            json.dumps(dict(prepared.payload), sort_keys=True),
            prepared.prepared_at.isoformat(),
        ),
    )


def _select_approval(
    connection: sqlite3.Connection, approval_id: str
) -> ApprovalRecord | None:
    row = connection.execute(
        """
        SELECT approval_id, write_id, payload_digest, approved_at, state
        FROM external_write_approvals WHERE approval_id = ?
        """,
        (approval_id,),
    ).fetchone()
    if row is None:
        return None
    return ApprovalRecord(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        datetime.fromisoformat(str(row[3])),
        row[4],
    )


def _insert_approval(
    connection: sqlite3.Connection, record: ApprovalRecord
) -> None:
    connection.execute(
        """
        INSERT INTO external_write_approvals (
            approval_id, write_id, payload_digest, approved_at, state, revision
        ) VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            record.approval_id,
            record.write_id,
            record.payload_digest,
            record.approved_at.isoformat(),
            record.state,
        ),
    )


def _select_permission(
    connection: sqlite3.Connection, permission_id: str
) -> StandingPermission | None:
    row = connection.execute(
        """
        SELECT permission_id, connector_id, action_type, target, trigger,
               boundary, granted_at, revoked_at
        FROM external_standing_permissions WHERE permission_id = ?
        """,
        (permission_id,),
    ).fetchone()
    if row is None:
        return None
    return StandingPermission(
        id=str(row[0]),
        connector_id=str(row[1]),
        action_type=str(row[2]),
        target=str(row[3]),
        trigger=str(row[4]),
        boundary=str(row[5]),
        granted_at=datetime.fromisoformat(str(row[6])),
        revoked_at=(
            datetime.fromisoformat(str(row[7])) if row[7] is not None else None
        ),
    )


def _insert_permission(
    connection: sqlite3.Connection, permission: StandingPermission
) -> None:
    connection.execute(
        """
        INSERT INTO external_standing_permissions (
            permission_id, connector_id, action_type, target, trigger,
            boundary, granted_at, state, revoked_at, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            permission.id,
            permission.connector_id,
            permission.action_type,
            permission.target,
            permission.trigger,
            permission.boundary,
            permission.granted_at.isoformat(),
            "revoked" if permission.revoked_at is not None else "active",
            permission.revoked_at.isoformat() if permission.revoked_at else None,
        ),
    )


def _select_receipt(
    connection: sqlite3.Connection, write_id: str
) -> WriteReceipt | None:
    row = connection.execute(
        """
        SELECT write_id, connector_id, status, external_id, detail, attempts,
               idempotency_key, completed_at, authorization_kind,
               authorization_id, authorization_scope
        FROM external_write_receipts WHERE write_id = ?
        """,
        (write_id,),
    ).fetchone()
    if row is None:
        return None
    return WriteReceipt(
        write_id=str(row[0]),
        connector_id=str(row[1]),
        status=row[2],
        external_id=str(row[3]) if row[3] is not None else None,
        detail=str(row[4]),
        attempts=int(row[5]),
        idempotency_key=str(row[6]),
        completed_at=datetime.fromisoformat(str(row[7])),
        authorization_kind=row[8],
        authorization_id=str(row[9]),
        authorization_scope=str(row[10]),
    )


def _insert_receipt(
    connection: sqlite3.Connection, receipt: WriteReceipt
) -> None:
    connection.execute(
        """
        INSERT INTO external_write_receipts (
            write_id, payload_digest, connector_id, status, external_id,
            detail, attempts, idempotency_key, completed_at,
            authorization_kind, authorization_id, authorization_scope, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            receipt.write_id,
            _receipt_payload_digest(connection, receipt.write_id),
            receipt.connector_id,
            receipt.status,
            receipt.external_id,
            receipt.detail,
            receipt.attempts,
            receipt.idempotency_key,
            receipt.completed_at.isoformat(),
            receipt.authorization_kind,
            receipt.authorization_id,
            receipt.authorization_scope,
        ),
    )


def _receipt_payload_digest(connection: sqlite3.Connection, write_id: str) -> str:
    row = connection.execute(
        "SELECT payload_digest FROM external_prepared_writes WHERE write_id = ?",
        (write_id,),
    ).fetchone()
    return str(row[0]) if row is not None else "unknown"


def _select_execution(
    connection: sqlite3.Connection, write_id: str
) -> ExecutionClaim | None:
    row = connection.execute(
        """
        SELECT write_id, payload_digest, claim_owner, claim_state,
               idempotency_key, authorization_kind, authorization_id,
               authorization_scope, attempts, claimed_at, revision
        FROM external_write_executions WHERE write_id = ?
        """,
        (write_id,),
    ).fetchone()
    if row is None:
        return None
    return ExecutionClaim(
        str(row[0]),
        str(row[1]),
        str(row[2]),
        str(row[3]),
        str(row[4]),
        str(row[5]),
        str(row[6]),
        str(row[7]),
        int(row[8]),
        datetime.fromisoformat(str(row[9])),
        int(row[10]),
    )


def _insert_execution(
    connection: sqlite3.Connection, claim: ExecutionClaim
) -> None:
    connection.execute(
        """
        INSERT INTO external_write_executions (
            write_id, payload_digest, claim_owner, claim_state,
            idempotency_key, authorization_kind, authorization_id,
            authorization_scope, attempts, claimed_at, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim.write_id,
            claim.payload_digest,
            claim.claim_owner,
            claim.claim_state,
            claim.idempotency_key,
            claim.authorization_kind,
            claim.authorization_id,
            claim.authorization_scope,
            claim.attempts,
            claim.claimed_at.isoformat(),
            claim.revision,
        ),
    )


def _owned_execution(
    connection: sqlite3.Connection, write_id: str, owner: str
) -> ExecutionClaim:
    claim = _select_execution(connection, write_id)
    if claim is None:
        raise KeyError(f"Unknown execution claim: {write_id}")
    if claim.claim_owner != owner:
        raise ExternalWriteClaimConflict(
            f"Prepared write {write_id} is owned by another worker."
        )
    return claim


@dataclass(frozen=True)
class _LegacyExternalWrites:
    writes: tuple[PreparedWrite, ...]
    approvals: tuple[ApprovalRecord, ...]
    permissions: tuple[StandingPermission, ...]
    receipts: tuple[WriteReceipt, ...]


def _load_legacy_authority(path: Path) -> tuple[dict[str, Any], str]:
    data, _legacy, digest = _load_legacy(path)
    return data, digest


def _load_legacy(
    path: Path,
) -> tuple[dict[str, Any], _LegacyExternalWrites, str]:
    from reckoning.json_store import read_json

    data = read_json(path, default={})
    if data.get("schema_version") == 2 and data.get("authority") == "sqlite":
        from reckoning.store_migration import rollback_path

        data = read_json(rollback_path(path), default={})
    raw_writes = data.get("writes")
    raw_permissions = data.get("permissions")
    raw_receipts = data.get("receipts")
    if (
        data.get("schema_version") != 1
        or not isinstance(raw_writes, list)
        or any(not isinstance(item, dict) for item in raw_writes)
        or not isinstance(raw_permissions, list)
        or any(not isinstance(item, dict) for item in raw_permissions)
        or not isinstance(raw_receipts, list)
        or any(not isinstance(item, dict) for item in raw_receipts)
    ):
        raise RuntimeError("Unsupported external-write storage schema.")
    writes = tuple(_legacy_prepared(item) for item in raw_writes)
    permissions = tuple(_legacy_permission(item) for item in raw_permissions)
    receipts = tuple(_legacy_receipt(item) for item in raw_receipts)
    approvals = tuple(
        ApprovalRecord(
            approval_id=prepared.exact_approval_id,
            write_id=prepared.id,
            payload_digest=prepared.payload_digest,
            approved_at=prepared.prepared_at,
        )
        for prepared in writes
        if prepared.exact_approval_id is not None
    )
    legacy = _LegacyExternalWrites(writes, approvals, permissions, receipts)
    _validate_legacy(legacy)
    canonical = json.dumps(
        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return data, legacy, sha256(canonical).hexdigest()


def _legacy_prepared(item: dict[str, Any]) -> PreparedWrite:
    raw_payload = item["payload"]
    if not isinstance(raw_payload, dict):
        raise RuntimeError("prepared write payload must be an object")
    payload = {str(key): str(value) for key, value in raw_payload.items()}
    digest = str(item["payload_digest"])
    if digest != payload_digest(payload):
        raise ValueError("Legacy prepared-write payload digest does not match.")
    return PreparedWrite(
        id=str(item["id"]),
        connector_id=str(item["connector_id"]),
        action_type=str(item["action_type"]),
        target=str(item["target"]),
        trigger=str(item["trigger"]),
        boundary=str(item["boundary"]),
        payload=MappingProxyType(dict(sorted(payload.items()))),
        prepared_at=datetime.fromisoformat(str(item["prepared_at"])),
        payload_digest=digest,
        exact_approval_id=(
            str(item["exact_approval_id"])
            if item.get("exact_approval_id") is not None
            else None
        ),
    )


def _legacy_permission(item: dict[str, Any]) -> StandingPermission:
    return StandingPermission(
        id=str(item["id"]),
        connector_id=str(item["connector_id"]),
        action_type=str(item["action_type"]),
        target=str(item["target"]),
        trigger=str(item["trigger"]),
        boundary=str(item["boundary"]),
        granted_at=datetime.fromisoformat(str(item["granted_at"])),
        revoked_at=(
            datetime.fromisoformat(str(item["revoked_at"]))
            if item.get("revoked_at")
            else None
        ),
    )


def _legacy_receipt(item: dict[str, Any]) -> WriteReceipt:
    status = item["status"]
    if status not in ("success", "failed", "unknown"):
        raise ValueError("Legacy write receipt status is invalid.")
    return WriteReceipt(
        write_id=str(item["write_id"]),
        connector_id=str(item["connector_id"]),
        status=status,
        external_id=(str(item["external_id"]) if item.get("external_id") else None),
        detail=str(item["detail"]),
        attempts=int(item["attempts"]),
        idempotency_key=str(item["idempotency_key"]),
        completed_at=datetime.fromisoformat(str(item["completed_at"])),
        authorization_kind=item.get("authorization_kind", "unknown"),
        authorization_id=str(item.get("authorization_id", "unknown")),
        authorization_scope=str(item.get("authorization_scope", "")),
    )


def _validate_legacy(legacy: _LegacyExternalWrites) -> None:
    write_ids = [item.id for item in legacy.writes]
    if any(not item for item in write_ids) or len(write_ids) != len(set(write_ids)):
        raise ValueError("External-write identifiers must be unique and nonempty.")
    known = set(write_ids)
    permission_ids = [item.id for item in legacy.permissions]
    if any(not item for item in permission_ids) or len(permission_ids) != len(
        set(permission_ids)
    ):
        raise ValueError("Standing permission identifiers must be unique.")
    receipt_ids = [item.write_id for item in legacy.receipts]
    if len(receipt_ids) != len(set(receipt_ids)) or any(
        item not in known for item in receipt_ids
    ):
        raise ValueError("Write receipt links are invalid.")
    for record in legacy.approvals:
        if record.write_id not in known:
            raise ValueError("Exact approval links are invalid.")
        if not record.approval_id:
            raise ValueError("Exact approval identifiers are required.")


def _import_legacy(
    connection: sqlite3.Connection, legacy: _LegacyExternalWrites
) -> None:
    for prepared in legacy.writes:
        _insert_prepared(connection, prepared)
    for record in legacy.approvals:
        _insert_approval(connection, record)
    for permission in legacy.permissions:
        _insert_permission(connection, permission)
    for receipt in legacy.receipts:
        _insert_receipt(connection, receipt)


def _validate_migration(
    connection: sqlite3.Connection, legacy: _LegacyExternalWrites | None
) -> None:
    if legacy is None:
        return
    migrated_writes = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT write_id FROM external_prepared_writes ORDER BY rowid"
        ).fetchall()
    )
    if migrated_writes != tuple(item.id for item in legacy.writes):
        raise RuntimeError("External-write migration validation failed.")
    migrated_receipts = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT write_id FROM external_write_receipts ORDER BY rowid"
        ).fetchall()
    )
    if migrated_receipts != tuple(item.write_id for item in legacy.receipts):
        raise RuntimeError("External-write receipt migration validation failed.")
    for permission in legacy.permissions:
        migrated = _select_permission(connection, permission.id)
        if migrated is None or migrated.revoked_at != permission.revoked_at:
            raise RuntimeError("External-write permission migration validation failed.")
