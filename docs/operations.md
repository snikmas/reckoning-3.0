# Install and recover a single-user Reckoning instance

Use `reckoning-ops` for setup, diagnosis, encrypted transfer, recovery, and
release checks. The command does not provide conversation, planning, or other
daily product work.

## Install the commands

Use Python 3.13 or later.

```bash
python3 -m pip install .
```

The package installs `reckoning` for product setup and the local web interface,
`reckoning-telegram` for the Telegram channel, and `reckoning-ops` for
operations.

## Use Reckoning through Telegram on this computer

Create a bot with [BotFather](https://t.me/BotFather). Keep the token private.
Anyone who has the token can control the bot.

Run the setup command:

```bash
reckoning setup
```

Choose **Telegram**, then choose **Fake**. The menu also shows Discord, WhatsApp,
Slack, DeepSeek, and OrcaRouter as future setup choices. Only Telegram and Fake
are available in this setup flow.

Fake uses deterministic local replies and does not need a provider API key. The
command then asks for the BotFather token and hides it while you type. It verifies
the bot and asks you to send a one-time `/connect` code in a private chat.
Reckoning saves the selected gateway, the selected provider, and the Telegram
configuration in `~/.config/reckoning/telegram.json`. The file has owner-only
permissions.

Start the bot:

```bash
reckoning-telegram
```

Keep the command running while you use the bot. Stop it with `Ctrl+C`. The bot
accepts ordinary messages, `/status`, `/help`, and
`/confirm <confirmation-id>`. It does not provide Control or operational
commands.

## Run a Telegram webhook on a personal server

Use the webhook command only when the installation has a public HTTPS endpoint.
Set the webhook secret, the internal gateway token, and the allowed Telegram
chat IDs in the process environment. Do not put these values in the repository.

```bash
export RECKONING_TELEGRAM_WEBHOOK_SECRET="replace-with-webhook-secret"
export RECKONING_TELEGRAM_GATEWAY_TOKEN="replace-with-gateway-token"
export RECKONING_TELEGRAM_ALLOWED_CHAT_IDS="123456789"
reckoning-telegram webhook --port 8081
```

Configure Telegram to send updates to `/telegram/webhook` and to include
`RECKONING_TELEGRAM_WEBHOOK_SECRET` as its secret token. The built-in server
binds to loopback. Put an authenticated TLS reverse proxy in front of it on a
personal server.

The allowed chat list is a comma-separated list. The webhook rejects other chat
IDs even when Telegram supplies the correct webhook secret.

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

For `personal-server` or `hybrid`, set a separate server root:

```bash
reckoning-ops setup \
  --placement hybrid \
  --server-data-dir /mnt/reckoning-server
```

Pass the same `--server-data-dir` to backup, export, diagnosis, web, and
Telegram commands. The local and server roots must not contain one another.
Each source category has its own subdirectory under the assigned node root.

Setup writes `instance.json` and runs a disposable continuity check with the
deterministic fake provider. The check proposes and confirms a decision,
resumes the decision, records an outcome, and resumes it after a restart. The
check does not add synthetic records to the new instance.

Setup builds the local and optional server roots in sibling staging
directories. It moves them into place only after every required file is ready.
If setup fails, it removes the staging directories and restores any empty roots
that existed before setup.

The profiles have these rules:

- `local` keeps all source categories on the local device.
- `personal-server` places the single-user instance on the personal server.
- `hybrid` keeps private personal context and confirmed state local. It permits
  only approved remote-source data on the personal server.

Every profile requires exact or standing permission for an external write. If
required context is unavailable, Reckoning blocks the work or declares limited
mode. The runtime probes each node marker and its filesystem access. Web and
Telegram report the same limited-mode notice when a node becomes unavailable.
The built-in server binds only to loopback because it does not provide
public authentication. Use an SSH tunnel for remote access. Setup does not
create hosted tenancy or a public administration dashboard.

Setup also creates `release-evidence.json` with every public-release gate set to
`false`. Its final report therefore remains `BLOCKED` until clean-install,
recovery, privacy, deletion, external-action, repeated-value, and
developer-independent continuity evidence has actually been recorded.

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

For a personal-server or hybrid instance, pass the configured server root:

```bash
reckoning-ops backup \
  --server-data-dir /mnt/reckoning-server \
  --output "$PWD/reckoning-backup.reckoning"
```

The command rejects a missing or different server root. It also rejects local
and server roots that overlap.

## Restore into a clean installation

Restore only into a missing or empty data directory.

```bash
reckoning-ops restore \
  --input "$PWD/reckoning-backup.reckoning" \
  --data-dir "$PWD/restored-instance"
```

If the archive contains server state, supply a new server root:

```bash
reckoning-ops restore \
  --input "$PWD/reckoning-backup.reckoning" \
  --data-dir "$PWD/restored-instance" \
  --server-data-dir /mnt/restored-reckoning-server
```

The command authenticates the archive and validates every file before it
creates the target. A wrong passphrase, changed archive, unsafe path, invalid
JSON file, or checksum mismatch stops the restore. A failed restore does not
replace an existing instance. For a two-root restore, a failed commit restores
both empty target directories and their original permissions.

Restore replaces the archived server-root marker in `instance.json` with the
new absolute server path. The restored instance does not retain the old path.

After restore, diagnose the new instance.

```bash
reckoning-ops diagnose \
  --data-dir "$PWD/restored-instance" \
  --server-data-dir /mnt/restored-reckoning-server
```

Omit `--server-data-dir` when you restore a local-only archive.

## Export or migrate a transfer

Create an encrypted export with the same preservation contract as a backup.

```bash
reckoning-ops export --output "$PWD/reckoning-export.reckoning"
```

For a personal-server or hybrid instance, also pass the configured
`--server-data-dir`.

Validate and rewrite an archive in the current transfer format.

```bash
reckoning-ops migrate \
  --input "$PWD/reckoning-export.reckoning" \
  --output "$PWD/reckoning-migrated.reckoning"
```

Both commands read the passphrase from `RECKONING_TRANSFER_PASSPHRASE`.
Migration preserves the logical roots and the portable server-root marker.

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
