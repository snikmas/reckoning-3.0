from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from reckoning.json_store import atomic_write_json, read_json
from reckoning.provider_adapters import AdapterConfig
from reckoning.provider_registry import find_provider

ProcessingCategory = Literal[
    "current-request",
    "recent-channel-history",
    "private-persona",
    "personal-context",
    "confirmed-state",
    "permissions",
    "supplied-context",
    "derived-summaries",
    "raw-reckoning-input",
    "delegated-context",
]

PROCESSING_CATEGORIES: tuple[ProcessingCategory, ...] = (
    "current-request",
    "recent-channel-history",
    "private-persona",
    "personal-context",
    "confirmed-state",
    "permissions",
    "supplied-context",
    "derived-summaries",
    "raw-reckoning-input",
    "delegated-context",
)


@dataclass(frozen=True)
class ProcessingDestination:
    provider: str
    endpoint: str
    kind: Literal["local", "cloud"]

    @property
    def id(self) -> str:
        return f"{self.provider}@{self.endpoint}"


@dataclass(frozen=True)
class ProcessingGrant:
    destination_id: str
    version: int
    allowed_categories: tuple[str, ...]
    changed_at: datetime


@dataclass(frozen=True)
class ProcessingScopeStatus:
    destination: ProcessingDestination
    grant_version: int
    allowed_categories: tuple[str, ...]
    restricted_categories: tuple[str, ...]
    explicit: bool
    storage_location: str = "unknown"
    executing_node: str = "unknown"


@dataclass(frozen=True)
class ProcessingDecision:
    allowed_categories: tuple[str, ...]
    unavailable_categories: tuple[str, ...]
    blocked_categories: tuple[str, ...]


class ProcessingGrantConflict(RuntimeError):
    def __init__(self, destination_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Processing grant {destination_id} changed from revision "
            f"{expected} to {actual}."
        )
        self.destination_id = destination_id
        self.expected = expected
        self.actual = actual


