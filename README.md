# Reckoning

Reckoning currently provides a small local web interface for sending a message
through its application boundary. The repository also contains the first
continuity slice: a reckoning can be proposed, corrected, confirmed, explained,
resumed after restart, and linked to a dated outcome check-in.

The default local model is a deterministic fake. The same application boundary
also supports OrcaRouter and DeepSeek through their OpenAI-compatible APIs.
Recent conversation messages remain in memory. Confirmed continuity records use
an atomic JSON file so that they survive a restart.

## Run locally

Reckoning requires Python 3.13. Install the package and its encrypted-transfer
dependency before running it.

```bash
python3 -m pip install .
```

```bash
PYTHONPATH=src python3 -m reckoning
```

Open `http://127.0.0.1:8000`. Stop the server with `Ctrl+C`.

To run with OrcaRouter, add these values to the repository's ignored `.env` file.

```dotenv
ORCAROUTER_API_KEY=...
MODEL="orcarouter/auto"
BASE_URL="https://api.orcarouter.ai/v1"
```

Then start Reckoning. The presence of `ORCAROUTER_API_KEY` selects OrcaRouter.

```bash
PYTHONPATH=src python3 -m reckoning
```

If OrcaRouter returns `model_access_denied`, open the API key in the OrcaRouter
console and allow the model named by `MODEL`. Router aliases such as
`orcarouter/auto` must be allowed explicitly for that key.

To run with DeepSeek, add these values to `.env`:

```dotenv
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL="your-model-name"
BASE_DEEPSEEK_URL="https://api.deepseek.com"
```

Start Reckoning with the DeepSeek provider:

```bash
PYTHONPATH=src python3 -m reckoning --provider deepseek
```

For backward compatibility, DeepSeek also accepts `MODEL` and `BASE_URL`.
`DEEPSEEK_MODEL` and `BASE_DEEPSEEK_URL` take precedence. Reckoning reads only
the documented provider values from `.env` and does not copy API keys into its
data files.

Each attempted provider run records the provider, model calls, latency, retries,
token usage, and failure status in the application run repository. Token usage is
the billable unit; Reckoning does not hard-code provider prices.

The local application stores continuity data in
`~/.local/state/reckoning/continuity.json`. Live state files contain plain JSON;
encrypted backup and restore are available through `reckoning-ops`. The web
interface opens with Simon for a new user and the Home command center for a
returning user. Plan, Review, and locally restricted Control areas remain
separate task-based views.

## Run tests

Install pytest 9 in a development environment, then run:

```bash
PYTHONPATH=src python3 -m pytest
```

The tests use fake time, model, placement, connectors, and temporary storage.
They do not call an external model or service.

## Implemented domain services

The Python API now includes services for private-slice trials, multilingual
personal context, context lifecycle and maintenance, directions and goals,
planning horizons, bounded research, goal forecasts, forecast revisions,
routines, watches, selective briefings, connector governance, external-content
isolation, placement, channel continuation, bounded delegation, and persona
selection. These services keep proposed and confirmed state separate where user
acceptance matters. See `docs/operations.md` for setup, diagnosis, encrypted
transfer, and recovery commands.
