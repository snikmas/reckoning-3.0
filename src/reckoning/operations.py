from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any, Literal, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from reckoning.application import PersonaSettings, PlacementState, create_local_application
from reckoning.interfaces import (
    NodeAvailabilitySource,
    NodeName,
    PlacementPolicy,
    SourcePlacement,
)
from reckoning.json_store import atomic_write_json, read_json
from reckoning.personas import (
    DEFAULT_PERSONAS,
    JsonFilePersonaRepository,
    PersonaDefinition,
    PersonaService,
)
from reckoning.personal_context import (
    JsonFilePersonalContextRepository,
    PersonalContextService,
    UserProfileEntry,
    read_user_profile,
)

TRANSFER_FORMAT = "reckoning-encrypted-transfer"
TRANSFER_VERSION = 1
PASSPHRASE_ENV = "RECKONING_TRANSFER_PASSPHRASE"
RELEASE_GATES = (
    "repeated_value",
    "clean_install",
    "recovery",
    "privacy",
    "deletion",
    "external_action",
    "core_continuity_without_developer",
)

PlacementProfile = Literal["local", "personal-server", "hybrid"]
TransferKind = Literal["backup", "export", "migration"]
StateCategory = Literal[
    "personal-context", "confirmed-state", "approved-remote-sources"
]
NODE_MARKER = ".reckoning-node.json"


class OperationError(RuntimeError):
    """An operational command failed without changing user data."""


@dataclass(frozen=True)
class ReleaseReadiness:
    ready: bool
    missing_gates: tuple[str, ...]


@dataclass(frozen=True)
class InstallationRuntime:
    persona: PersonaSettings
    application_placement: PlacementState
    interface_placement: PlacementPolicy
    routes: tuple["SourceStateRoute", ...]
    node_availability: NodeAvailabilitySource

    def root_for(self, category: StateCategory) -> Path:
        try:
            return next(route.root for route in self.routes if route.category == category)
        except StopIteration as error:
            raise KeyError(f"No storage route for {category}.") from error

    def state_path(self, category: StateCategory, filename: str) -> Path:
        relative = Path(filename)
        if relative.is_absolute() or len(relative.parts) != 1 or relative.name != filename:
            raise ValueError("A routed state filename cannot contain a path.")
        return self.root_for(category) / filename


@dataclass(frozen=True)
class SourceStateRoute:
    category: StateCategory
    node: NodeName
    root: Path


class NodeAvailabilityProbe(Protocol):
    def is_available(self, root: Path, node: NodeName) -> bool: ...


class FilesystemNodeAvailabilityProbe:
    """Check a configured node through its private marker and filesystem access."""

    def is_available(self, root: Path, node: NodeName) -> bool:
        marker = root / NODE_MARKER
        if not root.is_dir() or not marker.is_file():
            return False
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return (
            data == {"schema_version": 1, "node": node}
            and os.access(root, os.R_OK | os.W_OK)
        )


@dataclass(frozen=True)
class RoutedNodeAvailability:
    local_root: Path
    server_root: Path | None
    probe: NodeAvailabilityProbe
    local_override: bool | None = None
    server_override: bool | None = None

    def is_available(self, node: NodeName) -> bool:
        if node == "local":
            if self.local_override is not None:
                return self.local_override
            return self.probe.is_available(self.local_root, node)
        if self.server_override is not None:
            return self.server_override
        return self.server_root is not None and self.probe.is_available(
            self.server_root, node
        )


