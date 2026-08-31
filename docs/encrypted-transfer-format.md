# Encrypted transfer format

Reckoning backup, export, restore, and migration commands use
`reckoning-encrypted-transfer` format version 1.

## Envelope

The archive is a UTF-8 JSON object with these fields:

| Field | Value |
| --- | --- |
| `format` | `reckoning-encrypted-transfer` |
| `format_version` | `1` |
| `cipher` | `AES-256-GCM` |
| `kdf.name` | `scrypt` |
| `kdf.n` | `16384` |
| `kdf.r` | `8` |
| `kdf.p` | `1` |
| `kdf.salt` | Base64-encoded 16-byte random salt |
| `nonce` | Base64-encoded 12-byte random nonce |
| `ciphertext` | Base64-encoded authenticated ciphertext and tag |

The key derivation function derives a 32-byte key from
`RECKONING_TRANSFER_PASSPHRASE`. AES-GCM uses
`reckoning-encrypted-transfer` as authenticated additional data. Each command
creates a new salt and nonce.

## Encrypted payload

The ciphertext contains one UTF-8 JSON object with these fields:

| Field | Meaning |
| --- | --- |
| `format` | Transfer format name |
| `format_version` | Transfer format version |
| `transfer_kind` | `backup`, `export`, or `migration` |
| `created_at` | UTC archive creation time |
| `migrated_at` | UTC migration time, present after migration |
| `logical_roots` | The `local` root, or both the `local` and `server` roots |
| `files` | State-file manifest |

Each item in `files` contains a logical root, a relative `.json` path, a byte
count, a SHA-256 digest, and Base64-encoded file bytes. The logical root is
either `local` or `server`. Paths are relative to that root.

Before export, Reckoning replaces the absolute server path in `instance.json`
with a portable marker. Restore replaces the marker with the destination server
path. Restore writes every other state file without changing its contents. This
mechanism preserves identifiers, versions, provenance, permissions, routines,
receipts, and suppression markers.

Version 1 local-only archives created before logical roots were added remain
valid. Restore treats a missing item root as `local`. A legacy archive cannot
contain server state.

## Restore validation

Restore accepts only authenticated version 1 archives. Each manifest path must
be unique, relative, free of `..`, and end in `.json`. The byte count and digest
must match. Each decoded file must contain a JSON object. For a current archive,
the declared logical roots must match the roots used by its file entries.

Restore requires a destination server root when the archive contains server
state. The local and server roots must be separate. Neither root can contain the
other. Restore stages every validated file beside its destination root and then
commits both roots. A non-empty target blocks restore. If either commit fails,
restore removes the partial result and recreates any empty target directory with
its original permissions.

## File permissions

The archive and each restored state file use mode `0600`. Setup and restore use
mode `0700` for newly created local and server data directories.
