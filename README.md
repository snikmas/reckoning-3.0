# Reckoning

Reckoning is an early-stage, local-first continuity agent for one person. The
current runnable flow helps you propose, correct, confirm, revisit, and review
an important decision across restarts. The codebase also contains broader
services for goals, plans, research, routines, and personal context.

The default setup needs no API key and makes no model-provider request until
you choose a real provider. It uses a deterministic fake provider so you can
inspect the product safely. Connect OrcaRouter or DeepSeek when you want real
model responses.

## Quickstart

You need Python 3.13 or newer. From the repository root:

```bash
pipx install .        # or: uv tool install .
reckoning setup       # guided first run, ending in a real first conversation
reckoning             # talk to your agent in the terminal
reckoning web         # open the web interface at http://127.0.0.1:8000
reckoning gateway     # optional: run the messaging channels you configured
```

If you cannot use pipx or uv, `python3 -m pip install .` works too, inside a
virtual environment when your Python installation does not allow system-wide
packages.

## Commands

`reckoning --help` prints this same table, grouped the same way.

| Group | Command | What it does |
| --- | --- | --- |
| Run | `reckoning` | Start a Terminal conversation. |
| Run | `reckoning web` | Run the local Web interface. |
| Run | `reckoning gateway` | Run every configured channel (Telegram today). |
| Setup | `reckoning setup` | Guided setup, plus status and editing on a configured installation. |
| Setup | `reckoning doctor` | Report installation health and how to fix problems. |
| Setup | `reckoning reset` | Remove installation state after an explicit preview. |
| Data | `reckoning backup` | Create an encrypted recovery archive. |
| Data | `reckoning restore` | Restore into a clean installation. |
| Data | `reckoning export` | Create an encrypted transfer export. |
| Data | `reckoning migrate` | Migrate an encrypted transfer to the current format. |

## Which command when

- **Talk to your agent** → `reckoning`
- **Use the browser interface** → `reckoning web`
- **Install, inspect, or edit configuration** → `reckoning setup`
- **Check whether the installation is healthy** → `reckoning doctor`
- **Check a provider key live, right now** → `reckoning doctor --ping`
- **Talk over Telegram** → `reckoning gateway`
- **Remove an installation** → `reckoning reset`
- **Protect the instance before maintenance** → `reckoning backup`
- **Recover onto a clean machine** → `reckoning restore`
- **Move state to another Reckoning instance** → `reckoning export`
- **Upgrade an old transfer archive** → `reckoning migrate`

## Setup

`reckoning setup` runs one guided journey with five visible sections: AI
provider and model, Ways to use Reckoning, Agent style, About you, and Review.
Review shows every choice — provider, model, Agent style, About you status,
interface status, Telegram state, and local-first storage — and every row can
be edited. After Review, Reckoning asks one real first question and answers it
with the selected provider and Desired-self persona while tools, external
writes, routines, and personal-context confirmation stay disabled. Only an
accepted exchange becomes durable. Setup only reports success after it reopens
the installed state and finds that accepted exchange.

Re-running `reckoning setup` on a configured installation opens the
installation status view instead: provider and model, verification freshness,
Agent style, About you proposals, storage, interfaces, Telegram, and Gateway
runtime state. Opening the view makes no network request; the explicit **Verify
all** action discloses any possible paid use before it runs. Every section can
be edited in place.

For scripts and CI, every guided answer has a flag or environment variable:

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

Run `reckoning setup` and choose **DeepSeek**, **OrcaRouter**, or another
supported provider in the first section. Setup shows detected usable access
first without revealing any secret value, then the supported catalog in stable
order, with Demo separated at the bottom. It asks for the provider's API key
with hidden input, shows the selected provider, endpoint domain, and any
possible paid charge, and then sends one small fixed, non-personal test
request. The key never echoes to the terminal. If verification fails, setup
offers Retry, Edit, Back, Save for later (the credential stays inactive), or
Exit. A searchable model screen follows, with the recommended model
preselected and manual entry under Advanced.

Keys are saved in `~/.config/reckoning/provider.json` with owner-only
permissions. The file can hold one key per provider plus the default-provider
marker. Keys are never written to the instance data. Credential files saved by
older versions migrate to the multi-provider format automatically.

`reckoning`, `reckoning web`, and `reckoning gateway` use the installed
provider automatically; no `.env` file or `--provider` flag is needed. You can
still override with environment variables or an ignored `.env` file in the
repository root; those values take precedence over the saved credential. See
[operations](docs/operations.md) for the variable names.

## Storage and access limits

The default instance stores live state under `~/.local/state/reckoning`.
Transactional records use the placement-root SQLite database. Small JSON files
hold configuration and compatibility pointers. Use `reckoning backup` to create
one encrypted archive of both formats.

The built-in web server accepts loopback addresses only and has no public
login. Do not expose it directly to the internet. For remote access to a
personal server, use an SSH tunnel. Every browser mutation requires the
configured Host, an allowed Origin when the browser sends one, and a
session-bound form token. See
[web access](docs/operations.md#use-the-web-interface-safely) for the supported
origin and private-access contract.

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
