# Install and recover a single-user Reckoning instance

Use `reckoning-ops` for setup, diagnosis, encrypted transfer, recovery, and
release checks. The command does not provide conversation, planning, or other
daily product work.

## Install the commands

Use Python 3.13 or later.

```bash
python3 -m pip install .
```

The package installs `reckoning` for the local web interface and
`reckoning-ops` for operations.

## Create an instance

Choose one placement profile.

```bash
reckoning-ops setup --placement local
```

Setup selects Simon by default. Select the other bundled persona with
`--persona steady`. To author an original persona, use `--persona original`
with `--persona-id`, `--persona-name`, and the six style options shown by
`reckoning-ops setup --help`. Persona options change style only. They cannot
change protected privacy, authority, deletion, or safety rules.

The default data directory is `~/.local/state/reckoning`. Use `--data-dir` to
select a different directory.

Setup writes `instance.json` and runs a disposable continuity check with the
deterministic fake provider. The check proposes and confirms a decision,
resumes the decision, records an outcome, and resumes it after a restart. The
check does not add synthetic records to the new instance.

The profiles have these rules:

- `local` keeps all source categories on the local device.
- `personal-server` places the single-user instance on the personal server.
- `hybrid` keeps private personal context and confirmed state local. It permits
  only approved remote-source data on the personal server.

Every profile requires exact or standing permission for an external write. If
required context is unavailable, Reckoning blocks the work or declares limited
mode. Administration stays on loopback, a private authenticated network, or an
SSH tunnel. Setup does not create hosted tenancy or a public administration
dashboard.

## Diagnose an instance

```bash
reckoning-ops diagnose
```

The command validates each JSON state file. It reports `healthy` only when the
instance configuration exists and every state file contains a JSON object.

## Create an encrypted backup

Set the passphrase without putting it in the command history.

```bash
read -rsp "Transfer passphrase: " RECKONING_TRANSFER_PASSPHRASE
export RECKONING_TRANSFER_PASSPHRASE
reckoning-ops backup --output "$PWD/reckoning-backup.reckoning"
```

Store the passphrase separately from the archive. The command cannot recover a
lost passphrase.

## Restore into a clean installation

Restore only into a missing or empty data directory.

```bash
reckoning-ops restore \
  --input "$PWD/reckoning-backup.reckoning" \
  --data-dir "$PWD/restored-instance"
```

The command authenticates the archive and validates every file before it
creates the target. A wrong passphrase, changed archive, unsafe path, invalid
JSON file, or checksum mismatch stops the restore. A failed restore does not
replace an existing instance.

After restore, diagnose the new instance.

```bash
reckoning-ops diagnose --data-dir "$PWD/restored-instance"
```

## Export or migrate a transfer

Create an encrypted export with the same preservation contract as a backup.

```bash
reckoning-ops export --output "$PWD/reckoning-export.reckoning"
```

Validate and rewrite an archive in the current transfer format.

```bash
reckoning-ops migrate \
  --input "$PWD/reckoning-export.reckoning" \
  --output "$PWD/reckoning-migrated.reckoning"
```

Both commands read the passphrase from `RECKONING_TRANSFER_PASSPHRASE`.

## Check public-release evidence

Create a JSON evidence file. A `true` value means that recorded evidence exists
for that gate. Do not set a value to `true` based only on the setup self-check.

```json
{
  "schema_version": 1,
  "repeated_value": false,
  "clean_install": false,
  "recovery": false,
  "privacy": false,
  "deletion": false,
  "external_action": false,
  "core_continuity_without_developer": false
}
```

Run the report.

```bash
reckoning-ops diagnose --release-evidence release-evidence.json
```

The command prints `BLOCKED` and exits with status 2 while any gate lacks
evidence. It prints `READY` only when all seven gates are `true`.
