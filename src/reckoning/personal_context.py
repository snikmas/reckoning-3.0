from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Literal, Protocol

from reckoning.json_store import atomic_write_json, read_json

ContextStatus = Literal["proposed", "active", "archived", "forgotten"]
Sensitivity = Literal["low", "private", "restricted"]
MAX_PROFILE_BYTES = 65_536
_RETRIEVAL_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "or",
    "should",
    "the",
    "to",
    "what",
    "when",
    "with",
}


@dataclass(frozen=True)
class UserProfileEntry:
    record_id: str
    section: str
    text: str
    source: str


@dataclass(frozen=True)
class PersonalContextVersion:
    record_id: str
    version: int
    status: ContextStatus
    original_text: str
    language: str
    canonical_meaning: str
    source: str
    sensitivity: Sensitivity
    retrieval_permitted: bool
    processing_location: str
    created_at: datetime
    fresh_until: datetime | None = None
    retention_until: datetime | None = None
    supersedes_version: int | None = None


@dataclass(frozen=True)
class SuppressionMarker:
    deleted_record_id: str
    deleted_at: datetime


@dataclass(frozen=True)
class ContextWhyView:
    current: PersonalContextVersion
    superseded: tuple[PersonalContextVersion, ...]


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    now: datetime
    allowed_sources: tuple[str, ...]
    allowed_sensitivities: tuple[Sensitivity, ...]
    processing_location: str


class PersonalContextRepository(Protocol):
    def save(self, version: PersonalContextVersion) -> None: ...

    def versions(self, record_id: str) -> tuple[PersonalContextVersion, ...]: ...

    def all_versions(self) -> tuple[PersonalContextVersion, ...]: ...

    def delete(self, record_id: str, marker: SuppressionMarker) -> None: ...

    def suppression_markers(self) -> tuple[SuppressionMarker, ...]: ...


class ContextLinkRemover(Protocol):
    def remove_context_links(self, record_id: str) -> None: ...


class InMemoryPersonalContextRepository:
    def __init__(self) -> None:
        self._versions: list[PersonalContextVersion] = []
        self._markers: dict[str, SuppressionMarker] = {}

    def save(self, version: PersonalContextVersion) -> None:
        self._versions.append(version)

    def versions(self, record_id: str) -> tuple[PersonalContextVersion, ...]:
        return tuple(item for item in self._versions if item.record_id == record_id)

    def all_versions(self) -> tuple[PersonalContextVersion, ...]:
        return tuple(self._versions)

    def delete(self, record_id: str, marker: SuppressionMarker) -> None:
        self._versions = [
            item for item in self._versions if item.record_id != record_id
        ]
        self._markers[record_id] = marker

    def suppression_markers(self) -> tuple[SuppressionMarker, ...]:
        return tuple(self._markers.values())


class JsonFilePersonalContextRepository(InMemoryPersonalContextRepository):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        data = read_json(
            path,
            default={"schema_version": 1, "versions": [], "suppressions": []},
        )
        if data.get("schema_version") != 1:
            raise RuntimeError("Unsupported personal-context storage schema.")
        self._versions = [
            _version_from_data(item) for item in data.get("versions", [])
        ]
        self._markers = {
            str(item["deleted_record_id"]): SuppressionMarker(
                deleted_record_id=str(item["deleted_record_id"]),
                deleted_at=datetime.fromisoformat(str(item["deleted_at"])),
            )
            for item in data.get("suppressions", [])
        }

    def save(self, version: PersonalContextVersion) -> None:
        super().save(version)
        self._flush()

    def delete(self, record_id: str, marker: SuppressionMarker) -> None:
        super().delete(record_id, marker)
        self._flush()

    def initialize(self) -> None:
        if not self._path.exists():
            self._flush()

    def _flush(self) -> None:
        atomic_write_json(
            self._path,
            {
                "schema_version": 1,
                "versions": [_version_to_data(item) for item in self._versions],
                "suppressions": [
                    {
                        "deleted_record_id": item.deleted_record_id,
                        "deleted_at": item.deleted_at.isoformat(),
                    }
                    for item in self._markers.values()
                ],
            },
        )


