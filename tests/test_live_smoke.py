"""Opt-in live smoke tests; excluded from the default suite.

Each Available provider needs recorded evidence of one real completion.
Run with RECKONING_LIVE_SMOKE=1 and the provider's key in its documented
environment variable. These never run in CI by default.
"""

from __future__ import annotations

import os

import pytest

from reckoning.provider_adapters import (
    AdapterConfig,
    candidate_adapter_for,
    setup_adapter_for,
)

LIVE = os.environ.get("RECKONING_LIVE_SMOKE") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE, reason="opt-in live smoke tests; set RECKONING_LIVE_SMOKE=1"
)


def test_deepseek_live_completion() -> None:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        pytest.skip("DEEPSEEK_API_KEY is not set")
    adapter = setup_adapter_for("deepseek")
    result = adapter.verify(AdapterConfig(api_key=key))
    assert result.demo is False
    assert result.latency_ms > 0


def test_orcarouter_live_completion() -> None:
    key = os.environ.get("ORCAROUTER_API_KEY", "").strip()
    if not key:
        pytest.skip("ORCAROUTER_API_KEY is not set")
    adapter = setup_adapter_for("orcarouter")
    result = adapter.verify(AdapterConfig(api_key=key))
    assert result.demo is False


def test_openai_live_completion_candidate() -> None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        pytest.skip("OPENAI_API_KEY is not set")
    adapter = candidate_adapter_for("openai")
    result = adapter.verify(AdapterConfig(api_key=key))
    assert result.demo is False
    assert result.latency_ms > 0


def test_custom_openai_compatible_live_completion() -> None:
    base_url = os.environ.get("RECKONING_CUSTOM_BASE_URL", "").strip()
    model = os.environ.get("RECKONING_CUSTOM_MODEL", "").strip()
    if not base_url or not model:
        pytest.skip("RECKONING_CUSTOM_BASE_URL and RECKONING_CUSTOM_MODEL are not set")
    key = os.environ.get("RECKONING_CUSTOM_API_KEY", "").strip() or None
    adapter = setup_adapter_for("custom")
    result = adapter.verify(
        AdapterConfig(api_key=key, base_url=base_url, model=model)
    )
    assert result.demo is False


def test_ollama_live_completion_when_a_model_is_served() -> None:
    adapter = setup_adapter_for("ollama")
    try:
        models = adapter.discover_models(AdapterConfig())
    except Exception as error:  # noqa: BLE001 — any failure means skip
        pytest.skip(f"no Ollama runtime responded at the documented address: {error}")
    if not models:
        pytest.skip("no Ollama model is pulled")
    result = adapter.verify(AdapterConfig(model=models[0]))
    assert result.demo is False
