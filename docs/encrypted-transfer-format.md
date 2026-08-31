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
| `files` | State-file manifest |

Each item in `files` contains a relative `.json` path, a byte count, a SHA-256
digest, and Base64-encoded file bytes. Restore writes those bytes unchanged.
This mechanism preserves identifiers, versions, provenance, permissions,
routines, receipts, and suppression markers without interpreting or weakening
their domain rules.

## Restore validation

Restore accepts only authenticated version 1 archives. Each manifest path must
be unique, relative, free of `..`, and end in `.json`. The byte count and digest
must match. Each decoded file must contain a JSON object.

Restore stages every validated file beside the target directory. It then
replaces a missing or empty target with the staged directory. A non-empty target
always blocks restore.

## File permissions

The archive and each restored state file use mode `0600`. Setup and restore use
mode `0700` for the data directory.
