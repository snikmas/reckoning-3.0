# Reckoning

Reckoning currently provides a small local web interface for sending a message
through its application boundary. The repository also contains the first
continuity slice: a reckoning can be proposed, corrected, confirmed, explained,
resumed after restart, and linked to a dated outcome check-in.

The local model is a deterministic fake. Recent conversation messages remain in
memory. Confirmed continuity records use an atomic JSON file so that they survive
a restart.

## Run locally

Reckoning requires Python 3.13 and has no runtime dependencies.

```bash
PYTHONPATH=src python3 -m reckoning
```

Open `http://127.0.0.1:8000`. Stop the server with `Ctrl+C`.

The local application stores continuity data in
`~/.local/state/reckoning/continuity.json`. The file contains plain JSON and is
not encrypted by Reckoning. The current web page exposes conversation only;
the continuity operations are available through `ReckoningApplication` while
the interface remains under development.

## Run tests

Install pytest 9 in a development environment, then run:

```bash
PYTHONPATH=src python3 -m pytest
```

The tests use fake time, model, placement, connectors, and temporary storage.
They do not call an external model or service.
