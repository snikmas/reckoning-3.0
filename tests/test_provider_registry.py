from __future__ import annotations

import pytest

from reckoning.provider_registry import (
    GROUP_ORDER,
    PROVIDER_REGISTRY,
    REGISTRY_VERSION,
    available_providers,
    coming_soon_providers,
    find_provider,
    providers_in_group,
)


def test_the_registry_is_versioned_and_bundled() -> None:
    assert REGISTRY_VERSION >= 1
    assert PROVIDER_REGISTRY


def test_provider_ids_are_unique() -> None:
    ids = [item.id for item in PROVIDER_REGISTRY]
    assert len(ids) == len(set(ids))


def test_available_providers_are_grouped_and_working() -> None:
    available = {item.id for item in available_providers()}
    assert {"fake", "deepseek", "orcarouter"} <= available
    assert {"ollama", "lmstudio", "vllm", "llamacpp"} <= available
    # Custom stays coming-soon until its opt-in real smoke test passes.
    assert "custom" not in available
    for item in available_providers():
        assert item.availability == "available"


def test_coming_soon_providers_cover_the_product_horizon() -> None:
    coming_soon = {item.id for item in coming_soon_providers()}
    assert {
        "openai",
        "custom",
        "anthropic",
        "google-gemini",
        "xai",
        "mistral",
        "groq",
        "together-ai",
        "fireworks-ai",
        "kimi",
        "qwen",
        "minimax",
        "openrouter",
        "vercel-ai-gateway",
        "cloudflare-ai-gateway",
        "litellm",
    } <= coming_soon
    for item in coming_soon_providers():
        assert item.availability == "coming-soon"


def test_groups_follow_the_direct_gateway_local_custom_order() -> None:
    assert GROUP_ORDER == ("direct", "gateway", "local", "custom", "developer")
    assert {item.id for item in providers_in_group("direct")} >= {"deepseek"}
    assert {item.id for item in providers_in_group("gateway")} >= {"orcarouter"}
    assert find_provider("fake").group == "developer"


def test_cloud_providers_pin_their_official_host() -> None:
    for item in available_providers():
        if item.group in ("direct", "gateway"):
            assert item.required_host is not None
            assert item.base_url is not None
            assert item.base_url.startswith(f"https://{item.required_host}")


@pytest.mark.parametrize(
    ("provider_id", "env_name", "host", "model"),
    (
        (
            "openai",
            "OPENAI_API_KEY",
            "api.openai.com",
            "gpt-5.6-terra",
        ),
        (
            "anthropic",
            "ANTHROPIC_API_KEY",
            "api.anthropic.com",
            "claude-haiku-4-5",
        ),
        (
            "google-gemini",
            "GEMINI_API_KEY",
            "generativelanguage.googleapis.com",
            "gemini-2.5-flash",
        ),
    ),
)
def test_provider_candidates_publish_their_documented_connection_contract(
    provider_id: str, env_name: str, host: str, model: str
) -> None:
    definition = find_provider(provider_id)

    assert definition.availability == "coming-soon"
    assert definition.env_names == (env_name,)
    assert definition.required_host == host
    assert definition.base_url is not None
    assert definition.base_url.startswith(f"https://{host}")
    assert definition.recommended_model == model


def test_local_providers_publish_documented_localhost_probes() -> None:
    for item in providers_in_group("local"):
        assert item.local_probe_urls
        assert all(url.startswith("http://127.0.0.1:") for url in item.local_probe_urls)
        assert item.install_hint


def test_find_provider_rejects_unknown_ids() -> None:
    with pytest.raises(KeyError, match="Unknown provider"):
        find_provider("not-a-provider")
