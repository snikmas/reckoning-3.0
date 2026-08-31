from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from reckoning.application import PersonaSettings, PlacementState, create_local_application
from reckoning.interfaces import NodeName, PlacementPolicy, SourcePlacement
from reckoning.json_store import atomic_write_json, read_json
from reckoning.personas import (
    DEFAULT_PERSONAS,
    JsonFilePersonaRepository,
    PersonaDefinition,
    PersonaService,
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


def load_installation_runtime(
    data_dir: Path,
    *,
    local_node_available: bool | None = None,
    server_node_available: bool | None = None,
) -> InstallationRuntime:
    """Load the setup choices that must govern every public interface."""
    instance = read_json(data_dir / "instance.json", default={})
    profile = instance.get("placement_profile")
    if profile not in {"local", "personal-server", "hybrid"}:
        raise OperationError("instance.json has no supported placement profile")

    try:
        persona = PersonaService(
            JsonFilePersonaRepository(data_dir / "personas.json")
        ).active().to_application_settings()
    except (KeyError, LookupError, RuntimeError, ValueError) as error:
        raise OperationError("the selected persona configuration is invalid") from error

    local_available = (
        profile in {"local", "hybrid"}
        if local_node_available is None
        else local_node_available
    )
    server_available = (
        profile in {"personal-server", "hybrid"}
        if server_node_available is None
        else server_node_available
    )
    interface_placement = _runtime_placement_policy(
        profile,
        local_node_available=local_available,
        server_node_available=server_available,
    )
    primary_node = "local" if profile in {"local", "hybrid"} else "personal-server"
    return InstallationRuntime(
        persona=persona,
        application_placement=PlacementState(
            processing_location=primary_node,
            storage_location=primary_node,
            local_node_available=local_available,
        ),
        interface_placement=interface_placement,
    )


def setup_instance(
    data_dir: Path,
    placement: PlacementProfile,
    persona: PersonaDefinition | None = None,
) -> dict[str, Any]:
    if data_dir.exists() and any(data_dir.iterdir()):
        raise OperationError("setup requires a new or empty data directory")
    _check_core_continuity_loop()
    selected_persona = persona or DEFAULT_PERSONAS[0]
    configuration: dict[str, Any] = {
        "schema_version": 1,
        "instance_type": "single-user",
        "hosted_tenancy": False,
        "public_admin": False,
        "placement_profile": placement,
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
        "operational_access": (
            "loopback-only"
            if placement == "local"
            else "loopback-private-network-or-ssh-tunnel"
        ),
        "installation_check": {
            "core_continuity_loop": "passed",
            "provider": "deterministic-fake",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(data_dir / "instance.json", configuration)
    persona_service = PersonaService(JsonFilePersonaRepository(data_dir / "personas.json"))
    if selected_persona in DEFAULT_PERSONAS:
        persona_service.select(selected_persona.id)
    else:
        persona_service.author_and_select(selected_persona)
    data_dir.chmod(0o700)
    return configuration


def diagnose(data_dir: Path) -> tuple[str, ...]:
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
    return (
        "status: healthy",
        f"instance: {instance.get('instance_type', 'unknown')}",
        f"placement: {instance.get('placement_profile', 'unknown')}",
        f"valid state files: {len(files)}",
    )


def create_transfer(
    data_dir: Path,
    output: Path,
    passphrase: str,
    *,
    kind: TransferKind,
) -> int:
    files = _state_files(data_dir)
    if not files:
        raise OperationError("no JSON state files were found")
    entries: list[dict[str, Any]] = []
    for path in files:
        _read_state_file(path)
        content = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(data_dir).as_posix(),
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
        "files": entries,
    }
    _write_encrypted_payload(output, payload, passphrase)
    return len(entries)


def restore_transfer(archive: Path, data_dir: Path, passphrase: str) -> int:
    if data_dir.exists() and any(data_dir.iterdir()):
        raise OperationError("restore requires a clean, empty data directory")
    payload = _read_encrypted_payload(archive, passphrase)
    files = _validated_transfer_files(payload)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{data_dir.name}.restore-", dir=data_dir.parent))
    try:
        for relative_path, content in files:
            target = staging / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o600)
        os.replace(staging, data_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
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
    local_node_available: bool,
    server_node_available: bool,
) -> PlacementPolicy:
    private_node: NodeName = "server" if profile == "personal-server" else "local"
    remote_source_node: NodeName = "local" if profile == "local" else "server"
    private_remote = private_node == "server"
    return PlacementPolicy(
        profile=profile,
        categories=(
            SourcePlacement(
                category="personal-context",
                sensitivity="private",
                storage_node=private_node,
                processing_node=private_node,
                remote_approved=private_remote,
            ),
            SourcePlacement(
                category="confirmed-state",
                sensitivity="private",
                storage_node=private_node,
                processing_node=private_node,
                remote_approved=private_remote,
            ),
            SourcePlacement(
                category="approved-remote-sources",
                sensitivity="public",
                storage_node=remote_source_node,
                processing_node=remote_source_node,
            ),
        ),
        local_node_available=local_node_available,
        server_node_available=server_node_available,
    )


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
) -> tuple[tuple[Path, bytes], ...]:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise OperationError("the transfer contains no state files")
    validated: list[tuple[Path, bytes]] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise OperationError("the transfer file manifest is invalid")
        relative_text = str(item.get("path", ""))
        relative = PurePosixPath(relative_text)
        if (
            not relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".json"
            or relative_text in seen
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
        seen.add(relative_text)
        validated.append((Path(*relative.parts), content))
    return tuple(validated)