def load_installation_runtime(
    data_dir: Path,
    *,
    server_data_dir: Path | None = None,
    availability_probe: NodeAvailabilityProbe | None = None,
    local_node_available: bool | None = None,
    server_node_available: bool | None = None,
) -> InstallationRuntime:
    """Load the setup choices that must govern every public interface."""
    local_root = data_dir.expanduser().resolve()
    instance = read_json(local_root / "instance.json", default={})
    profile = instance.get("placement_profile")
    if profile not in {"local", "personal-server", "hybrid"}:
        raise OperationError("instance.json has no supported placement profile")

    configured_roots = instance.get("storage_roots")
    if not isinstance(configured_roots, dict):
        raise OperationError("instance.json has no enforceable storage roots")
    if configured_roots.get("local") != "local-data-dir":
        raise OperationError("instance.json has no enforceable local root")
    configured_server = _optional_absolute_root(configured_roots.get("server"))
    if server_data_dir is not None:
        requested_server = server_data_dir.expanduser().resolve()
        if configured_server != requested_server:
            raise OperationError(
                "--server-data-dir does not match the configured personal-server root"
            )
    if profile != "local" and configured_server is None:
        raise OperationError(f"{profile} placement requires an explicit server root")
    server_root = configured_server

    try:
        persona = PersonaService(
            JsonFilePersonaRepository(local_root / "personas.json")
        ).active().to_application_settings()
    except (KeyError, LookupError, RuntimeError, ValueError) as error:
        raise OperationError("the selected persona configuration is invalid") from error

    placement_data = instance.get("placement_policy")
    routes = _state_routes(profile, placement_data, local_root, server_root)
    availability = RoutedNodeAvailability(
        local_root,
        server_root,
        availability_probe or FilesystemNodeAvailabilityProbe(),
        local_node_available,
        server_node_available,
    )
    interface_placement = _runtime_placement_policy(
        profile,
        routes=routes,
        availability=availability,
    )
    confirmed_node = next(
        route.node for route in routes if route.category == "confirmed-state"
    )
    primary_node = "local" if confirmed_node == "local" else "personal-server"
    return InstallationRuntime(
        persona=persona,
        application_placement=PlacementState(
            processing_location=primary_node,
            storage_location=primary_node,
            local_node_available=availability.is_available(confirmed_node),
        ),
        interface_placement=interface_placement,
        routes=routes,
        node_availability=availability,
    )


def setup_instance(
    data_dir: Path,
    placement: PlacementProfile,
    persona: PersonaDefinition | None = None,
    *,
    server_data_dir: Path | None = None,
    user_profile: Path | None = None,
) -> dict[str, Any]:
    local_root = data_dir.expanduser().resolve()
    _require_empty_setup_root(local_root, "data")
    server_root = _setup_server_root(local_root, placement, server_data_dir)
    if server_root is not None:
        _require_empty_setup_root(server_root, "server data")
    defaults_by_id = {item.id: item for item in DEFAULT_PERSONAS}
    if (
        persona is not None
        and persona.id in defaults_by_id
        and persona != defaults_by_id[persona.id]
    ):
        raise OperationError("an original persona cannot replace a default persona")
    try:
        profile_entries = read_user_profile(user_profile) if user_profile else ()
    except (OSError, ValueError) as error:
        raise OperationError(str(error)) from error
    _check_core_continuity_loop()
    selected_persona = persona or DEFAULT_PERSONAS[0]
    created_at = datetime.now(timezone.utc)
    roots = {
        "local": "local-data-dir",
        "server": str(server_root) if server_root else None,
    }
    configuration: dict[str, Any] = {
        "schema_version": 1,
        "instance_type": "single-user",
        "hosted_tenancy": False,
        "public_admin": False,
        "placement_profile": placement,
        "storage_roots": roots,
        "active_persona_id": selected_persona.id,
        "placement_policy": _placement_policy(placement),
        "permission_policy": {
            "external_writes": "exact-or-standing-permission",
            "lower_layers_cannot_expand_authority": True,
        },
        "limited_mode": {
            "visible": True,
            "missing_required_context": "block-or-declare-limited",
        },
        "operational_access": "loopback-or-ssh-tunnel",
        "installation_check": {
            "core_continuity_loop": "passed",
            "provider": "deterministic-fake",
        },
        "profile_bootstrap": {
            "provided": bool(profile_entries),
            "proposal_count": len(profile_entries),
            "raw_profile_retained": False,
        },
        "created_at": created_at.isoformat(),
    }

    local_staging: Path | None = None
    server_staging: Path | None = None
    try:
        local_staging = _new_setup_staging(local_root)
        if server_root is not None:
            server_staging = _new_setup_staging(server_root)
        _write_staged_setup(
            local_staging,
            server_staging,
            placement,
            selected_persona,
            configuration,
            profile_entries,
            created_at,
        )
        staged_roots = [(local_root, local_staging)]
        if server_root is not None and server_staging is not None:
            staged_roots.append((server_root, server_staging))
        _commit_staged_setup(tuple(staged_roots))
    except OSError as error:
        raise OperationError(
            "setup failed; no installation was created and existing empty "
            "roots were preserved"
        ) from error
    finally:
        for staging in (local_staging, server_staging):
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
    return configuration