class PersonalContextService:
    def __init__(
        self,
        repository: PersonalContextRepository,
        link_removers: tuple[ContextLinkRemover, ...] = (),
    ) -> None:
        self._repository = repository
        self._link_removers = link_removers

    def remember(
        self,
        *,
        record_id: str,
        original_text: str,
        language: str,
        canonical_meaning: str,
        source: str,
        created_at: datetime,
        sensitivity: Sensitivity = "low",
        retrieval_permitted: bool = True,
        processing_location: str = "local",
        fresh_until: datetime | None = None,
        retention_until: datetime | None = None,
    ) -> PersonalContextVersion:
        if self._repository.versions(record_id):
            raise ValueError(f"Personal context already exists: {record_id}")
        version = PersonalContextVersion(
            record_id=record_id,
            version=1,
            status="active",
            original_text=_required(original_text, "Original wording"),
            language=_required(language, "Language"),
            canonical_meaning=_required(canonical_meaning, "Canonical meaning"),
            source=_required(source, "Source"),
            sensitivity=sensitivity,
            retrieval_permitted=retrieval_permitted,
            processing_location=processing_location,
            created_at=created_at,
            fresh_until=fresh_until,
            retention_until=retention_until,
        )
        self._repository.save(version)
        return version

    def propose(
        self,
        *,
        record_id: str,
        original_text: str,
        language: str,
        canonical_meaning: str,
        source: str,
        created_at: datetime,
        sensitivity: Sensitivity = "private",
        processing_location: str = "local",
    ) -> PersonalContextVersion:
        if self._repository.versions(record_id):
            raise ValueError(f"Personal context already exists: {record_id}")
        version = PersonalContextVersion(
            record_id=record_id,
            version=1,
            status="proposed",
            original_text=_required(original_text, "Original wording"),
            language=_required(language, "Language"),
            canonical_meaning=_required(canonical_meaning, "Canonical meaning"),
            source=_required(source, "Source"),
            sensitivity=sensitivity,
            retrieval_permitted=False,
            processing_location=processing_location,
            created_at=created_at,
        )
        self._repository.save(version)
        return version

    def correct(
        self,
        record_id: str,
        *,
        original_text: str,
        language: str,
        canonical_meaning: str,
        corrected_at: datetime,
    ) -> PersonalContextVersion:
        current = self._current(record_id)
        corrected = replace(
            current,
            version=current.version + 1,
            status="proposed" if current.status == "proposed" else "active",
            original_text=_required(original_text, "Correction wording"),
            language=_required(language, "Correction language"),
            canonical_meaning=_required(canonical_meaning, "Canonical meaning"),
            source="direct user correction",
            created_at=corrected_at,
            supersedes_version=current.version,
        )
        self._repository.save(corrected)
        return corrected

    def confirm(self, record_id: str, confirmed_at: datetime) -> PersonalContextVersion:
        current = self._current(record_id)
        if current.status != "proposed":
            raise ValueError("Only proposed personal context can be confirmed.")
        confirmed = replace(
            current,
            version=current.version + 1,
            status="active",
            retrieval_permitted=True,
            created_at=confirmed_at,
            supersedes_version=current.version,
        )
        self._repository.save(confirmed)
        return confirmed

    def retrieve(self, query: RetrievalQuery) -> tuple[PersonalContextVersion, ...]:
        query_terms = _lexical_terms(query.text)
        return tuple(
            item
            for item in self._current_versions()
            if item.status == "active"
            and item.retrieval_permitted
            and item.source in query.allowed_sources
            and item.sensitivity in query.allowed_sensitivities
            and item.processing_location == query.processing_location
            and (item.fresh_until is None or item.fresh_until >= query.now)
            and _is_relevant(query.text, query_terms, item)
        )

    def retrieve_proposed(
        self, query: RetrievalQuery
    ) -> tuple[PersonalContextVersion, ...]:
        query_terms = _lexical_terms(query.text)
        return tuple(
            item
            for item in self._current_versions()
            if item.status == "proposed"
            and item.source in query.allowed_sources
            and item.sensitivity in query.allowed_sensitivities
            and item.processing_location == query.processing_location
            and _is_relevant(query.text, query_terms, item)
        )

    def list_active(self) -> tuple[PersonalContextVersion, ...]:
        return tuple(
            item for item in self._current_versions() if item.status == "active"
        )

    def list_proposed(self) -> tuple[PersonalContextVersion, ...]:
        return tuple(
            item for item in self._current_versions() if item.status == "proposed"
        )

    def inspect(self, record_id: str) -> ContextWhyView:
        versions = self._repository.versions(record_id)
        if not versions:
            raise KeyError(f"Unknown personal context: {record_id}")
        return ContextWhyView(current=versions[-1], superseded=versions[:-1])

    def archive(self, record_id: str, changed_at: datetime) -> PersonalContextVersion:
        return self._transition(record_id, "archived", changed_at)

    def forget(self, record_id: str, changed_at: datetime) -> PersonalContextVersion:
        return self._transition(record_id, "forgotten", changed_at)

    def recover(self, record_id: str, changed_at: datetime) -> PersonalContextVersion:
        return self._transition(record_id, "active", changed_at)

    def enforce_retention(self, now: datetime) -> tuple[SuppressionMarker, ...]:
        expired_ids = {
            item.record_id
            for item in self._current_versions()
            if item.retention_until is not None and item.retention_until <= now
        }
        return tuple(self.delete(record_id, now) for record_id in sorted(expired_ids))

    def delete(self, record_id: str, deleted_at: datetime) -> SuppressionMarker:
        self._current(record_id)
        marker = SuppressionMarker(record_id, deleted_at)
        for remover in self._link_removers:
            remover.remove_context_links(record_id)
        self._repository.delete(record_id, marker)
        return marker

    def _transition(
        self, record_id: str, status: ContextStatus, changed_at: datetime
    ) -> PersonalContextVersion:
        current = self._current(record_id)
        if current.status == status:
            return current
        transitioned = replace(
            current,
            version=current.version + 1,
            status=status,
            created_at=changed_at,
            supersedes_version=current.version,
        )
        self._repository.save(transitioned)
        return transitioned

    def _current(self, record_id: str) -> PersonalContextVersion:
        versions = self._repository.versions(record_id)
        if not versions:
            raise KeyError(f"Unknown personal context: {record_id}")
        return versions[-1]

    def _current_versions(self) -> tuple[PersonalContextVersion, ...]:
        current: dict[str, PersonalContextVersion] = {}
        for item in self._repository.all_versions():
            current[item.record_id] = item
        return tuple(current.values())


