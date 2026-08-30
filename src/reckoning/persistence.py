from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from reckoning.continuity import (
    CheckIn,
    Evidence,
    Inference,
    MaterialQuestion,
    PersonalRecordProposal,
    PersonalRecordVersion,
    Reckoning,
    ReckoningDraft,
    SourcedFact,
)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}.")


def _reckoning_from_data(data: dict[str, Any]) -> Reckoning:
    draft_data = data["draft"]
    if not isinstance(draft_data, dict):
        raise RuntimeError("The stored reckoning draft is invalid.")
    draft = ReckoningDraft(
        conflict=str(draft_data["conflict"]),
        questions=tuple(
            MaterialQuestion(
                text=str(item["text"]),
                effect_on_recommendation=str(item["effect_on_recommendation"]),
            )
            for item in draft_data["questions"]
        ),
        matters_now=tuple(str(item) for item in draft_data["matters_now"]),
        maintained=tuple(str(item) for item in draft_data["maintained"]),
        parked=tuple(str(item) for item in draft_data["parked"]),
        uncertainties=tuple(str(item) for item in draft_data["uncertainties"]),
        known=tuple(
            SourcedFact(
                text=str(item["text"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
            )
            for item in draft_data["known"]
        ),
        inferences=tuple(
            Inference(
                text=str(item["text"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
                uncertainty=str(item["uncertainty"]),
            )
            for item in draft_data["inferences"]
        ),
        evidence=tuple(
            Evidence(
                id=str(item["id"]),
                source=str(item["source"]),
                content=str(item["content"]),
            )
            for item in draft_data["evidence"]
        ),
        next_step=str(draft_data["next_step"]),
        proposed_records=tuple(
            PersonalRecordProposal(
                record_type=item["record_type"],
                meaning=str(item["meaning"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
            )
            for item in draft_data["proposed_records"]
        ),
    )
    return Reckoning(
        id=str(data["id"]),
        version=int(data["version"]),
        status=data["status"],
        created_at=datetime.fromisoformat(str(data["created_at"])),
        source_input=str(data["source_input"]),
        draft=draft,
        record_versions=tuple(
            PersonalRecordVersion(
                record_id=str(item["record_id"]),
                version=int(item["version"]),
                status=item["status"],
                record_type=item["record_type"],
                meaning=str(item["meaning"]),
                evidence_ids=tuple(str(value) for value in item["evidence_ids"]),
                created_at=datetime.fromisoformat(str(item["created_at"])),
                supersedes_version=(
                    int(item["supersedes_version"])
                    if item["supersedes_version"] is not None
                    else None
                ),
            )
            for item in data["record_versions"]
        ),
    )


def _check_in_from_data(data: dict[str, Any]) -> CheckIn:
    return CheckIn(
        id=str(data["id"]),
        decision_id=str(data["decision_id"]),
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
        outcome=str(data["outcome"]),
        supporting_evidence_ids=tuple(
            str(value) for value in data["supporting_evidence_ids"]
        ),
    )


class JsonFileReckoningRepository:
    """A small durable repository for the first local continuity slice."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._reckonings: dict[str, Reckoning] = {}
        self._check_ins: dict[str, CheckIn] = {}
        self._load()

    def save(self, reckoning: Reckoning) -> None:
        self._reckonings[reckoning.id] = reckoning
        self._flush()

    def get(self, reckoning_id: str) -> Reckoning:
        try:
            return self._reckonings[reckoning_id]
        except KeyError as error:
            raise KeyError(f"Unknown reckoning: {reckoning_id}") from error

    def save_check_in(self, check_in: CheckIn) -> None:
        self._check_ins[check_in.id] = check_in
        self._flush()

    def list_check_ins(self, decision_id: str) -> tuple[CheckIn, ...]:
        return tuple(
            check_in
            for check_in in self._check_ins.values()
            if check_in.decision_id == decision_id
        )

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1:
                raise RuntimeError("Unsupported continuity storage schema.")
            self._reckonings = {
                item["id"]: _reckoning_from_data(item)
                for item in data["reckonings"]
            }
            self._check_ins = {
                item["id"]: _check_in_from_data(item)
                for item in data["check_ins"]
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Continuity storage is invalid.") from error

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "reckonings": [
                asdict(reckoning) for reckoning in self._reckonings.values()
            ],
            "check_ins": [asdict(check_in) for check_in in self._check_ins.values()],
        }
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    payload,
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                    default=_json_default,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
