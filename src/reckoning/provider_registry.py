"""The versioned provider catalog bundled with Reckoning releases.

Provider definitions ship with the release; setup never downloads a registry.
A provider stays ``coming-soon`` until an adapter passes the shared provider
contract (see tests/test_provider_contract.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

REGISTRY_VERSION = 2

ProviderGroup = Literal["direct", "gateway", "local", "custom", "developer"]
ProviderAvailability = Literal["available", "coming-soon"]
AuthKind = Literal["none", "api-key", "keyless-local", "custom-endpoint"]

GROUP_ORDER: tuple[ProviderGroup, ...] = (
    "direct",
    "gateway",
    "local",
    "custom",
    "developer",
)

GROUP_LABELS: dict[ProviderGroup, str] = {
    "direct": "Direct — one provider, one API key",
    "gateway": "Gateway — one key routes to many models",
    "local": "Local — inference runs on this machine",
    "custom": "Custom — an OpenAI-compatible endpoint you choose",
    "developer": "Developer and Demo",
}


@dataclass(frozen=True)
class ProviderDefinition:
    """One catalog entry; copy and capability data, never secrets."""

    id: str
    display_name: str
    group: ProviderGroup
    availability: ProviderAvailability
    auth: AuthKind
    summary: str
    env_names: tuple[str, ...] = ()
    base_url: str | None = None
    required_host: str | None = None
    local_probe_urls: tuple[str, ...] = ()
    recommended_model: str | None = None
    badges: tuple[str, ...] = ()
    install_hint: str | None = None

    @property
    def available(self) -> bool:
        return self.availability == "available"

    @property
    def needs_api_key(self) -> bool:
        return self.auth == "api-key"

    @property
    def is_local(self) -> bool:
        return self.group == "local"


def _coming_soon(
    provider_id: str,
    display_name: str,
    group: ProviderGroup,
    summary: str,
    *,
    badges: tuple[str, ...] = (),
) -> ProviderDefinition:
    return ProviderDefinition(
        id=provider_id,
        display_name=display_name,
        group=group,
        availability="coming-soon",
        auth="api-key",
        summary=summary,
        badges=badges,
    )


PROVIDER_REGISTRY: tuple[ProviderDefinition, ...] = (
    # Direct providers.
    ProviderDefinition(
        id="deepseek",
        display_name="DeepSeek",
        group="direct",
        availability="available",
        auth="api-key",
        summary="DeepSeek chat models through the official API.",
        env_names=("DEEPSEEK_API_KEY",),
        base_url="https://api.deepseek.com",
        required_host="api.deepseek.com",
        recommended_model="deepseek-v4-flash",
        badges=("API key required", "paid credit may be used"),
    ),
    ProviderDefinition(
        id="openai",
        display_name="OpenAI",
        group="direct",
        availability="coming-soon",
        auth="api-key",
        summary="GPT models through the official OpenAI API.",
        env_names=("OPENAI_API_KEY",),
        base_url="https://api.openai.com/v1",
        required_host="api.openai.com",
        # gpt-5-mini snapshots retire 2026-12-11; OpenAI names gpt-5.6-terra
        # as the replacement (platform.openai.com/docs/deprecations).
        recommended_model="gpt-5.6-terra",
        badges=("API key required", "paid credit may be used"),
    ),
    ProviderDefinition(
        id="anthropic",
        display_name="Anthropic",
        group="direct",
        availability="coming-soon",
        auth="api-key",
        summary="Claude models through the official Anthropic API.",
        env_names=("ANTHROPIC_API_KEY",),
        base_url="https://api.anthropic.com/v1",
        required_host="api.anthropic.com",
        recommended_model="claude-haiku-4-5",
        badges=("API key required", "paid credit may be used"),
    ),
    ProviderDefinition(
        id="google-gemini",
        display_name="Google Gemini",
        group="direct",
        availability="coming-soon",
        auth="api-key",
        summary="Gemini models through the official Google API.",
        env_names=("GEMINI_API_KEY",),
        base_url="https://generativelanguage.googleapis.com/v1beta",
        required_host="generativelanguage.googleapis.com",
        recommended_model="gemini-2.5-flash",
        badges=("API key required", "free or paid quota may be used"),
    ),
    _coming_soon(
        "xai",
        "xAI",
        "direct",
        "Grok models through the official xAI API.",
        badges=("API key required",),
    ),
    _coming_soon(
        "mistral",
        "Mistral",
        "direct",
        "Mistral models through the official API.",
        badges=("API key required",),
    ),
    _coming_soon(
        "groq",
        "Groq",
        "direct",
        "Fast hosted open models through the Groq API.",
        badges=("API key required",),
    ),
    _coming_soon(
        "together-ai",
        "Together AI",
        "direct",
        "Hosted open models through the Together API.",
        badges=("API key required",),
    ),
    _coming_soon(
        "fireworks-ai",
        "Fireworks AI",
        "direct",
        "Hosted open models through the Fireworks API.",
        badges=("API key required",),
    ),
    _coming_soon(
        "kimi",
        "Kimi (Moonshot)",
        "direct",
        "Kimi models through the official Moonshot API.",
        badges=("API key required", "regional terms may apply"),
    ),
    _coming_soon(
        "qwen",
        "Qwen (DashScope)",
        "direct",
        "Qwen models through Alibaba Cloud DashScope.",
        badges=("API key required", "regional terms may apply"),
    ),
    _coming_soon(
        "minimax",
        "MiniMax",
        "direct",
        "MiniMax models through the official API.",
        badges=("API key required", "regional terms may apply"),
    ),
    # Gateways.
    ProviderDefinition(
        id="orcarouter",
        display_name="OrcaRouter",
        group="gateway",
        availability="available",
        auth="api-key",
        summary="One OrcaRouter key routes to many hosted models.",
        env_names=("ORCAROUTER_API_KEY",),
        base_url="https://api.orcarouter.ai/v1",
        required_host="api.orcarouter.ai",
        recommended_model="orcarouter/auto",
        badges=("API key required", "paid credit may be used"),
    ),
    _coming_soon(
        "openrouter",
        "OpenRouter",
        "gateway",
        "One OpenRouter key routes to many hosted models.",
        badges=("API key required",),
    ),
    _coming_soon(
        "vercel-ai-gateway",
        "Vercel AI Gateway",
        "gateway",
        "Model routing through the Vercel AI Gateway.",
        badges=("API key required",),
    ),
    _coming_soon(
        "cloudflare-ai-gateway",
        "Cloudflare AI Gateway",
        "gateway",
        "Model routing through the Cloudflare AI Gateway.",
        badges=("API key required",),
    ),
    _coming_soon(
        "litellm",
        "LiteLLM",
        "gateway",
        "Self-hosted model routing through a LiteLLM proxy.",
        badges=("API key required",),
    ),
    # Local runtimes; all expose an OpenAI-compatible endpoint.
    ProviderDefinition(
        id="ollama",
        display_name="Ollama",
        group="local",
        availability="available",
        auth="keyless-local",
        summary="Run open models locally with Ollama.",
        base_url="http://127.0.0.1:11434/v1",
        local_probe_urls=("http://127.0.0.1:11434",),
        badges=("local processing", "no API key required"),
        install_hint=(
            "Install Ollama from https://ollama.com/download, then run "
            "'ollama serve' and pull a model, for example 'ollama pull qwen3'."
        ),
    ),
    ProviderDefinition(
        id="lmstudio",
        display_name="LM Studio",
        group="local",
        availability="available",
        auth="keyless-local",
        summary="Run open models locally with LM Studio.",
        base_url="http://127.0.0.1:1234/v1",
        local_probe_urls=("http://127.0.0.1:1234",),
        badges=("local processing", "no API key required"),
        install_hint=(
            "Install LM Studio from https://lmstudio.ai, load a model, and "
            "start the local server."
        ),
    ),
    ProviderDefinition(
        id="vllm",
        display_name="vLLM",
        group="local",
        availability="available",
        auth="keyless-local",
        summary="Serve open models locally with vLLM.",
        base_url="http://127.0.0.1:8000/v1",
        local_probe_urls=("http://127.0.0.1:8000",),
        badges=("local processing", "no API key required"),
        install_hint=(
            "Install vLLM with 'pip install vllm', then run "
            "'vllm serve <model>'."
        ),
    ),
    ProviderDefinition(
        id="llamacpp",
        display_name="llama.cpp",
        group="local",
        availability="available",
        auth="keyless-local",
        summary="Serve open models locally with llama.cpp.",
        base_url="http://127.0.0.1:8080/v1",
        local_probe_urls=("http://127.0.0.1:8080",),
        badges=("local processing", "no API key required"),
        install_hint=(
            "Build or install llama.cpp from https://github.com/ggml-org/"
            "llama.cpp, then run 'llama-server -m <model.gguf>'."
        ),
    ),
    # Custom OpenAI-compatible endpoint. Stays coming-soon until an opt-in
    # real completion is recorded (see tests/test_live_smoke.py).
    ProviderDefinition(
        id="custom",
        display_name="Custom OpenAI-compatible endpoint",
        group="custom",
        availability="coming-soon",
        auth="custom-endpoint",
        summary="Any endpoint that speaks the OpenAI chat-completions protocol.",
        badges=("endpoint URL required", "API key optional"),
    ),
    # Deterministic offline provider for evaluation and demos.
    ProviderDefinition(
        id="fake",
        display_name="Fake (deterministic demo)",
        group="developer",
        availability="available",
        auth="none",
        summary="Deterministic local replies; proves the workflow offline.",
        badges=("offline", "no API key required", "demo mode"),
    ),
)


def provider_registry() -> tuple[ProviderDefinition, ...]:
    return PROVIDER_REGISTRY


def find_provider(provider_id: str) -> ProviderDefinition:
    normalized = provider_id.strip().casefold()
    for definition in PROVIDER_REGISTRY:
        if definition.id == normalized:
            return definition
    raise KeyError(f"Unknown provider: {provider_id}")


def available_providers() -> tuple[ProviderDefinition, ...]:
    return tuple(item for item in PROVIDER_REGISTRY if item.available)


def coming_soon_providers() -> tuple[ProviderDefinition, ...]:
    return tuple(item for item in PROVIDER_REGISTRY if not item.available)


def providers_in_group(group: ProviderGroup) -> tuple[ProviderDefinition, ...]:
    return tuple(item for item in PROVIDER_REGISTRY if item.group == group)