def _require_empty_setup_root(root: Path, label: str) -> None:
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
        raise OperationError(f"setup requires a new or empty {label} directory")


def _new_setup_staging(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(mkdtemp(prefix=f".{target.name}.setup-", dir=target.parent))


def _write_staged_setup(
    local_staging: Path,
    server_staging: Path | None,
    placement: PlacementProfile,
    selected_persona: PersonaDefinition,
    configuration: dict[str, Any],
    profile_entries: tuple[UserProfileEntry, ...],
    created_at: datetime,
) -> None:
    routes = _state_routes(
        placement,
        _placement_policy(placement),
        local_staging,
        server_staging,
    )
    for route in routes:
        route.root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        local_staging / NODE_MARKER,
        {"schema_version": 1, "node": "local"},
    )
    if server_staging is not None:
        atomic_write_json(
            server_staging / NODE_MARKER,
            {"schema_version": 1, "node": "server"},
        )
    atomic_write_json(local_staging / "instance.json", configuration)
    persona_service = PersonaService(
        JsonFilePersonaRepository(local_staging / "personas.json")
    )
    if selected_persona in DEFAULT_PERSONAS:
        persona_service.select(selected_persona.id)
    else:
        persona_service.author_and_select(selected_persona)
    profile_route = next(
        route for route in routes if route.category == "personal-context"
    )
    profile_repository = JsonFilePersonalContextRepository(
        profile_route.root / "personal-context.json"
    )
    profile_repository.initialize()
    profile_service = PersonalContextService(profile_repository)
    processing_location = (
        "local" if profile_route.node == "local" else "personal-server"
    )
    for entry in profile_entries:
        profile_service.propose(
            record_id=entry.record_id,
            original_text=entry.text,
            language="und",
            canonical_meaning=f"{entry.section}: {entry.text}",
            source=entry.source,
            created_at=created_at,
            processing_location=processing_location,
        )
    atomic_write_json(
        local_staging / "release-evidence.json",
        {"schema_version": 1, **{gate: False for gate in RELEASE_GATES}},
    )
    local_staging.chmod(0o700)
    if server_staging is not None:
        server_staging.chmod(0o700)


def _commit_staged_setup(staged_roots: tuple[tuple[Path, Path], ...]) -> None:
    installed: list[tuple[Path, int | None]] = []
    try:
        for target, staging in staged_roots:
            original_mode = (
                stat.S_IMODE(target.stat().st_mode) if target.exists() else None
            )
            if original_mode is not None:
                target.rmdir()
            try:
                os.replace(staging, target)
            except OSError:
                if original_mode is not None:
                    target.mkdir()
                    target.chmod(original_mode)
                raise
            installed.append((target, original_mode))
    except OSError:
        for target, original_mode in reversed(installed):
            shutil.rmtree(target)
            if original_mode is not None:
                target.mkdir()
                target.chmod(original_mode)
        raise


def diagnose(
    data_dir: Path, *, server_data_dir: Path | None = None
) -> tuple[str, ...]:
    files = _state_files(data_dir)
    for path in files:
        _read_state_file(path)
    instance_path = data_dir / "instance.json"
    if not instance_path.exists():
        return (
            "status: degraded",
            "reason: instance.json is missing; run setup or restore a backup",
            f"valid state files: {len(files)}",
        )
    instance = _read_state_file(instance_path)
    runtime = load_installation_runtime(
        data_dir, server_data_dir=server_data_dir
    )
    outcome = runtime.interface_placement.current_outcome()
    local_health = runtime.node_availability.is_available("local")
    server_health = runtime.node_availability.is_available("server")
    return (
        f"status: {'healthy' if outcome.status == 'available' else 'degraded'}",
        f"instance: {instance.get('instance_type', 'unknown')}",
        f"placement: {instance.get('placement_profile', 'unknown')}",
        f"local node: {'available' if local_health else 'offline'}",
        f"server node: {'available' if server_health else 'offline'}",
        f"placement detail: {outcome.notice}",
        f"valid state files: {len(files)}",
    )


