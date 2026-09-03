# Reckoning

Reckoning is an early-stage, local-first continuity agent for one person. The
current runnable flow helps you propose, correct, confirm, revisit, and review
an important decision across restarts. The codebase also contains broader
services for goals, plans, research, routines, and personal context.

The default setup needs no API key and makes no model-provider request until
you choose a real provider. It uses a deterministic fake provider so you can
inspect the product safely. Connect OrcaRouter or DeepSeek when you want real
model responses.

## Commands changed in 0.2.0

0.2.0 installs exactly one binary, `reckoning`. The 0.1.x binaries are gone —
no shims, no deprecation period. Your data, credentials, and configuration
files are untouched.

| 0.1.x | 0.2.0 |
| --- | --- |
| `reckoning-ops setup` | `reckoning setup` |
| `reckoning-ops diagnose` | `reckoning doctor` |
| `reckoning-ops backup` | `reckoning backup` |
| `reckoning-ops restore` | `reckoning restore` |
| `reckoning-ops export` | `reckoning export` |
| `reckoning-ops migrate` | `reckoning migrate` |
| `reckoning-telegram` | `reckoning gateway` |
| `reckoning` (web interface) | `reckoning` (unchanged) |

## Quickstart

You need Python 3.13 or newer. From the repository root:

```bash
pipx install .        # or: uv tool install .
reckoning setup       # first-run wizard; proves the core continuity loop
reckoning             # web interface at http://127.0.0.1:8000
reckoning gateway     # optional: run the channels you configured
```

If you cannot use pipx or uv, `python3 -m pip install .` works too, inside a
virtual environment when your Python installation does not allow system-wide
packages.

## Commands

`reckoning --help` prints this same table, grouped the same way.

| Group | Command | What it does |
| --- | --- | --- |
| Run | `reckoning` | Run the web interface. |
| Run | `reckoning gateway` | Run every configured channel (Telegram today). |
| Setup | `reckoning setup` | First-run setup wizard; re-run to manage providers and channels. |
| Setup | `reckoning doctor` | Report installation health and how to fix problems. |
| Data | `reckoning backup` | Create an encrypted recovery archive. |
| Data | `reckoning restore` | Restore into a clean installation. |
| Data | `reckoning export` | Create an encrypted transfer export. |
| Data | `reckoning migrate` | Migrate an encrypted transfer to the current format. |

## Which command when

- **Start the product** → `reckoning`
- **Install or repair an installation** → `reckoning setup`
- **Check whether the installation is healthy** → `reckoning doctor`
- **Check a provider key live, right now** → `reckoning doctor --ping`
- **Talk over Telegram** → `reckoning gateway`
- **Protect the instance before maintenance** → `reckoning backup`
- **Recover onto a clean machine** → `reckoning restore`
- **Move state to another Reckoning instance** → `reckoning export`
- **Upgrade an old transfer archive** → `reckoning migrate`

## Setup

`reckoning setup` runs a five-step wizard: instance (data directory and
placement policy), desired-self persona preset (Simon, Steady, or an original
you author), model provider, channels (Telegram, skippable), and an automatic
proof step. The proof step sends one round-trip through the chosen provider,
creates and confirms a continuity record, and re-opens the store to prove the
record survives a restart. Setup only reports success when the proof passes;
otherwise it reports the setup as incomplete and points at `reckoning doctor`.

Re-running `reckoning setup` on a configured installation opens a management
menu instead: add a provider, replace a key, remove a provider, or change the
default. Pressing Enter at the key prompt keeps the current key.

For scripts and CI, every wizard answer has a flag or environment variable:

```bash
DEEPSEEK_API_KEY=replace-with-your-key reckoning setup \
  --non-interactive \
  --placement local \
  --persona simon \
  --provider deepseek
```

Secrets are read from environment variables only — never from command-line
flags. Non-interactive setup skips channel pairing, and any missing value
fails with a clear message instead of prompting.

## Choose a model provider

You do not need this section for the default fake provider.

Run `reckoning setup` and choose **DeepSeek** or **OrcaRouter** in the provider
step. Setup asks for the provider's API key with hidden input and verifies the
key against the provider. The key never echoes to the terminal. If verification
fails, setup offers to re-enter the key, save it unverified, or abort without
saving anything. After each provider, setup asks whether to add another one,
then asks which configured provider is the default.

Keys are saved in `~/.config/reckoning/provider.json` with owner-only
permissions. The file can hold one key per provider plus the default-provider
marker. Keys are never written to the instance data. Credential files saved by
older versions migrate to the multi-provider format automatically.

`reckoning` and `reckoning gateway` use the saved default provider
automatically; no `.env` file or `--provider` flag is needed. You can still
override with environment variables or an ignored `.env` file in the
repository root; those values take precedence over the saved credential. See
[operations](docs/operations.md) for the variable names.

## Storage and access limits

The default instance stores live state as plain JSON under
`~/.local/state/reckoning`. Use `reckoning backup` to create an encrypted
archive.

The built-in web server accepts loopback addresses only and has no public
login. Do not expose it directly to the internet. For remote access to a
personal server, use an SSH tunnel.

Reckoning supports `local`, `personal-server`, and `hybrid` placement profiles.
The local profile is the default. See [operations](docs/operations.md) for
placement, health checks, encrypted backup, restore, and migration details.

## Check an installation

Run:

```bash
reckoning doctor
```

The report covers the instance configuration and JSON state files, rendered as
a table with a fix pointer for each problem. The default report makes no
network calls; add `--ping` to verify the saved provider key live.

## Develop and test

Install Reckoning in editable mode with its test dependency:

```bash
python3 -m pip install -e ".[test]"
python3 -m pytest
```

The test suite uses fake time, model, placement, connectors, and temporary
storage. It does not call an external model or service.

For implementation details, read the
[architecture overview](docs/architecture/vertical-slices-06-15.md) and the
[encrypted transfer format](docs/encrypted-transfer-format.md).
