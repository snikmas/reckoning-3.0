"""The shared provider contract runs against every Available adapter.

A registry entry can only move from Coming soon to Available when its adapter
passes this suite with a stubbed transport and records one real completion
in the opt-in live smoke tests.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from reckoning.provider_adapters import (
    AdapterConfig,
    AnthropicProviderAdapter,
    DeepSeekProviderAdapter,
    FakeProviderAdapter,
    GeminiProviderAdapter,
    ModelDiscoveryError,
    ProviderVerificationError,
    SetupProviderAdapter,
    available_adapters,
    candidate_adapter_for,
    setup_adapter_for,
)
from reckoning.provider_registry import available_providers
from reckoning.providers import ProviderUsage

ADAPTERS = available_adapters()
ADAPTER_IDS = [adapter.definition.id for adapter in ADAPTERS]

SECRET = "sk-contract-secret"


def completion_payload(model: str = "test-model") -> bytes:
    return json.dumps(
        {
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": "ready"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        }
    ).encode("utf-8")


def response_payload(
    *,
    content: object = "ready",
    include_content: bool = True,
    finish_reason: str | None = None,
    reasoning_content: str | None = None,
) -> bytes:
    message: dict[str, object] = {"role": "assistant"}
    if include_content:
        message["content"] = content
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    choice: dict[str, object] = {"message": message}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return json.dumps(
        {
            "model": "test-model",
            "choices": [choice],
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        }
    ).encode("utf-8")


def models_payload(*ids: str) -> bytes:
    return json.dumps({"data": [{"id": item} for item in ids]}).encode("utf-8")


class RecordingTransport:
    def __init__(self, outcomes: list[bytes | Exception]) -> None:
        self.outcomes = outcomes
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Request, timeout: float) -> bytes:
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def http_error(code: int, message: str = "") -> HTTPError:
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    import io

    return HTTPError(
        f"https://example.test/{code}", code, "Error", {}, io.BytesIO(body)
    )


def config_for(adapter: SetupProviderAdapter) -> AdapterConfig:
    definition = adapter.definition
    if definition.auth == "api-key":
        return AdapterConfig(api_key=SECRET, model="test-model")
    if definition.auth == "keyless-local":
        return AdapterConfig(model="test-model")
    if definition.auth == "custom-endpoint":
        return AdapterConfig(
            api_key=None,
            base_url="https://llm.example.test/v1",
            model="test-model",
        )
    return AdapterConfig()


@pytest.mark.parametrize("adapter", ADAPTERS, ids=ADAPTER_IDS)
class TestSharedProviderContract:
    def test_verification_normalizes_a_real_completion(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider is covered by the demo-mode contract")
        transport = RecordingTransport([completion_payload("served-model")])

        result = adapter.verify(config_for(adapter), transport=transport)

        assert result.provider == adapter.definition.id
        assert result.model == "served-model"
        assert result.demo is False
        assert result.latency_ms >= 0
        assert result.usage.total_tokens == 5
        (request,) = transport.requests
        assert request.get_method() == "POST"
        assert request.full_url.endswith("/chat/completions")
        assert transport.timeouts[0] > 0

    def test_verification_sends_a_fixed_non_personal_prompt(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider makes no requests")
        transport = RecordingTransport([completion_payload()])

        adapter.verify(config_for(adapter), transport=transport)

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert body["messages"] == [
            {"role": "user", "content": "Reply with the single word: ready"}
        ]

    def test_client_errors_fail_without_retries_and_redact_secrets(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider makes no requests")
        config = config_for(adapter)
        echoed = config.api_key or "server-detail"
        transport = RecordingTransport(
            [http_error(401, f"invalid key {echoed} provided")]
        )

        with pytest.raises(ProviderVerificationError) as failure:
            adapter.verify(config, transport=transport, max_retries=3)

        assert len(transport.requests) == 1
        assert "HTTP 401" in str(failure.value)
        if config.api_key:
            assert config.api_key not in str(failure.value)

    def test_server_errors_stop_at_the_retry_limit(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider makes no requests")
        transport = RecordingTransport([http_error(500)] * 5)

        with pytest.raises(ProviderVerificationError, match="HTTP 500"):
            adapter.verify(config_for(adapter), transport=transport, max_retries=2)

        assert len(transport.requests) == 3

    def test_network_failures_are_normalized(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider makes no requests")
        transport = RecordingTransport([URLError("offline")])

        with pytest.raises(ProviderVerificationError, match="could not be reached"):
            adapter.verify(config_for(adapter), transport=transport, max_retries=0)

    def test_invalid_responses_are_normalized(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider makes no requests")
        transport = RecordingTransport([b'{"unexpected": true}'])

        with pytest.raises(ProviderVerificationError, match="invalid response"):
            adapter.verify(config_for(adapter), transport=transport)

    def test_model_discovery_returns_sorted_ids(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider is covered by the demo-mode contract")
        transport = RecordingTransport([models_payload("b-model", "a-model")])

        models = adapter.discover_models(config_for(adapter), transport=transport)

        assert models == ("a-model", "b-model")
        (request,) = transport.requests
        assert request.get_method() == "GET"
        assert request.full_url.endswith("/models")

    def test_model_discovery_failures_are_safe(
        self, adapter: SetupProviderAdapter
    ) -> None:
        if isinstance(adapter, FakeProviderAdapter):
            pytest.skip("the fake provider makes no requests")
        transport = RecordingTransport([http_error(503)])

        with pytest.raises(ModelDiscoveryError, match="HTTP 503"):
            adapter.discover_models(config_for(adapter), transport=transport)

    def test_detection_reports_names_without_secret_values(
        self, adapter: SetupProviderAdapter
    ) -> None:
        env_names = adapter.definition.env_names
        environ = {name: SECRET for name in env_names}
        hits = adapter.detect(
            environ=environ,
            configured=frozenset({adapter.definition.id}),
            probe=lambda url: True,
        )
        labels = "\n".join(hit.label for hit in hits)
        assert SECRET not in labels
        if isinstance(adapter, FakeProviderAdapter):
            assert hits == ()
            return
        if env_names:
            assert any(
                hit.kind == "environment" and env_names[0] in hit.label
                for hit in hits
            )
        assert any(hit.kind == "credential" for hit in hits)
        if adapter.definition.local_probe_urls:
            assert any(hit.kind == "local-endpoint" for hit in hits)


class TestDeepSeekVerificationContract:
    def test_deepseek_verification_disables_thinking(self) -> None:
        adapter = setup_adapter_for("deepseek")
        assert isinstance(adapter, DeepSeekProviderAdapter)
        transport = RecordingTransport([completion_payload()])

        adapter.verify(
            AdapterConfig(api_key=SECRET, model="deepseek-v4-flash"),
            transport=transport,
        )

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "deepseek-v4-flash"
        assert body["messages"] == [
            {"role": "user", "content": "Reply with the single word: ready"}
        ]
        assert body["stream"] is False
        assert body["max_tokens"] == 8
        assert body["thinking"] == {"type": "disabled"}

    @pytest.mark.parametrize(
        "provider_id",
        [item.id for item in available_providers() if item.id not in {"deepseek", "fake"}],
    )
    def test_other_adapters_omit_thinking(self, provider_id: str) -> None:
        adapter = setup_adapter_for(provider_id)
        transport = RecordingTransport([completion_payload()])

        adapter.verify(config_for(adapter), transport=transport)

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert "thinking" not in body

    def test_normal_deepseek_completion_omits_verification_override(self) -> None:
        adapter = setup_adapter_for("deepseek")
        transport = RecordingTransport([completion_payload()])

        adapter.complete(
            AdapterConfig(api_key=SECRET, model="deepseek-v4-flash"),
            (("user", "Hello"),),
            transport=transport,
        )

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert "thinking" not in body

    def test_reasoning_only_length_response_has_a_safe_specific_error(self) -> None:
        adapter = setup_adapter_for("deepseek")
        transport = RecordingTransport(
            [
                response_payload(
                    content=None,
                    finish_reason="length",
                    reasoning_content="private reasoning text",
                )
            ]
        )

        with pytest.raises(ProviderVerificationError) as failure:
            adapter.verify(
                AdapterConfig(api_key=SECRET, model="deepseek-v4-flash"),
                transport=transport,
            )

        message = str(failure.value)
        assert message == (
            "DeepSeek provider test ended before a final answer. Retry the test "
            "or edit provider settings."
        )
        assert SECRET not in message
        assert "reasoning_content" not in message
        assert "private reasoning text" not in message

    @pytest.mark.parametrize(
        ("content", "include_content"),
        [
            (None, True),
            ("", True),
            (None, False),
            ([], True),
            ({"answer": "ready"}, True),
            (0, True),
            (3.14, True),
        ],
        ids=["null", "empty", "missing", "list", "object", "zero", "numeric"],
    )
    def test_non_string_or_missing_content_cannot_pass(
        self, content: object, include_content: bool
    ) -> None:
        adapter = setup_adapter_for("deepseek")
        transport = RecordingTransport(
            [response_payload(content=content, include_content=include_content)]
        )

        with pytest.raises(ProviderVerificationError, match="invalid response"):
            adapter.verify(
                AdapterConfig(api_key=SECRET, model="deepseek-v4-flash"),
                transport=transport,
            )


class TestKeylessLocalContract:
    @pytest.mark.parametrize(
        "provider_id", ["ollama", "lmstudio", "vllm", "llamacpp"]
    )
    def test_local_adapters_send_no_authorization_header(
        self, provider_id: str
    ) -> None:
        adapter = setup_adapter_for(provider_id)
        transport = RecordingTransport([completion_payload()])

        adapter.verify(AdapterConfig(model="m"), transport=transport)

        (request,) = transport.requests
        assert "Authorization" not in request.headers

    @pytest.mark.parametrize(
        "provider_id", ["ollama", "lmstudio", "vllm", "llamacpp"]
    )
    def test_local_adapters_refuse_non_local_endpoints(
        self, provider_id: str
    ) -> None:
        adapter = setup_adapter_for(provider_id)
        with pytest.raises(ProviderVerificationError, match="localhost"):
            adapter.verify(
                AdapterConfig(base_url="https://remote.example.test/v1", model="m"),
                transport=RecordingTransport([]),
            )


class TestCustomEndpointContract:
    def test_custom_endpoint_uses_the_given_base_url_and_model(self) -> None:
        adapter = candidate_adapter_for("custom")
        transport = RecordingTransport([completion_payload()])

        result = adapter.verify(
            AdapterConfig(
                api_key="custom-key",
                base_url="https://llm.example.test/v1/",
                model="my-model",
            ),
            transport=transport,
        )

        (request,) = transport.requests
        assert request.full_url == "https://llm.example.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer custom-key"
        assert result.model == "test-model"

    def test_custom_endpoint_works_without_a_key(self) -> None:
        adapter = candidate_adapter_for("custom")
        transport = RecordingTransport([completion_payload()])

        adapter.verify(
            AdapterConfig(base_url="http://127.0.0.1:9000/v1", model="my-model"),
            transport=transport,
        )

        (request,) = transport.requests
        assert "Authorization" not in request.headers

    def test_custom_endpoint_rejects_an_invalid_base_url(self) -> None:
        adapter = candidate_adapter_for("custom")
        with pytest.raises(ProviderVerificationError, match="invalid"):
            adapter.verify(
                AdapterConfig(base_url="not-a-url", model="m"),
                transport=RecordingTransport([]),
            )

    @pytest.mark.parametrize(
        "base_url",
        (
            "https://user:base-url-secret@llm.example.test/v1",
            "https://llm.example.test/v1?token=base-url-secret",
        ),
    )
    def test_custom_endpoint_rejects_secret_bearing_base_urls_without_echo(
        self, base_url: str
    ) -> None:
        adapter = candidate_adapter_for("custom")
        transport = RecordingTransport([])

        with pytest.raises(ProviderVerificationError, match="invalid") as failure:
            adapter.verify(
                AdapterConfig(base_url=base_url, model="m"),
                transport=transport,
            )

        assert "base-url-secret" not in str(failure.value)
        assert transport.requests == []

    def test_custom_endpoint_requires_a_base_url(self) -> None:
        adapter = candidate_adapter_for("custom")
        with pytest.raises(ProviderVerificationError, match="base URL"):
            adapter.verify(
                AdapterConfig(model="m"), transport=RecordingTransport([])
            )

    def test_custom_endpoint_adds_non_secret_advanced_headers(self) -> None:
        adapter = candidate_adapter_for("custom")
        transport = RecordingTransport([completion_payload()])

        adapter.verify(
            AdapterConfig(
                base_url="https://llm.example.test/v1",
                model="my-model",
                protocol="openai-chat-completions",
                context_window=32_000,
                headers=(("X-Tenant", "tenant-a"),),
            ),
            transport=transport,
        )

        (request,) = transport.requests
        assert request.headers["X-tenant"] == "tenant-a"

    @pytest.mark.parametrize(
        ("name", "value"),
        (
            ("Bad Header", "safe"),
            ("X-Test", "secret\r\nInjected: yes"),
        ),
    )
    def test_custom_endpoint_rejects_unsafe_headers_without_echoing_values(
        self, name: str, value: str
    ) -> None:
        adapter = candidate_adapter_for("custom")

        with pytest.raises(ProviderVerificationError) as failure:
            adapter.verify(
                AdapterConfig(
                    base_url="https://llm.example.test/v1",
                    model="my-model",
                    headers=((name, value),),
                ),
                transport=RecordingTransport([]),
            )

        assert value not in str(failure.value)

    def test_custom_endpoint_discovery_uses_advanced_headers(self) -> None:
        adapter = candidate_adapter_for("custom")
        transport = RecordingTransport([models_payload("b-model", "a-model")])

        models = adapter.discover_models(
            AdapterConfig(
                base_url="https://llm.example.test/v1",
                headers=(("X-Tenant", "tenant-a"),),
            ),
            transport=transport,
        )

        assert models == ("a-model", "b-model")
        (request,) = transport.requests
        assert request.headers["X-tenant"] == "tenant-a"

    def test_custom_endpoint_redacts_environment_backed_header_values(self) -> None:
        adapter = candidate_adapter_for("custom")
        secret = "tenant-header-secret"
        transport = RecordingTransport(
            [http_error(401, f"unknown tenant {secret}")]
        )

        with pytest.raises(ProviderVerificationError) as failure:
            adapter.verify(
                AdapterConfig(
                    base_url="https://llm.example.test/v1",
                    model="my-model",
                    headers=(("X-Tenant", secret),),
                ),
                transport=transport,
            )

        assert secret not in str(failure.value)
        assert "[redacted]" in str(failure.value)


class TestOpenAICandidateContract:
    def test_openai_candidate_uses_the_documented_api_contract(self) -> None:
        adapter = candidate_adapter_for("openai")
        transport = RecordingTransport([completion_payload("served-openai-model")])

        result = adapter.verify(
            AdapterConfig(api_key=SECRET),
            transport=transport,
        )

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert request.full_url == "https://api.openai.com/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        assert body["model"] == adapter.definition.recommended_model
        assert body["max_completion_tokens"] == 8
        assert "max_tokens" not in body
        assert result.provider == "openai"

    def test_openai_candidate_discovers_models_and_accepts_manual_model(self) -> None:
        adapter = candidate_adapter_for("openai")
        discovery = RecordingTransport([models_payload("gpt-z", "gpt-a")])

        models = adapter.discover_models(
            AdapterConfig(api_key=SECRET), transport=discovery
        )

        assert models == ("gpt-a", "gpt-z")
        assert discovery.requests[0].headers["Authorization"] == f"Bearer {SECRET}"

        completion = RecordingTransport([completion_payload("manual-model")])
        result = adapter.verify(
            AdapterConfig(api_key=SECRET, model="manual-model"),
            transport=completion,
        )
        body = json.loads(completion.requests[0].data.decode("utf-8"))
        assert body["model"] == "manual-model"
        assert result.model == "manual-model"

    def test_openai_stays_coming_soon_before_the_opt_in_smoke(self) -> None:
        assert candidate_adapter_for("openai").definition.available is False
        with pytest.raises(KeyError, match="not available"):
            setup_adapter_for("openai")


def _gated_config(provider_id: str) -> AdapterConfig:
    if provider_id == "custom":
        return AdapterConfig(
            api_key=SECRET,
            base_url="https://llm.example.test/v1",
            model="test-model",
        )
    return AdapterConfig(api_key=SECRET, model="test-model")


@pytest.mark.parametrize("provider_id", ["openai", "custom"])
class TestGatedOpenAICompatibleCandidates:
    def test_client_error_fails_without_retries_and_redacts_secrets(
        self, provider_id: str
    ) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport([http_error(401, f"invalid key {SECRET}")])

        with pytest.raises(ProviderVerificationError) as failure:
            adapter.verify(_gated_config(provider_id), transport=transport, max_retries=3)

        assert len(transport.requests) == 1
        assert "HTTP 401" in str(failure.value)
        assert SECRET not in str(failure.value)

    def test_rate_limits_are_retried(self, provider_id: str) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport([http_error(429), completion_payload()])

        result = adapter.verify(
            _gated_config(provider_id), transport=transport, max_retries=1
        )

        assert result.retries == 1
        assert len(transport.requests) == 2

    def test_server_errors_stop_at_the_retry_limit(self, provider_id: str) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport([http_error(500)] * 5)

        with pytest.raises(ProviderVerificationError, match="HTTP 500"):
            adapter.verify(
                _gated_config(provider_id), transport=transport, max_retries=2
            )

        assert len(transport.requests) == 3

    def test_transport_failures_are_normalized(self, provider_id: str) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport([URLError("offline")])

        with pytest.raises(ProviderVerificationError, match="could not be reached"):
            adapter.verify(
                _gated_config(provider_id), transport=transport, max_retries=0
            )

    def test_malformed_json_is_normalized(self, provider_id: str) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport([b"{not json"])

        with pytest.raises(ProviderVerificationError, match="invalid response"):
            adapter.verify(_gated_config(provider_id), transport=transport)

    def test_missing_content_is_normalized(self, provider_id: str) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport(
            [response_payload(include_content=False)]
        )

        with pytest.raises(ProviderVerificationError, match="invalid response"):
            adapter.verify(_gated_config(provider_id), transport=transport)

    def test_length_termination_has_a_safe_specific_error(
        self, provider_id: str
    ) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport(
            [response_payload(content=None, finish_reason="length")]
        )

        with pytest.raises(ProviderVerificationError, match="before a final answer"):
            adapter.verify(_gated_config(provider_id), transport=transport)

    def test_missing_usage_defaults_to_zero(self, provider_id: str) -> None:
        adapter = candidate_adapter_for(provider_id)
        transport = RecordingTransport(
            [
                json.dumps(
                    {
                        "model": "test-model",
                        "choices": [{"message": {"content": "ready"}}],
                    }
                ).encode("utf-8")
            ]
        )

        result = adapter.verify(_gated_config(provider_id), transport=transport)

        assert result.usage == ProviderUsage(0, 0, 0)


def anthropic_completion_payload(content: str = "ready") -> bytes:
    return json.dumps(
        {
            "model": "claude-served-model",
            "content": [{"type": "text", "text": content}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }
    ).encode("utf-8")


def gemini_completion_payload(content: str = "ready") -> bytes:
    return json.dumps(
        {
            "modelVersion": "gemini-served-model",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": content}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 11,
                "candidatesTokenCount": 3,
                "totalTokenCount": 14,
            },
        }
    ).encode("utf-8")


class TestAnthropicCandidateContract:
    def test_anthropic_maps_system_and_conversation_without_losing_order(self) -> None:
        adapter = candidate_adapter_for("anthropic")
        assert isinstance(adapter, AnthropicProviderAdapter)
        transport = RecordingTransport([anthropic_completion_payload("Use the proof.")])

        result = adapter.complete(
            AdapterConfig(api_key=SECRET, model="claude-manual"),
            (
                ("system", "[protected]\ntruth first"),
                ("system", "[persona]\nbe direct"),
                ("user", "Earlier question"),
                ("assistant", "Earlier answer"),
                ("user", "Current question"),
            ),
            transport=transport,
        )

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert request.full_url == "https://api.anthropic.com/v1/messages"
        assert request.headers["X-api-key"] == SECRET
        assert request.headers["Anthropic-version"] == "2023-06-01"
        assert body == {
            "model": "claude-manual",
            "system": "[protected]\ntruth first\n\n[persona]\nbe direct",
            "messages": [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
                {"role": "user", "content": "Current question"},
            ],
            "max_tokens": 4096,
            "stream": False,
        }
        assert result.content == "Use the proof."
        assert result.model == "claude-served-model"
        assert result.usage == ProviderUsage(12, 4, 16)

    def test_anthropic_discovers_models_and_uses_the_recommended_model(self) -> None:
        adapter = candidate_adapter_for("anthropic")
        discovery = RecordingTransport(
            [json.dumps({"data": [{"id": "claude-z"}, {"id": "claude-a"}]}).encode()]
        )

        models = adapter.discover_models(
            AdapterConfig(api_key=SECRET), transport=discovery
        )

        assert models == ("claude-a", "claude-z")
        assert discovery.requests[0].full_url == (
            "https://api.anthropic.com/v1/models?limit=1000"
        )
        verification = RecordingTransport([anthropic_completion_payload()])
        adapter.verify(AdapterConfig(api_key=SECRET), transport=verification)
        request_body = json.loads(verification.requests[0].data.decode())
        assert request_body["model"] == "claude-haiku-4-5"
        assert request_body["max_tokens"] == 8

    def test_anthropic_model_discovery_follows_pagination(self) -> None:
        adapter = candidate_adapter_for("anthropic")
        discovery = RecordingTransport(
            [
                json.dumps(
                    {
                        "data": [{"id": "claude-b"}],
                        "has_more": True,
                        "last_id": "claude-b",
                    }
                ).encode(),
                json.dumps(
                    {
                        "data": [{"id": "claude-a"}],
                        "has_more": False,
                        "last_id": "claude-a",
                    }
                ).encode(),
            ]
        )

        models = adapter.discover_models(
            AdapterConfig(api_key=SECRET), transport=discovery
        )

        assert models == ("claude-a", "claude-b")
        assert len(discovery.requests) == 2
        assert discovery.requests[0].full_url.endswith("/models?limit=1000")
        assert discovery.requests[1].full_url.endswith(
            "/models?limit=1000&after_id=claude-b"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            json.dumps({"data": []}).encode(),
            json.dumps({"models": []}).encode(),
            json.dumps({"data": [{"id": ""}]}).encode(),
            json.dumps(
                {"data": [{"id": "claude-a"}], "has_more": True}
            ).encode(),
        ],
        ids=["empty", "missing-data", "blank-id", "missing-cursor"],
    )
    def test_anthropic_model_discovery_rejects_empty_or_malformed_pages(
        self, payload: bytes
    ) -> None:
        adapter = candidate_adapter_for("anthropic")
        transport = RecordingTransport([payload])

        with pytest.raises(ModelDiscoveryError):
            adapter.discover_models(AdapterConfig(api_key=SECRET), transport=transport)

    def test_anthropic_verification_accepts_a_manual_model_id(self) -> None:
        adapter = candidate_adapter_for("anthropic")
        transport = RecordingTransport([anthropic_completion_payload("ready")])

        result = adapter.verify(
            AdapterConfig(api_key=SECRET, model="claude-manual-20260101"),
            transport=transport,
        )

        body = json.loads(transport.requests[0].data.decode())
        assert body["model"] == "claude-manual-20260101"
        assert result.usage == ProviderUsage(12, 4, 16)
        assert result.latency_ms >= 0

    def test_anthropic_retries_rate_limits_and_server_errors(self) -> None:
        adapter = candidate_adapter_for("anthropic")
        transport = RecordingTransport(
            [http_error(429), http_error(529), anthropic_completion_payload()]
        )

        result = adapter.verify(
            AdapterConfig(api_key=SECRET), transport=transport, max_retries=2
        )

        assert result.retries == 2
        assert len(transport.requests) == 3

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_anthropic_does_not_retry_non_retryable_client_errors(
        self, code: int
    ) -> None:
        adapter = candidate_adapter_for("anthropic")
        transport = RecordingTransport([http_error(code)] * 3)

        with pytest.raises(ProviderVerificationError):
            adapter.verify(
                AdapterConfig(api_key=SECRET), transport=transport, max_retries=3
            )

        assert len(transport.requests) == 1


class TestGeminiCandidateContract:
    def test_gemini_maps_system_and_conversation_without_losing_order(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        assert isinstance(adapter, GeminiProviderAdapter)
        transport = RecordingTransport([gemini_completion_payload("Use the proof.")])

        result = adapter.complete(
            AdapterConfig(api_key=SECRET, model="gemini-manual"),
            (
                ("system", "[protected]\ntruth first"),
                ("system", "[persona]\nbe direct"),
                ("user", "Earlier question"),
                ("assistant", "Earlier answer"),
                ("user", "Current question"),
            ),
            transport=transport,
        )

        (request,) = transport.requests
        body = json.loads(request.data.decode("utf-8"))
        assert request.full_url == (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-manual:generateContent"
        )
        assert request.headers["X-goog-api-key"] == SECRET
        assert body == {
            "systemInstruction": {
                "parts": [{"text": "[protected]\ntruth first\n\n[persona]\nbe direct"}]
            },
            "contents": [
                {"role": "user", "parts": [{"text": "Earlier question"}]},
                {"role": "model", "parts": [{"text": "Earlier answer"}]},
                {"role": "user", "parts": [{"text": "Current question"}]},
            ],
        }
        assert result.content == "Use the proof."
        assert result.model == "gemini-served-model"
        assert result.usage == ProviderUsage(11, 3, 14)

    def test_gemini_discovers_generation_models_and_uses_recommended_model(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        discovery = RecordingTransport(
            [
                json.dumps(
                    {
                        "models": [
                            {
                                "name": "models/gemini-z",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                            {
                                "name": "models/embedding-only",
                                "supportedGenerationMethods": ["embedContent"],
                            },
                            {
                                "name": "models/gemini-a",
                                "supportedGenerationMethods": ["generateContent"],
                            },
                        ]
                    }
                ).encode()
            ]
        )

        models = adapter.discover_models(
            AdapterConfig(api_key=SECRET), transport=discovery
        )

        assert models == ("gemini-a", "gemini-z")
        assert discovery.requests[0].full_url.endswith("/v1beta/models?pageSize=1000")
        verification = RecordingTransport([gemini_completion_payload()])
        adapter.verify(AdapterConfig(api_key=SECRET), transport=verification)
        assert verification.requests[0].full_url.endswith(
            "/models/gemini-2.5-flash:generateContent"
        )
        request_body = json.loads(verification.requests[0].data.decode())
        assert request_body["generationConfig"] == {"maxOutputTokens": 8}

    def test_gemini_model_discovery_follows_pagination(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        discovery = RecordingTransport(
            [
                json.dumps(
                    {
                        "models": [
                            {
                                "name": "models/gemini-b",
                                "supportedGenerationMethods": ["generateContent"],
                            }
                        ],
                        "nextPageToken": "page-two",
                    }
                ).encode(),
                json.dumps(
                    {
                        "models": [
                            {
                                "name": "models/gemini-a",
                                "supportedGenerationMethods": ["generateContent"],
                            }
                        ]
                    }
                ).encode(),
            ]
        )

        models = adapter.discover_models(
            AdapterConfig(api_key=SECRET), transport=discovery
        )

        assert models == ("gemini-a", "gemini-b")
        assert len(discovery.requests) == 2
        assert discovery.requests[0].full_url.endswith("/v1beta/models?pageSize=1000")
        assert discovery.requests[1].full_url.endswith(
            "/v1beta/models?pageSize=1000&pageToken=page-two"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            json.dumps({"models": []}).encode(),
            json.dumps({"data": []}).encode(),
            json.dumps(
                {
                    "models": [
                        {
                            "name": "models/embedding-only",
                            "supportedGenerationMethods": ["embedContent"],
                        }
                    ]
                }
            ).encode(),
        ],
        ids=["empty", "missing-models", "no-generation-method"],
    )
    def test_gemini_model_discovery_rejects_empty_or_malformed_pages(
        self, payload: bytes
    ) -> None:
        adapter = candidate_adapter_for("google-gemini")
        transport = RecordingTransport([payload])

        with pytest.raises(ModelDiscoveryError):
            adapter.discover_models(AdapterConfig(api_key=SECRET), transport=transport)

    def test_gemini_verification_accepts_a_manual_model_id(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        transport = RecordingTransport([gemini_completion_payload("ready")])

        result = adapter.verify(
            AdapterConfig(api_key=SECRET, model="gemini-manual-20260101"),
            transport=transport,
        )

        assert transport.requests[0].full_url.endswith(
            "/models/gemini-manual-20260101:generateContent"
        )
        assert result.usage == ProviderUsage(11, 3, 14)
        assert result.latency_ms >= 0

    def test_gemini_verification_reports_a_blocked_prompt(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        transport = RecordingTransport(
            [json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()]
        )

        with pytest.raises(ProviderVerificationError, match="blocked the provider test"):
            adapter.verify(AdapterConfig(api_key=SECRET), transport=transport)

    def test_gemini_verification_reports_a_truncated_answer(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        transport = RecordingTransport(
            [
                json.dumps(
                    {
                        "candidates": [
                            {
                                "content": {"parts": [{"text": ""}]},
                                "finishReason": "MAX_TOKENS",
                            }
                        ]
                    }
                ).encode()
            ]
        )

        with pytest.raises(
            ProviderVerificationError, match="before a final answer"
        ):
            adapter.verify(AdapterConfig(api_key=SECRET), transport=transport)

    def test_gemini_retries_rate_limits_and_server_errors(self) -> None:
        adapter = candidate_adapter_for("google-gemini")
        transport = RecordingTransport(
            [http_error(429), http_error(503), gemini_completion_payload()]
        )

        result = adapter.verify(
            AdapterConfig(api_key=SECRET), transport=transport, max_retries=2
        )

        assert result.retries == 2
        assert len(transport.requests) == 3

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
    def test_gemini_does_not_retry_non_retryable_client_errors(
        self, code: int
    ) -> None:
        adapter = candidate_adapter_for("google-gemini")
        transport = RecordingTransport([http_error(code)] * 3)

        with pytest.raises(ProviderVerificationError):
            adapter.verify(
                AdapterConfig(api_key=SECRET), transport=transport, max_retries=3
            )

        assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("provider_id", "env_name"),
    (
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google-gemini", "GEMINI_API_KEY"),
    ),
)
def test_protocol_candidates_detect_key_references_and_remain_gated(
    provider_id: str, env_name: str
) -> None:
    adapter = candidate_adapter_for(provider_id)

    hits = adapter.detect(
        environ={env_name: SECRET}, configured=frozenset({provider_id})
    )

    assert [hit.kind for hit in hits] == ["environment", "credential"]
    assert SECRET not in "\n".join(hit.label for hit in hits)
    with pytest.raises(KeyError, match="not available"):
        setup_adapter_for(provider_id)


@pytest.mark.parametrize(
    ("provider_id", "payload"),
    (
        ("anthropic", anthropic_completion_payload()),
        ("google-gemini", gemini_completion_payload()),
    ),
)
def test_protocol_candidates_normalize_retries_and_safe_errors(
    provider_id: str, payload: bytes
) -> None:
    adapter = candidate_adapter_for(provider_id)
    retrying = RecordingTransport([http_error(503), payload])

    result = adapter.verify(
        AdapterConfig(api_key=SECRET), transport=retrying, max_retries=1
    )

    assert result.retries == 1
    assert result.latency_ms >= 0
    assert len(retrying.requests) == 2
    denied = RecordingTransport([http_error(401, f"bad key {SECRET}")])
    with pytest.raises(ProviderVerificationError) as failure:
        adapter.verify(AdapterConfig(api_key=SECRET), transport=denied)
    assert SECRET not in str(failure.value)


class TestCloudHostPinning:
    @pytest.mark.parametrize("provider_id", ["deepseek", "orcarouter"])
    def test_cloud_adapters_refuse_unofficial_hosts(self, provider_id: str) -> None:
        adapter = setup_adapter_for(provider_id)
        with pytest.raises(ProviderVerificationError, match="official"):
            adapter.verify(
                AdapterConfig(
                    api_key="k",
                    base_url="https://evil.example.test",
                    model="m",
                ),
                transport=RecordingTransport([]),
            )

    @pytest.mark.parametrize("provider_id", ["deepseek", "orcarouter"])
    def test_cloud_adapters_require_an_api_key(self, provider_id: str) -> None:
        adapter = setup_adapter_for(provider_id)
        with pytest.raises(ProviderVerificationError, match="API key"):
            adapter.verify(
                AdapterConfig(model="m"), transport=RecordingTransport([])
            )


class TestFakeDemoContract:
    def test_fake_verification_is_demo_mode_and_offline(self) -> None:
        adapter = setup_adapter_for("fake")

        def forbidden_transport(request: Request, timeout: float) -> bytes:
            raise AssertionError("the fake provider must not make requests")

        result = adapter.verify(
            AdapterConfig(), transport=forbidden_transport
        )

        assert result.demo is True
        assert result.provider == "fake"

    def test_fake_discovery_returns_the_deterministic_model(self) -> None:
        adapter = setup_adapter_for("fake")
        assert adapter.discover_models(AdapterConfig()) == ("deterministic-fake",)

    def test_coming_soon_providers_have_no_adapter(self) -> None:
        available = {item.id for item in available_providers()}
        with pytest.raises(KeyError, match="not available"):
            setup_adapter_for("openai")
        assert "openai" not in available