def create_transfer(
    data_dir: Path,
    output: Path,
    passphrase: str,
    *,
    kind: TransferKind,
    server_data_dir: Path | None = None,
) -> int:
    roots = _transfer_roots(data_dir, server_data_dir=server_data_dir)
    rooted_files = tuple(
        (root_name, root, path)
        for root_name, root in roots
        for path in _state_files(root)
    )
    if not rooted_files:
        raise OperationError("no JSON state files were found")
    entries: list[dict[str, Any]] = []
    for root_name, root, path in rooted_files:
        _read_state_file(path)
        content = _portable_state_content(path, root_name=root_name)
        entries.append(
            {
                "root": root_name,
                "path": path.relative_to(root).as_posix(),
                "size": len(content),
                "sha256": sha256(content).hexdigest(),
                "content": b64encode(content).decode("ascii"),
            }
        )
    payload = {
        "format": TRANSFER_FORMAT,
        "format_version": TRANSFER_VERSION,
        "transfer_kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "logical_roots": [name for name, _ in roots],
        "files": entries,
    }
    _write_encrypted_payload(output, payload, passphrase)
    return len(entries)


def restore_transfer(
    archive: Path,
    data_dir: Path,
    passphrase: str,
    *,
    server_data_dir: Path | None = None,
) -> int:
    payload = _read_encrypted_payload(archive, passphrase)
    files = _validated_transfer_files(payload)
    needs_server = any(root_name == "server" for root_name, _, _ in files)
    local_root = data_dir.expanduser().resolve()
    if needs_server and server_data_dir is None:
        raise OperationError("this transfer requires --server-data-dir")
    server_root = (
        server_data_dir.expanduser().resolve()
        if server_data_dir is not None
        else None
    )
    if not needs_server and server_root is not None:
        raise OperationError("this transfer does not contain a server root")
    if server_root is not None and _roots_overlap(local_root, server_root):
        raise OperationError("local and personal-server roots must be separate")
    _require_empty_restore_root(local_root, "data")
    if server_root is not None:
        _require_empty_restore_root(server_root, "server data")

    local_staging: Path | None = None
    server_staging: Path | None = None
    try:
        local_staging = _new_restore_staging(local_root)
        if server_root is not None:
            server_staging = _new_restore_staging(server_root)
        staging_roots = {"local": local_staging, "server": server_staging}
        for root_name, relative_path, content in files:
            staging_root = staging_roots[root_name]
            if staging_root is None:
                raise OperationError("the transfer server root has no destination")
            target = staging_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o600)
        _rewrite_restored_storage_roots(
            local_staging / "instance.json",
            server_root=server_root,
        )
        staged_roots = [(local_root, local_staging)]
        if server_root is not None and server_staging is not None:
            staged_roots.append((server_root, server_staging))
        _commit_staged_setup(tuple(staged_roots))
    finally:
        for staging in (local_staging, server_staging):
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
    return len(files)


