# Reckoning

Reckoning is an early-stage, local-first continuity agent for one person. The
current runnable flow helps you propose, correct, confirm, revisit, and review
an important decision across restarts. The codebase also contains broader
services for goals, plans, research, routines, and personal context.

The default setup needs no API key and makes no model-provider request. It uses
a deterministic fake provider so that you can inspect the product safely. You
can connect OrcaRouter or DeepSeek when you want real model responses.

## Start a local instance

You need Python 3.13 or newer. From the repository root, run:

```bash
python3 -m pip install .
reckoning-ops setup
reckoning
```

Open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`.

`reckoning-ops setup` creates a local single-user instance, selects the Simon
persona, and checks the core continuity flow. It stores the instance under
`~/.local/state/reckoning` by default. You only need to run setup once.

If your Python installation does not allow system-wide packages, create a
virtual environment first:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

After activating the virtual environment, run the three commands from the start
section.

## What you can use

The browser interface has separate views for conversation, current priorities,
planning, review, and local operational status. Confirmed continuity records
survive a restart. Recent unconfirmed conversation messages stay in memory only.

The Python code also contains domain services for:

- personal context with correction and deletion history;
- directions, goals, plans, and check-ins;
- research, forecasts, routines, watches, and briefings;
- connector permissions, external-content isolation, and data placement;
- bounded delegation and persona selection.

Some of these services are available through the Python API but do not yet have
a complete browser workflow.

## Connect Telegram

The browser interface does not require Telegram. To connect a Telegram bot, run:

```bash
reckoning setup
```

Choose **Telegram**, then choose a provider. **Fake** does not need a
model-provider API key. **DeepSeek** and **OrcaRouter** ask for the provider's
API key during setup (see the next section). The setup command then asks for
your BotFather token and pairs one private chat. Start the connected bot with:

```bash
reckoning-telegram
```

Keep the command running while you use the bot. See
[operations](docs/operations.md) for webhook and personal-server setup.

## Choose a model provider

You do not need this section for the default fake provider.

### Enter the API key during setup

Run the setup command:

```bash
reckoning setup
```

Choose **Telegram**, then choose **DeepSeek** or **OrcaRouter** in the provider
menu. Setup asks for the provider's API key with hidden input — the key is
never echoed to the terminal. It verifies the key against the provider and
refuses to save a key that fails verification. A verified key is saved in
`~/.config/reckoning/provider.json` with owner-only permissions. The key is
never written to the instance data.

Then start the interface you use:

```bash
reckoning
```

or:

```bash
reckoning-telegram
```

Both commands pick up the saved key automatically; no `.env` file is needed.
To switch providers or replace a key, re-run the full setup:

```bash
reckoning setup
```

Answer `y` when setup asks to replace the existing Telegram configuration.

### Use environment variables instead

You can still configure a provider with environment variables or an ignored
`.env` file in the repository root. Those values take precedence over the
saved credential. To use OrcaRouter:

```dotenv
ORCAROUTER_API_KEY=replace-with-your-key
MODEL=orcarouter/auto
```

Start the server with an explicit provider:

```bash
reckoning --provider orcarouter
```

If OrcaRouter returns `model_access_denied`, allow the value of `MODEL` for that
API key in the OrcaRouter console. This also applies to router aliases such as
`orcarouter/auto`.

To use DeepSeek, put these values in `.env`:

```dotenv
DEEPSEEK_API_KEY=replace-with-your-key
DEEPSEEK_MODEL=deepseek-v4-flash
```

Then start the server:

```bash
reckoning --provider deepseek
```

The same flags work for the Telegram runtime:

```bash
reckoning-telegram --provider deepseek
```

Both providers use their official API URL by default. Use `BASE_URL` for
OrcaRouter or `BASE_DEEPSEEK_URL` for DeepSeek only when you need to override
that default.

Reckoning reads only its documented provider variables from `.env`. It does not
write API keys to the instance data. Each provider attempt records the provider,
model calls, latency, retries, token usage, and failure status.

## Understand the storage and access limits

The default instance stores live state as plain JSON under
`~/.local/state/reckoning`. Use `reckoning-ops backup` to create an encrypted
archive.

The built-in web server accepts loopback addresses only and has no public login.
Do not expose it directly to the internet. For remote access to a personal
server, use an SSH tunnel.

Reckoning supports `local`, `personal-server`, and `hybrid` placement profiles.
The local profile is the default. See [operations](docs/operations.md) for
placement, diagnosis, encrypted backup, restore, and migration commands.

## Diagnose a local instance

Run:

```bash
reckoning-ops diagnose
```

The command checks the instance configuration and JSON state files. It reports
`healthy` when every required file is valid.

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
