# Reckoning

Reckoning currently provides a small local web interface for sending a message
through its application boundary. The model and storage implementations are
deterministic in-memory fakes, so this version is intended for development and
testing.

## Run locally

Reckoning requires Python 3.13 and has no runtime dependencies.

```bash
PYTHONPATH=src python3 -m reckoning
```

Open `http://127.0.0.1:8000`. Stop the server with `Ctrl+C`.

## Run tests

Install pytest 9 in a development environment, then run:

```bash
PYTHONPATH=src python3 -m pytest
```

The test uses fake time, model, placement, connector, and storage components.
It does not call an external model or service.