def migrate_transfer(
    source: Path, output: Path, passphrase: str
) -> tuple[int, int, int]:
    payload = _read_encrypted_payload(source, passphrase)
    files = _validated_transfer_files(payload)
    source_version = int(payload["format_version"])
    migrated = {
        **payload,
        "format_version": TRANSFER_VERSION,
        "transfer_kind": "migration",
        "migrated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_encrypted_payload(output, migrated, passphrase)
    return source_version, TRANSFER_VERSION, len(files)


def release_readiness(evidence_path: Path) -> ReleaseReadiness:
    try:
        evidence = read_json(evidence_path, default={})
    except RuntimeError as error:
        raise OperationError(str(error)) from error
    if evidence.get("schema_version") != 1:
        raise OperationError("release evidence must use schema_version 1")
    missing = tuple(gate for gate in RELEASE_GATES if evidence.get(gate) is not True)
    return ReleaseReadiness(not missing, missing)


def _check_core_continuity_loop() -> None:
    with TemporaryDirectory(prefix="reckoning-install-check-") as temporary:
        state_path = Path(temporary) / "continuity.json"
        application = create_local_application(state_path, provider_name="fake")
        decision = application.start_reckoning(
            "I need to protect one fixed commitment while keeping one smaller "
            "task alive."
        )
        confirmed = application.confirm_reckoning(decision.id)
        if confirmed.status != "confirmed":
            raise OperationError("the clean-install continuity check did not confirm")
        resumed = application.resume_decision(decision.id)
        application.record_check_in(
            decision.id, "The fixed commitment moved and the smaller task stayed alive."
        )
        restarted = create_local_application(state_path, provider_name="fake")
        resumed_after_restart = restarted.resume_decision(decision.id)
        if resumed.decision.id != decision.id or not resumed_after_restart.check_ins:
            raise OperationError("the clean-install continuity check did not persist")


def _placement_policy(placement: PlacementProfile) -> dict[str, str]:
    if placement == "local":
        return {
            "private_personal_context": "local",
            "confirmed_state": "local",
            "approved_remote_sources": "local",
        }
    if placement == "personal-server":
        return {
            "private_personal_context": "personal-server",
            "confirmed_state": "personal-server",
            "approved_remote_sources": "personal-server",
        }
    return {
        "private_personal_context": "local",
        "confirmed_state": "local",
        "approved_remote_sources": "personal-server",
    }


def _runtime_placement_policy(
    profile: PlacementProfile,
    *,
    routes: tuple[SourceStateRoute, ...],
    availability: NodeAvailabilitySource,
) -> PlacementPolicy:
    nodes = {route.category: route.node for route in routes}
    private_node = nodes["personal-context"]
    confirmed_node = nodes["confirmed-state"]
    remote_source_node = nodes["approved-remote-sources"]
    return PlacementPolicy(
        profile=profile,
        categories=(
            SourcePlacement(
                category="personal-context",
                sensitivity="private",
                storage_node=private_node,
                processing_node=private_node,
                remote_approved=private_node == "server",
            ),
            SourcePlacement(
                category="confirmed-state",
                sensitivity="private",
                storage_node=confirmed_node,
                processing_node=confirmed_node,
                remote_approved=confirmed_node == "server",
            ),
            SourcePlacement(
                category="approved-remote-sources",
                sensitivity="public",
                storage_node=remote_source_node,
                processing_node=remote_source_node,
            ),
        ),
        local_node_available=availability.is_available("local"),
        server_node_available=availability.is_available("server"),
        availability_source=availability,
    )


def _state_routes(
    profile: PlacementProfile,
    placement_data: object,
    local_root: Path,
    server_root: Path | None,
) -> tuple[SourceStateRoute, ...]:
    if not isinstance(placement_data, dict):
        raise OperationError("instance.json has no category placement policy")
    definitions: tuple[tuple[StateCategory, str], ...] = (
        ("personal-context", "private_personal_context"),
        ("confirmed-state", "confirmed_state"),
        ("approved-remote-sources", "approved_remote_sources"),
    )
    routes: list[SourceStateRoute] = []
    for category, key in definitions:
        configured = placement_data.get(key)
        if configured == "local":
            node: NodeName = "local"
            node_root = local_root
        elif configured == "personal-server" and server_root is not None:
            node = "server"
            node_root = server_root
        else:
            raise OperationError(
                f"invalid {category} route for {profile} placement"
            )
        routes.append(SourceStateRoute(category, node, node_root / category))
    return tuple(routes)


def _setup_server_root(
    local_root: Path,
    placement: PlacementProfile,
    server_data_dir: Path | None,
) -> Path | None:
    if placement == "local":
        if server_data_dir is not None:
            raise OperationError("local placement does not accept a server data root")
        return None
    if server_data_dir is None:
        raise OperationError(f"{placement} placement requires --server-data-dir")
    server_root = server_data_dir.expanduser().resolve()
    if _roots_overlap(local_root, server_root):
        raise OperationError("local and personal-server roots must be separate")
    return server_root


def _absolute_root(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise OperationError(f"instance.json has no configured {name} root")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OperationError(f"the configured {name} root must be absolute")
    return path.resolve()


def _optional_absolute_root(value: object) -> Path | None:
    if value is None:
        return None
    return _absolute_root(value, "server")


def _transfer_roots(
    data_dir: Path,
    *,
    server_data_dir: Path | None,
) -> tuple[tuple[Literal["local", "server"], Path], ...]:
    local_root = data_dir.expanduser().resolve()
    instance_path = local_root / "instance.json"
    if not instance_path.exists():
        if server_data_dir is not None:
            raise OperationError("legacy local state does not accept --server-data-dir")
        return (("local", local_root),)
    instance = _read_state_file(instance_path)
    profile = instance.get("placement_profile")
    configured_roots = instance.get("storage_roots")
    if profile not in {"local", "personal-server", "hybrid"} or not isinstance(
        configured_roots, dict
    ):
        raise OperationError("instance.json has invalid placement routing")
    configured_server = _optional_absolute_root(configured_roots.get("server"))
    if profile == "local":
        if server_data_dir is not None or configured_server is not None:
            raise OperationError("local placement does not accept --server-data-dir")
        return (("local", local_root),)
    if server_data_dir is None:
        raise OperationError(f"{profile} transfer requires --server-data-dir")
    server_root = server_data_dir.expanduser().resolve()
    if server_root != configured_server:
        raise OperationError(
            "--server-data-dir does not match the configured personal-server root"
        )
    if _roots_overlap(local_root, server_root):
        raise OperationError("local and personal-server roots must be separate")
    return (("local", local_root), ("server", server_root))


def _roots_overlap(local_root: Path, server_root: Path) -> bool:
    return (
        server_root == local_root
        or server_root.is_relative_to(local_root)
        or local_root.is_relative_to(server_root)
    )


def _portable_state_content(
    path: Path,
    *,
    root_name: Literal["local", "server"],
) -> bytes:
    content = path.read_bytes()
    if root_name != "local" or path.name != "instance.json":
        return content
    data = json.loads(content.decode("utf-8"))
    if not isinstance(data, dict):
        raise OperationError("instance.json must contain an object")
    roots = data.get("storage_roots")
    if isinstance(roots, dict) and roots.get("server") is not None:
        data = {**data, "storage_roots": {**roots, "server": "server-data-dir"}}
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return content


def _require_empty_restore_root(root: Path, label: str) -> None:
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
        raise OperationError(f"restore requires a clean, empty {label} directory")


def _new_restore_staging(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))


def _rewrite_restored_storage_roots(
    instance_path: Path,
    *,
    server_root: Path | None,
) -> None:
    if not instance_path.exists():
        return
    instance = _read_state_file(instance_path)
    roots = instance.get("storage_roots")
    if not isinstance(roots, dict):
        return
    archived_server = roots.get("server")
    if archived_server == "server-data-dir":
        if server_root is None:
            raise OperationError("the restored instance requires a server root")
        roots = {**roots, "server": str(server_root)}
    elif archived_server is not None:
        raise OperationError("the transfer contains a non-portable server root")
    atomic_write_json(instance_path, {**instance, "storage_roots": roots})


def _state_files(data_dir: Path) -> tuple[Path, ...]:
    if not data_dir.is_dir():
        raise OperationError(f"data directory does not exist: {data_dir}")
    files = tuple(sorted(data_dir.rglob("*.json")))
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise OperationError(
            "state files must be regular files inside the data directory"
        )
    return files


def _read_state_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OperationError(f"invalid JSON state file: {path.name}") from error
    if not isinstance(data, dict):
        raise OperationError(f"JSON state must be an object: {path.name}")
    return data


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 12:
        raise OperationError(
            f"{PASSPHRASE_ENV} must contain at least 12 characters"
        )
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(
        passphrase.encode("utf-8")
    )


def _write_encrypted_payload(
    output: Path, payload: dict[str, Any], passphrase: str
) -> None:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt)
    plaintext = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, TRANSFER_FORMAT.encode())
    atomic_write_json(
        output,
        {
            "format": TRANSFER_FORMAT,
            "format_version": TRANSFER_VERSION,
            "cipher": "AES-256-GCM",
            "kdf": {
                "name": "scrypt",
                "n": 2**14,
                "r": 8,
                "p": 1,
                "salt": b64encode(salt).decode("ascii"),
            },
            "nonce": b64encode(nonce).decode("ascii"),
            "ciphertext": b64encode(ciphertext).decode("ascii"),
        },
    )
    output.chmod(0o600)