def _required(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} cannot be empty.")
    return cleaned


def _lexical_terms(text: str) -> set[str]:
    normalized = text.casefold()
    words = {
        word
        for word in re.findall(r"\w+", normalized, flags=re.UNICODE)
        if word not in _RETRIEVAL_STOP_WORDS and len(word) > 1
    }
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
    words.update(cjk[index : index + 2] for index in range(len(cjk) - 1))
    return words


def _is_relevant(
    query_text: str,
    query_terms: set[str],
    item: PersonalContextVersion,
) -> bool:
    searchable = f"{item.original_text} {item.canonical_meaning}".casefold()
    normalized_query = query_text.casefold().strip()
    return normalized_query in searchable or bool(
        query_terms & _lexical_terms(searchable)
    )


def _version_to_data(version: PersonalContextVersion) -> dict[str, object]:
    data = asdict(version)
    data["created_at"] = version.created_at.isoformat()
    data["fresh_until"] = (
        version.fresh_until.isoformat() if version.fresh_until else None
    )
    data["retention_until"] = (
        version.retention_until.isoformat() if version.retention_until else None
    )
    return data


def _version_from_data(data: dict[str, Any]) -> PersonalContextVersion:
    return PersonalContextVersion(
        record_id=str(data["record_id"]),
        version=int(data["version"]),
        status=data["status"],
        original_text=str(data["original_text"]),
        language=str(data["language"]),
        canonical_meaning=str(data["canonical_meaning"]),
        source=str(data["source"]),
        sensitivity=data["sensitivity"],
        retrieval_permitted=bool(data["retrieval_permitted"]),
        processing_location=str(data["processing_location"]),
        created_at=datetime.fromisoformat(str(data["created_at"])),
        fresh_until=(
            datetime.fromisoformat(str(data["fresh_until"]))
            if data.get("fresh_until")
            else None
        ),
        retention_until=(
            datetime.fromisoformat(str(data["retention_until"]))
            if data.get("retention_until")
            else None
        ),
        supersedes_version=(
            int(data["supersedes_version"])
            if data.get("supersedes_version") is not None
            else None
        ),
    )


def read_user_profile(path: Path) -> tuple[UserProfileEntry, ...]:
    source_path = path.expanduser()
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("The user profile must be a regular file.")
    if source_path.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError("The user profile cannot exceed 65,536 bytes.")
    try:
        text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("The user profile must be UTF-8 text.") from error
    entries = _profile_entries(text, source_path.name)
    if not entries:
        raise ValueError("The user profile contains no reviewable statements.")
    return entries


def _profile_entries(text: str, source_name: str) -> tuple[UserProfileEntry, ...]:
    section = "Profile"
    paragraphs: list[tuple[str, str]] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        statement = " ".join(current).strip()
        current.clear()
        if statement:
            paragraphs.append((section, statement))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if heading:
            flush()
            section = heading.group(1).strip()
            continue
        if re.match(r"^#\s+", line) or re.fullmatch(r"\|?\s*:?-{3,}.*", line):
            continue
        if not line:
            flush()
            continue
        list_item = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if list_item:
            flush()
            paragraphs.append((section, list_item.group(1).strip()))
            continue
        current.append(line)
    flush()

    entries: list[UserProfileEntry] = []
    for index, (entry_section, statement) in enumerate(paragraphs, start=1):
        digest = sha256(
            f"{entry_section}\0{statement}".encode("utf-8")
        ).hexdigest()[:16]
        entries.append(
            UserProfileEntry(
                record_id=f"profile-{index:03d}-{digest}",
                section=entry_section,
                text=statement,
                source=f"user profile import: {source_name}#{entry_section}",
            )
        )
    return tuple(entries)
