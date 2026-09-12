from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from reckoning.root_database import (
    ROOT_DATABASE_FILENAME,
    connect_database,
    ensure_root_schema,
    logical_root_for,
    metadata,
    set_metadata,
    table_exists,
)

TELEGRAM_DELIVERY_SCHEMA_VERSION = "1"
_SCHEMA_METADATA_KEY = "telegram_delivery_schema_version"
_OFFSET_METADATA_KEY = "telegram_next_update_offset"

TelegramProcessingState = Literal[
    "accepted",
    "processing",
    "provider-failed",
    "processing-unknown",
    "completed",
    "discarded",
]
TelegramDeliveryState = Literal[
    "none",
    "pending",
    "delivering",
    "failed",
    "unknown",
    "delivered",
]


@dataclass(frozen=True)
class TelegramUpdateRecord:
    update_id: int
    update: dict[str, Any]
    processing_state: TelegramProcessingState
    delivery_state: TelegramDeliveryState
    claimed_by: str | None
    reply_chat_id: str | None
    reply_text: str | None
    processing_failure: str | None
    delivery_failure: str | None
    delivery_attempts: int
    accepted_at: datetime
    updated_at: datetime
    delivered_at: datetime | None


class SQLiteTelegramDeliveryRepository:
    """Own Telegram inbox, completed replies, and delivery outcomes per root."""

    def __init__(self, state_path: Path) -> None:
        self._root = logical_root_for(state_path)
        self._path = self._root / ROOT_DATABASE_FILENAME
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._path

    def initialize_offset(self, offset: int | None) -> None:
        if offset is None:
            return
        if type(offset) is not int or offset < 0:
            raise ValueError("Telegram polling offset is invalid.")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if metadata(connection, _OFFSET_METADATA_KEY) is None:
                    set_metadata(connection, _OFFSET_METADATA_KEY, str(offset))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def next_offset(self) -> int | None:
        with closing(connect_database(self._path)) as connection:
            stored = metadata(connection, _OFFSET_METADATA_KEY)
        return int(stored) if stored is not None else None

    def accept_updates(
        self,
        updates: tuple[dict[str, object], ...],
        *,
        accepted_at: datetime,
    ) -> tuple[int, ...]:
        prepared: list[tuple[int, str]] = []
        seen: dict[int, str] = {}
        for update in updates:
            update_id = update.get("update_id")
            if type(update_id) is not int:
                continue
            payload = json.dumps(
                update,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            previous = seen.get(update_id)
            if previous is not None and previous != payload:
                raise RuntimeError(
                    f"Telegram update {update_id} has conflicting payloads."
                )
            if previous is None:
                prepared.append((update_id, payload))
                seen[update_id] = payload

        timestamp = accepted_at.isoformat()
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for update_id, payload in prepared:
                    existing = connection.execute(
                        "SELECT update_payload FROM telegram_updates "
                        "WHERE update_id = ?",
                        (update_id,),
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != payload:
                            raise RuntimeError(
                                f"Telegram update {update_id} changed after acceptance."
                            )
                        continue
                    connection.execute(
                        """
                        INSERT INTO telegram_updates (
                            update_id,
                            update_payload,
                            processing_state,
                            delivery_state,
                            delivery_attempts,
                            accepted_at,
                            updated_at
                        ) VALUES (?, ?, 'accepted', 'none', 0, ?, ?)
                        """,
                        (update_id, payload, timestamp, timestamp),
                    )
                if prepared:
                    current = metadata(connection, _OFFSET_METADATA_KEY)
                    next_offset = max(update_id + 1 for update_id, _ in prepared)
                    if current is not None:
                        next_offset = max(next_offset, int(current))
                    set_metadata(connection, _OFFSET_METADATA_KEY, str(next_offset))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return tuple(update_id for update_id, _ in prepared)

    def processing_candidates(self) -> tuple[int, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT update_id
                FROM telegram_updates
                WHERE processing_state IN ('accepted', 'provider-failed')
                ORDER BY update_id
                """
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def delivery_candidates(self, *, maximum_attempts: int) -> tuple[int, ...]:
        if maximum_attempts < 1:
            raise ValueError("Telegram delivery retry limit must be positive.")
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(
                """
                SELECT update_id
                FROM telegram_updates
                WHERE delivery_state = 'pending'
                   OR (delivery_state = 'failed' AND delivery_attempts < ?)
                ORDER BY update_id
                """,
                (maximum_attempts,),
            ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def claim_processing(
        self,
        update_id: int,
        worker_id: str,
        *,
        claimed_at: datetime,
    ) -> TelegramUpdateRecord | None:
        worker = _required(worker_id, "Telegram worker identifier")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(
                    """
                    UPDATE telegram_updates
                    SET processing_state = 'processing',
                        claimed_by = ?,
                        updated_at = ?
                    WHERE update_id = ?
                      AND processing_state IN ('accepted', 'provider-failed')
                    """,
                    (worker, claimed_at.isoformat(), update_id),
                ).rowcount
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_update(update_id) if changed else None

    def complete_processing(
        self,
        update_id: int,
        worker_id: str,
        *,
        reply_chat_id: str,
        reply_text: str,
        completed_at: datetime,
    ) -> None:
        chat_id = _required(reply_chat_id, "Telegram reply chat")
        text = _required(reply_text, "Telegram reply")
        self._finish_claim(
            update_id,
            worker_id,
            """
            UPDATE telegram_updates
            SET processing_state = 'completed',
                delivery_state = 'pending',
                claimed_by = NULL,
                reply_chat_id = ?,
                reply_text = ?,
                processing_failure = NULL,
                delivery_failure = NULL,
                updated_at = ?
            WHERE update_id = ?
              AND processing_state = 'processing'
              AND claimed_by = ?
            """,
            (chat_id, text, completed_at.isoformat(), update_id, worker_id),
            "processing",
        )

    def discard_processing(
        self,
        update_id: int,
        worker_id: str,
        *,
        reason: str,
        discarded_at: datetime,
    ) -> None:
        self._finish_claim(
            update_id,
            worker_id,
            """
            UPDATE telegram_updates
            SET processing_state = 'discarded',
                delivery_state = 'none',
                claimed_by = NULL,
                processing_failure = ?,
                updated_at = ?
            WHERE update_id = ?
              AND processing_state = 'processing'
              AND claimed_by = ?
            """,
            (
                _required(reason, "Telegram discard reason"),
                discarded_at.isoformat(),
                update_id,
                worker_id,
            ),
            "processing",
        )

    def fail_processing(
        self,
        update_id: int,
        worker_id: str,
        *,
        failure: str,
        failed_at: datetime,
        outcome_unknown: bool,
    ) -> None:
        state = "processing-unknown" if outcome_unknown else "provider-failed"
        self._finish_claim(
            update_id,
            worker_id,
            """
            UPDATE telegram_updates
            SET processing_state = ?,
                delivery_state = 'none',
                claimed_by = NULL,
                processing_failure = ?,
                updated_at = ?
            WHERE update_id = ?
              AND processing_state = 'processing'
              AND claimed_by = ?
            """,
            (
                state,
                _required(failure, "Telegram processing failure"),
                failed_at.isoformat(),
                update_id,
                worker_id,
            ),
            "processing",
        )

    def recover_processing(
        self,
        update_id: int,
        *,
        recovered_at: datetime,
        acknowledge_possible_model_repeat: bool,
    ) -> TelegramUpdateRecord:
        if not acknowledge_possible_model_repeat:
            raise ValueError(
                "Interrupted Telegram model work may have run; recovery can repeat it."
            )
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(
                    """
                    UPDATE telegram_updates
                    SET processing_state = 'provider-failed',
                        claimed_by = NULL,
                        processing_failure =
                            'Explicit recovery accepted possible repeated model work.',
                        updated_at = ?
                    WHERE update_id = ?
                      AND processing_state IN ('processing', 'processing-unknown')
                    """,
                    (recovered_at.isoformat(), update_id),
                ).rowcount
                if not changed:
                    raise ValueError(
                        "Telegram update is not awaiting processing recovery."
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_update(update_id)

    def claim_delivery(
        self,
        update_id: int,
        worker_id: str,
        *,
        claimed_at: datetime,
        maximum_attempts: int,
    ) -> TelegramUpdateRecord | None:
        worker = _required(worker_id, "Telegram worker identifier")
        if maximum_attempts < 1:
            raise ValueError("Telegram delivery retry limit must be positive.")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(
                    """
                    UPDATE telegram_updates
                    SET delivery_state = 'delivering',
                        delivery_attempts = delivery_attempts + 1,
                        claimed_by = ?,
                        updated_at = ?
                    WHERE update_id = ?
                      AND processing_state = 'completed'
                      AND (
                          delivery_state = 'pending'
                          OR (
                              delivery_state = 'failed'
                              AND delivery_attempts < ?
                          )
                      )
                    """,
                    (worker, claimed_at.isoformat(), update_id, maximum_attempts),
                ).rowcount
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_update(update_id) if changed else None

    def complete_delivery(
        self,
        update_id: int,
        worker_id: str,
        *,
        delivered_at: datetime,
    ) -> None:
        timestamp = delivered_at.isoformat()
        self._finish_claim(
            update_id,
            worker_id,
            """
            UPDATE telegram_updates
            SET delivery_state = 'delivered',
                claimed_by = NULL,
                delivery_failure = NULL,
                delivered_at = ?,
                updated_at = ?
            WHERE update_id = ?
              AND delivery_state = 'delivering'
              AND claimed_by = ?
            """,
            (timestamp, timestamp, update_id, worker_id),
            "delivery",
        )

    def fail_delivery(
        self,
        update_id: int,
        worker_id: str,
        *,
        failure: str,
        failed_at: datetime,
        outcome_unknown: bool,
    ) -> None:
        state = "unknown" if outcome_unknown else "failed"
        self._finish_claim(
            update_id,
            worker_id,
            """
            UPDATE telegram_updates
            SET delivery_state = ?,
                claimed_by = NULL,
                delivery_failure = ?,
                updated_at = ?
            WHERE update_id = ?
              AND delivery_state = 'delivering'
              AND claimed_by = ?
            """,
            (
                state,
                _required(failure, "Telegram delivery failure"),
                failed_at.isoformat(),
                update_id,
                worker_id,
            ),
            "delivery",
        )

    def recover_delivery(
        self,
        update_id: int,
        *,
        recovered_at: datetime,
    ) -> TelegramUpdateRecord:
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(
                    """
                    UPDATE telegram_updates
                    SET delivery_state = 'unknown',
                        claimed_by = NULL,
                        delivery_failure =
                            'Delivery was interrupted; the remote outcome is unknown.',
                        updated_at = ?
                    WHERE update_id = ?
                      AND delivery_state = 'delivering'
                    """,
                    (recovered_at.isoformat(), update_id),
                ).rowcount
                if not changed:
                    raise ValueError(
                        "Telegram update is not awaiting delivery recovery."
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_update(update_id)

    def prepare_unknown_retry(
        self,
        update_id: int,
        *,
        prepared_at: datetime,
        acknowledge_possible_duplicate: bool,
    ) -> TelegramUpdateRecord:
        if not acknowledge_possible_duplicate:
            raise ValueError(
                "Retrying an unknown Telegram delivery may create a possible duplicate."
            )
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(
                    """
                    UPDATE telegram_updates
                    SET delivery_state = 'pending',
                        delivery_failure =
                            'Explicit retry accepted possible duplicate delivery.',
                        updated_at = ?
                    WHERE update_id = ?
                      AND processing_state = 'completed'
                      AND delivery_state = 'unknown'
                    """,
                    (prepared_at.isoformat(), update_id),
                ).rowcount
                if not changed:
                    raise ValueError(
                        "Telegram update has no unknown delivery to retry."
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_update(update_id)

    def get_update(self, update_id: int) -> TelegramUpdateRecord:
        with closing(connect_database(self._path)) as connection:
            row = connection.execute(
                f"{_SELECT_RECORD} WHERE update_id = ?", (update_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Telegram update: {update_id}")
        return _record_from_row(row)

    def list_updates(self) -> tuple[TelegramUpdateRecord, ...]:
        with closing(connect_database(self._path)) as connection:
            rows = connection.execute(f"{_SELECT_RECORD} ORDER BY update_id").fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def _initialize(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                ensure_root_schema(connection)
                version = metadata(connection, _SCHEMA_METADATA_KEY)
                has_table = table_exists(connection, "telegram_updates")
                if version not in (None, TELEGRAM_DELIVERY_SCHEMA_VERSION):
                    raise RuntimeError("Unsupported Telegram delivery storage schema.")
                if version is None and has_table:
                    raise RuntimeError("Incomplete Telegram delivery storage schema.")
                if version is not None and not has_table:
                    raise RuntimeError("Incomplete Telegram delivery storage schema.")
                if version is None:
                    _create_schema(connection)
                    set_metadata(
                        connection,
                        _SCHEMA_METADATA_KEY,
                        TELEGRAM_DELIVERY_SCHEMA_VERSION,
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _finish_claim(
        self,
        update_id: int,
        worker_id: str,
        statement: str,
        parameters: tuple[object, ...],
        claim_kind: str,
    ) -> None:
        _required(worker_id, "Telegram worker identifier")
        with closing(connect_database(self._path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                changed = connection.execute(statement, parameters).rowcount
                if not changed:
                    raise RuntimeError(
                        f"Telegram {claim_kind} claim is no longer owned by "
                        f"{worker_id} for update {update_id}."
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


def inspect_telegram_updates(state_path: Path) -> tuple[TelegramUpdateRecord, ...]:
    database = logical_root_for(state_path) / ROOT_DATABASE_FILENAME
    if not database.is_file():
        return ()
    try:
        with closing(
            sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            validate_telegram_delivery_schema(connection)
            if metadata(connection, _SCHEMA_METADATA_KEY) is None:
                return ()
            rows = connection.execute(
                f"{_SELECT_RECORD} ORDER BY update_id"
            ).fetchall()
    except sqlite3.Error as error:
        raise RuntimeError("Telegram delivery storage is invalid.") from error
    return tuple(_record_from_row(row) for row in rows)


def validate_telegram_delivery_schema(connection: sqlite3.Connection) -> None:
    version = metadata(connection, _SCHEMA_METADATA_KEY)
    has_table = table_exists(connection, "telegram_updates")
    if version not in (None, TELEGRAM_DELIVERY_SCHEMA_VERSION):
        raise RuntimeError("Unsupported Telegram delivery storage schema.")
    if (version is None) != (not has_table):
        raise RuntimeError("Incomplete Telegram delivery storage schema.")
    if not has_table:
        return
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(telegram_updates)")
    }
    if not _REQUIRED_COLUMNS.issubset(columns):
        raise RuntimeError("Incomplete Telegram delivery storage schema.")


def _create_schema(connection: sqlite3.Connection) -> None:
    ensure_root_schema(connection)
    connection.execute(
        """
        CREATE TABLE telegram_updates (
            update_id INTEGER PRIMARY KEY,
            update_payload TEXT NOT NULL,
            processing_state TEXT NOT NULL CHECK (
                processing_state IN (
                    'accepted',
                    'processing',
                    'provider-failed',
                    'processing-unknown',
                    'completed',
                    'discarded'
                )
            ),
            delivery_state TEXT NOT NULL CHECK (
                delivery_state IN (
                    'none',
                    'pending',
                    'delivering',
                    'failed',
                    'unknown',
                    'delivered'
                )
            ),
            claimed_by TEXT,
            reply_chat_id TEXT,
            reply_text TEXT,
            processing_failure TEXT,
            delivery_failure TEXT,
            delivery_attempts INTEGER NOT NULL CHECK (delivery_attempts >= 0),
            accepted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            delivered_at TEXT
        )
        """
    )


_SELECT_RECORD = """
SELECT
    update_id,
    update_payload,
    processing_state,
    delivery_state,
    claimed_by,
    reply_chat_id,
    reply_text,
    processing_failure,
    delivery_failure,
    delivery_attempts,
    accepted_at,
    updated_at,
    delivered_at
FROM telegram_updates
"""

_REQUIRED_COLUMNS = {
    "update_id",
    "update_payload",
    "processing_state",
    "delivery_state",
    "claimed_by",
    "reply_chat_id",
    "reply_text",
    "processing_failure",
    "delivery_failure",
    "delivery_attempts",
    "accepted_at",
    "updated_at",
    "delivered_at",
}


def _record_from_row(row: tuple[object, ...]) -> TelegramUpdateRecord:
    try:
        update = json.loads(str(row[1]))
        if not isinstance(update, dict):
            raise TypeError("Telegram update payload is not an object.")
        return TelegramUpdateRecord(
            update_id=int(str(row[0])),
            update=update,
            processing_state=cast(TelegramProcessingState, row[2]),
            delivery_state=cast(TelegramDeliveryState, row[3]),
            claimed_by=str(row[4]) if row[4] is not None else None,
            reply_chat_id=str(row[5]) if row[5] is not None else None,
            reply_text=str(row[6]) if row[6] is not None else None,
            processing_failure=str(row[7]) if row[7] is not None else None,
            delivery_failure=str(row[8]) if row[8] is not None else None,
            delivery_attempts=int(str(row[9])),
            accepted_at=datetime.fromisoformat(str(row[10])),
            updated_at=datetime.fromisoformat(str(row[11])),
            delivered_at=(
                datetime.fromisoformat(str(row[12])) if row[12] is not None else None
            ),
        )
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("Stored Telegram delivery state is invalid.") from error


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} is required.")
    return normalized