def _read_encrypted_payload(archive: Path, passphrase: str) -> dict[str, Any]:
    try:
        envelope = json.loads(archive.read_text(encoding="utf-8"))
        if (
            not isinstance(envelope, dict)
            or envelope.get("format") != TRANSFER_FORMAT
            or envelope.get("format_version") != TRANSFER_VERSION
            or envelope.get("cipher") != "AES-256-GCM"
        ):
            raise OperationError("unsupported encrypted transfer format")
        kdf = envelope["kdf"]
        if not isinstance(kdf, dict) or kdf.get("name") != "scrypt":
            raise OperationError("unsupported transfer key derivation")
        salt = b64decode(str(kdf["salt"]), validate=True)
        nonce = b64decode(str(envelope["nonce"]), validate=True)
        ciphertext = b64decode(str(envelope["ciphertext"]), validate=True)
        plaintext = AESGCM(_derive_key(passphrase, salt)).decrypt(
            nonce, ciphertext, TRANSFER_FORMAT.encode()
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except OperationError:
        raise
    except (
        InvalidTag,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise OperationError(
            "archive authentication failed; the passphrase is wrong or the "
            "archive changed"
        ) from error
    if not isinstance(payload, dict):
        raise OperationError("the encrypted transfer payload is invalid")
    if (
        payload.get("format") != TRANSFER_FORMAT
        or payload.get("format_version") != TRANSFER_VERSION
    ):
        raise OperationError("unsupported encrypted transfer payload")
    return payload


def _validated_transfer_files(
    payload: dict[str, Any],
) -> tuple[tuple[Literal["local", "server"], Path, bytes], ...]:
    raw_logical_roots = payload.get("logical_roots")
    declared_roots: set[str] | None
    if raw_logical_roots is None:
        declared_roots = None
    elif (
        not isinstance(raw_logical_roots, list)
        or not raw_logical_roots
        or any(root not in {"local", "server"} for root in raw_logical_roots)
        or len(set(raw_logical_roots)) != len(raw_logical_roots)
    ):
        raise OperationError("the transfer logical root manifest is invalid")
    else:
        declared_roots = set(raw_logical_roots)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise OperationError("the transfer contains no state files")
    validated: list[tuple[Literal["local", "server"], Path, bytes]] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise OperationError("the transfer file manifest is invalid")
        root_name = item.get("root", "local")
        if root_name not in {"local", "server"}:
            raise OperationError("the transfer contains an unsafe logical root")
        relative_text = str(item.get("path", ""))
        relative = PurePosixPath(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".json"
            or f"{root_name}:{relative_text}" in seen
        ):
            raise OperationError("the transfer contains an unsafe state path")
        try:
            content = b64decode(str(item["content"]), validate=True)
        except (KeyError, ValueError, TypeError) as error:
            raise OperationError(
                "the transfer contains invalid state content"
            ) from error
        if len(content) != item.get("size") or sha256(content).hexdigest() != item.get(
            "sha256"
        ):
            raise OperationError("the transfer state checksum does not match")
        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OperationError("the transfer contains invalid JSON state") from error
        if not isinstance(data, dict):
            raise OperationError("transferred JSON state must be an object")
        seen.add(f"{root_name}:{relative_text}")
        validated.append((root_name, Path(*relative.parts), content))
    roots_with_files = {root_name for root_name, _, _ in validated}
    if declared_roots is None:
        if roots_with_files != {"local"}:
            raise OperationError("a legacy transfer can contain only local state")
    elif roots_with_files != declared_roots:
        raise OperationError("the transfer logical roots do not match its files")
    return tuple(validated)