class JsonFileProcessingGrantRepository:
    """Versioned grants loaded afresh so a change applies to the next run."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, destination_id: str) -> ProcessingGrant | None:
        grants = _load_grants(self._path)
        return next(
            (item for item in grants if item.destination_id == destination_id),
            None,
        )

    def list_all(self) -> tuple[ProcessingGrant, ...]:
        """Return every stored grant, including historical destinations."""
        return _load_grants(self._path)

    def save(self, grant: ProcessingGrant, *, expected_version: int) -> None:
        with _grant_write_lock(self._path):
            grants = _load_grants(self._path)
            current = next(
                (
                    item
                    for item in grants
                    if item.destination_id == grant.destination_id
                ),
                None,
            )
            actual = current.version if current is not None else 0
            if actual != expected_version:
                raise ProcessingGrantConflict(
                    grant.destination_id, expected_version, actual
                )
            if grant.version != expected_version + 1:
                raise ValueError("Processing-grant versions must increase by one.")
            updated = tuple(
                item
                for item in grants
                if item.destination_id != grant.destination_id
            ) + (grant,)
            atomic_write_json(
                self._path,
                {
                    "schema_version": 1,
                    "grants": [_grant_to_data(item) for item in updated],
                },
            )


class ProcessingScope:
    def __init__(
        self,
        repository: JsonFileProcessingGrantRepository,
        destination: ProcessingDestination,
    ) -> None:
        self._repository = repository
        self.destination = destination

    def review(self) -> ProcessingScopeStatus:
        grant = self._repository.get(self.destination.id)
        if grant is None and self.destination.kind == "local":
            allowed: tuple[str, ...] = PROCESSING_CATEGORIES
            return ProcessingScopeStatus(self.destination, 0, allowed, (), False)
        allowed = grant.allowed_categories if grant is not None else ()
        restricted = tuple(
            category for category in PROCESSING_CATEGORIES if category not in allowed
        )
        return ProcessingScopeStatus(
            self.destination,
            grant.version if grant is not None else 0,
            allowed,
            restricted,
            grant is not None,
        )

    def change(
        self,
        allowed_categories: tuple[str, ...],
        *,
        changed_at: datetime,
        expected_version: int,
    ) -> ProcessingScopeStatus:
        allowed = tuple(dict.fromkeys(allowed_categories))
        if any(not category.strip() for category in allowed):
            raise ValueError("Processing categories cannot be empty.")
        grant = ProcessingGrant(
            self.destination.id,
            expected_version + 1,
            allowed,
            changed_at,
        )
        self._repository.save(grant, expected_version=expected_version)
        return self.review()

    def evaluate(
        self,
        categories: tuple[str, ...],
        *,
        required_categories: tuple[str, ...] = (),
    ) -> ProcessingDecision:
        status = self.review()
        allowed = tuple(
            category for category in categories if category in status.allowed_categories
        )
        unavailable = tuple(category for category in categories if category not in allowed)
        required = set(required_categories)
        blocked = tuple(category for category in unavailable if category in required)
        return ProcessingDecision(allowed, unavailable, blocked)

    def authorize_delegation(
        self, provider: str, categories: tuple[str, ...]
    ) -> ProcessingDecision:
        routed = tuple(
            dict.fromkeys(
                (
                    "delegated-context",
                    *(item for item in categories if item in PROCESSING_CATEGORIES),
                )
            )
        )
        if provider != self.destination.provider:
            return ProcessingDecision((), routed, routed)
        return self.evaluate(routed, required_categories=routed)


class UnrestrictedProcessingScope:
    """Compatibility boundary for injected unit-test models, never installed runtimes."""

    destination = ProcessingDestination("injected", "in-process", "local")

    def review(self) -> ProcessingScopeStatus:
        return ProcessingScopeStatus(
            self.destination, 0, PROCESSING_CATEGORIES, (), False
        )

    def evaluate(
        self,
        categories: tuple[str, ...],
        *,
        required_categories: tuple[str, ...] = (),
    ) -> ProcessingDecision:
        del required_categories
        return ProcessingDecision(categories, (), ())

    def authorize_delegation(
        self, provider: str, categories: tuple[str, ...]
    ) -> ProcessingDecision:
        del provider
        routed = tuple(
            dict.fromkeys(
                (
                    "delegated-context",
                    *(item for item in categories if item in PROCESSING_CATEGORIES),
                )
            )
        )
        return ProcessingDecision(routed, (), ())


def full_category_grant_payload(
    destination: ProcessingDestination,
    *,
    changed_at: datetime,
    existing: tuple[ProcessingGrant, ...] = (),
) -> dict[str, object]:
    """The file payload adding a version-1 full-category grant to a destination.

    Existing grants for other destinations are merged in unchanged, so a
    migration never replaces the file with a single-grant payload and never
    drops narrowed, revoked, or historical entries.
    """
    grant = ProcessingGrant(
        destination_id=destination.id,
        version=1,
        allowed_categories=PROCESSING_CATEGORIES,
        changed_at=changed_at,
    )
    preserved = tuple(
        item for item in existing if item.destination_id != destination.id
    )
    return {
        "schema_version": 1,
        "grants": [_grant_to_data(item) for item in (*preserved, grant)],
    }


def ensure_initial_processing_grant(
    repository: JsonFileProcessingGrantRepository,
    destination: ProcessingDestination,
    *,
    changed_at: datetime,
) -> tuple[ProcessingGrant, bool] | None:
    """Create a version-1 full-category grant only when a cloud grant is missing.

    Local and fake destinations need no explicit grant. An existing grant is
    returned unchanged so previously narrowed or revoked grants are never
    widened. The boolean is True when this call created the grant.
    """
    if destination.kind != "cloud":
        return None
    existing = repository.get(destination.id)
    if existing is not None:
        return existing, False
    grant = ProcessingGrant(
        destination_id=destination.id,
        version=1,
        allowed_categories=PROCESSING_CATEGORIES,
        changed_at=changed_at,
    )
    repository.save(grant, expected_version=0)
    return grant, True


def provider_destination(
    provider_name: str, config: AdapterConfig | None
) -> ProcessingDestination:
    definition = find_provider(provider_name)
    endpoint = (
        (config.base_url if config is not None else None)
        or definition.base_url
        or "in-process"
    ).strip().rstrip("/")
    parsed = urlsplit(endpoint)
    hostname = (parsed.hostname or "").casefold()
    local = definition.is_local or definition.id == "fake" or hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    return ProcessingDestination(
        definition.id,
        endpoint,
        "local" if local else "cloud",
    )


def _grant_to_data(grant: ProcessingGrant) -> dict[str, object]:
    return {**asdict(grant), "changed_at": grant.changed_at.isoformat()}


def _grant_from_data(data: dict[str, object]) -> ProcessingGrant:
    try:
        destination_id = str(data["destination_id"]).strip()
        version = int(str(data["version"]))
        raw_allowed = data["allowed_categories"]
        if not isinstance(raw_allowed, list):
            raise TypeError("allowed categories are not a list")
        allowed = tuple(str(item) for item in raw_allowed)
        if (
            not destination_id
            or version < 1
            or len(allowed) != len(set(allowed))
            or any(not category.strip() for category in allowed)
        ):
            raise ValueError("invalid processing grant")
        return ProcessingGrant(
            destination_id=destination_id,
            version=version,
            allowed_categories=allowed,
            changed_at=datetime.fromisoformat(str(data["changed_at"])),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Stored processing grant is invalid.") from error


def _load_grants(path: Path) -> tuple[ProcessingGrant, ...]:
    data = read_json(path, default={"schema_version": 1, "grants": []})
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(
        data.get("grants"), list
    ):
        raise RuntimeError("Unsupported processing-grant storage schema.")
    grants = tuple(_grant_from_data(item) for item in data["grants"])
    destination_ids = [item.destination_id for item in grants]
    if len(destination_ids) != len(set(destination_ids)):
        raise RuntimeError("Stored processing grants contain duplicate destinations.")
    return grants


@contextmanager
def _grant_write_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        lock_path.chmod(0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
