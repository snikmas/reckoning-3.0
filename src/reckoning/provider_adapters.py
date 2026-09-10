"""Setup-facing provider adapters behind one shared contract.

Every available registry provider gets an adapter that can detect existing
access (without displaying secrets), discover models, and prove itself with
one minimal real completion. Coming-soon entries have no adapter.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from reckoning.provider_registry import (
    ProviderDefinition,
    available_providers,
    find_provider,
)
from reckoning.provider_validation import is_safe_header_value, is_valid_header_name
from reckoning.providers import ProviderFailure, ProviderResponse, ProviderUsage

Transport = Callable[[Request, float], bytes]


def urlopen_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read()


VERIFICATION_PROMPT = "Reply with the single word: ready"
VERIFICATION_MAX_TOKENS = 8


class ProviderVerificationError(RuntimeError):
    """A safe-to-display provider failure with optional run metadata."""

    def __init__(
        self,
        message: str,
        *,
        model_calls: int = 0,
        latency_ms: int = 0,
        retries: int = 0,
    ) -> None:
        super().__init__(message)
        self.model_calls = model_calls
        self.latency_ms = latency_ms
        self.retries = retries


class ModelDiscoveryError(RuntimeError):
    """A safe-to-display model discovery failure."""


@dataclass(frozen=True)
class DetectionHit:
    """Existing access evidence; names and endpoints only, never values."""

    kind: Literal["environment", "credential", "local-endpoint"]
    label: str


@dataclass(frozen=True)
class ProviderVerification:
    provider: str
    model: str
    latency_ms: int
    retries: int
    usage: ProviderUsage
    demo: bool = False
    content: str = ""


@dataclass(frozen=True)
class ChatCompletion:
    content: str
    model: str
    latency_ms: int
    retries: int
    usage: ProviderUsage


@dataclass(frozen=True)
class AdapterConfig:
    """Everything an adapter needs; ``api_key`` stays out of reprs."""

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    protocol: str = "openai-chat-completions"
    context_window: int | None = None
    headers: tuple[tuple[str, str], ...] = ()

    def __repr__(self) -> str:
        return (
            f"AdapterConfig(api_key={'set' if self.api_key else None}, "
            f"base_url={self.base_url!r}, model={self.model!r}, "
            f"protocol={self.protocol!r}, context_window={self.context_window!r}, "
            f"headers={'set' if self.headers else None})"
        )


class SetupProviderAdapter(Protocol):
    """The shared provider contract every available adapter must satisfy."""

    @property
    def definition(self) -> ProviderDefinition: ...

    def detect(
        self,
        *,
        environ: Mapping[str, str],
        configured: frozenset[str],
        probe: Callable[[str], bool] | None = None,
    ) -> tuple[DetectionHit, ...]: ...

    def discover_models(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 15.0,
    ) -> tuple[str, ...]: ...

    def verify(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        timer: Callable[[], float] = monotonic,
    ) -> ProviderVerification: ...

    def complete(
        self,
        config: AdapterConfig,
        messages: tuple[tuple[str, str], ...],
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        timer: Callable[[], float] = monotonic,
    ) -> ChatCompletion: ...


def _redact(message: str, *secrets: str | None) -> str:
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _validated_base_url(definition: ProviderDefinition, base_url: str | None) -> str:
    candidate = (base_url or definition.base_url or "").strip().rstrip("/")
    if not candidate:
        raise ProviderVerificationError(
            f"{definition.display_name} needs a base URL. Add the endpoint address."
        )
    parsed = urlsplit(candidate)
    try:
        parsed.port
    except ValueError as error:
        raise ProviderVerificationError(
            f"{definition.display_name} base URL is invalid."
        ) from error
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ProviderVerificationError(
            f"{definition.display_name} base URL is invalid."
        )
    if definition.required_host is not None and (
        parsed.scheme != "https" or parsed.hostname != definition.required_host
    ):
        raise ProviderVerificationError(
            f"{definition.display_name} must use the official "
            f"https://{definition.required_host} host."
        )
    if definition.auth == "keyless-local" and parsed.hostname not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise ProviderVerificationError(
            f"{definition.display_name} only accepts localhost endpoints."
        )
    return candidate


class OpenAICompatibleAdapter:
    """One adapter for providers that speak OpenAI chat completions."""

    def __init__(self, definition: ProviderDefinition) -> None:
        self._definition = definition

    @property
    def definition(self) -> ProviderDefinition:
        return self._definition

    def detect(
        self,
        *,
        environ: Mapping[str, str],
        configured: frozenset[str],
        probe: Callable[[str], bool] | None = None,
    ) -> tuple[DetectionHit, ...]:
        hits: list[DetectionHit] = []
        for env_name in self._definition.env_names:
            if environ.get(env_name, "").strip():
                hits.append(DetectionHit("environment", f"{env_name} is set"))
        if self._definition.id in configured:
            hits.append(DetectionHit("credential", "a saved credential exists"))
        for url in self._definition.local_probe_urls:
            if probe is not None and probe(url):
                hits.append(DetectionHit("local-endpoint", f"{url} responds"))
        return tuple(hits)

    def discover_models(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 15.0,
    ) -> tuple[str, ...]:
        base_url = _validated_base_url(self._definition, config.base_url)
        self._validate_advanced_config(config)
        request = Request(
            f"{base_url}/models",
            headers=self._request_headers(config),
            method="GET",
        )
        try:
            raw = transport(request, timeout_seconds)
            parsed = json.loads(raw.decode("utf-8"))
            data = parsed["data"]
            models = tuple(
                sorted(str(item["id"]) for item in data if item.get("id"))
            )
        except HTTPError as error:
            raise ModelDiscoveryError(
                _redact(
                    f"{self._definition.display_name} returned HTTP "
                    f"{error.code} while listing models.",
                    config.api_key,
                )
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ModelDiscoveryError(
                f"{self._definition.display_name} could not be reached: {reason}"
            ) from error
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
            raise ModelDiscoveryError(
                f"{self._definition.display_name} returned an invalid model list."
            ) from error
        if not models:
            raise ModelDiscoveryError(
                f"{self._definition.display_name} listed no models; "
                "enter a model ID manually under Advanced."
            )
        return models

    def verify(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        timer: Callable[[], float] = monotonic,
    ) -> ProviderVerification:
        """Prove the provider with one minimal, fixed, non-personal completion."""
        if self._definition.needs_api_key and not (config.api_key or "").strip():
            raise ProviderVerificationError(
                f"{self._definition.display_name} needs an API key."
            )
        model = (config.model or self._definition.recommended_model or "").strip()
        if not model:
            raise ProviderVerificationError(
                f"{self._definition.display_name} needs a model ID."
            )
        return self._chat(
            config,
            model,
            (("user", VERIFICATION_PROMPT),),
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            timer=timer,
            max_tokens=VERIFICATION_MAX_TOKENS,
            verification_request_fields=self._verification_request_fields(),
        )

    def complete(
        self,
        config: AdapterConfig,
        messages: tuple[tuple[str, str], ...],
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        timer: Callable[[], float] = monotonic,
    ) -> ChatCompletion:
        """One chat completion with normalized errors and bounded retries."""
        if self._definition.needs_api_key and not (config.api_key or "").strip():
            raise ProviderVerificationError(
                f"{self._definition.display_name} needs an API key."
            )
        model = (config.model or self._definition.recommended_model or "").strip()
        if not model:
            raise ProviderVerificationError(
                f"{self._definition.display_name} needs a model ID."
            )
        verification = self._chat(
            config,
            model,
            messages,
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            timer=timer,
        )
        return ChatCompletion(
            content=verification.content,
            model=verification.model,
            latency_ms=verification.latency_ms,
            retries=verification.retries,
            usage=verification.usage,
        )

    def _chat(
        self,
        config: AdapterConfig,
        model: str,
        messages: tuple[tuple[str, str], ...],
        *,
        transport: Transport,
        timeout_seconds: float,
        max_retries: int,
        timer: Callable[[], float],
        max_tokens: int | None = None,
        verification_request_fields: Mapping[str, object] | None = None,
    ) -> ProviderVerification:
        base_url = _validated_base_url(self._definition, config.base_url)
        extra_fields = dict(verification_request_fields or {})
        for reserved in ("model", "messages", "stream", "max_tokens"):
            extra_fields.pop(reserved, None)
        body: dict[str, object] = {
            **extra_fields,
            "model": model,
            "messages": [
                {"role": role, "content": content} for role, content in messages
            ],
            "stream": False,
        }
        self._validate_advanced_config(config)
        if max_tokens is not None:
            body[self._max_tokens_field()] = max_tokens
        payload = json.dumps(body).encode("utf-8")
        started = timer()
        last_error = "The provider did not return a response."
        for attempt in range(max(0, max_retries) + 1):
            request = Request(
                f"{base_url}/chat/completions",
                data=payload,
                headers={
                    **self._request_headers(config),
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                raw = transport(request, timeout_seconds)
                return self._parse_completion(
                    raw, model=model, started=started, attempt=attempt, timer=timer
                )
            except HTTPError as error:
                last_error = self._http_error_message(error, config)
                if error.code < 500 and error.code != 429:
                    break
            except (URLError, TimeoutError, OSError) as error:
                reason = getattr(error, "reason", error)
                last_error = (
                    f"{self._definition.display_name} could not be reached: {reason}"
                )
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                last_error = (
                    f"{self._definition.display_name} returned an invalid response."
                )
                break
        raise ProviderVerificationError(
            _redact(
                last_error,
                config.api_key,
                *(value for _name, value in config.headers),
            ),
            model_calls=max(0, attempt + 1),
            latency_ms=round((timer() - started) * 1000),
            retries=max(0, attempt),
        )

    def _parse_completion(
        self,
        raw: bytes,
        *,
        model: str,
        started: float,
        attempt: int,
        timer: Callable[[], float],
    ) -> ProviderVerification:
        parsed = json.loads(raw.decode("utf-8"))
        choice = parsed["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise ValueError("message is not an object")
        content = message.get("content")
        if content is None or (
            isinstance(content, str) and not content.strip()
        ):
            if choice.get("finish_reason") == "length":
                raise ProviderVerificationError(
                    f"{self._definition.display_name} provider test ended "
                    "before a final answer. "
                    "Retry the test or edit provider settings."
                )
            raise ValueError("empty content")
        if not isinstance(content, str):
            raise ValueError("content is not a string")
        content = content.strip()
        usage_data = parsed.get("usage") or {}
        return ProviderVerification(
            provider=self._definition.id,
            model=str(parsed.get("model", model)),
            content=content,
            latency_ms=round((timer() - started) * 1000),
            retries=attempt,
            usage=ProviderUsage(
                input_tokens=int(usage_data.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage_data.get("completion_tokens", 0) or 0),
                total_tokens=int(usage_data.get("total_tokens", 0) or 0),
            ),
        )

    def _http_error_message(self, error: HTTPError, config: AdapterConfig) -> str:
        fallback = f"{self._definition.display_name} returned HTTP {error.code}."
        try:
            payload = json.loads(error.read(65_536).decode("utf-8"))
            detail = payload.get("error", {})
            if not isinstance(detail, dict):
                return fallback
            code = str(detail.get("code") or "").strip()
            message = str(detail.get("message") or "").strip()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return fallback
        if not message:
            return fallback
        safe = _redact(
            message,
            config.api_key,
            *(value for _name, value in config.headers),
        )
        code_text = f" ({code})" if code else ""
        return (
            f"{self._definition.display_name} returned HTTP {error.code}"
            f"{code_text}: {safe}"
        )

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        if api_key and api_key.strip():
            return {"Authorization": f"Bearer {api_key.strip()}"}
        return {}

    def _request_headers(self, config: AdapterConfig) -> dict[str, str]:
        headers: dict[str, str] = {}
        if config.headers:
            if self._definition.auth != "custom-endpoint":
                raise ProviderVerificationError(
                    "Custom headers are only supported for custom endpoints."
                )
            for name, value in config.headers:
                normalized = name.strip()
                if not is_valid_header_name(normalized) or not is_safe_header_value(
                    value
                ):
                    raise ProviderVerificationError(
                        "A custom header name or value is invalid."
                    )
                if normalized.casefold() in ("authorization", "content-type", "host"):
                    raise ProviderVerificationError(
                        f"Custom header {normalized} is reserved."
                    )
                headers[normalized] = value.strip()
        return {**headers, **self._headers(config.api_key)}

    @staticmethod
    def _max_tokens_field() -> str:
        return "max_tokens"

    @staticmethod
    def _validate_advanced_config(config: AdapterConfig) -> None:
        if config.protocol != "openai-chat-completions":
            raise ProviderVerificationError(
                f"Unsupported custom endpoint protocol: {config.protocol}"
            )
        if config.context_window is not None and config.context_window <= 0:
            raise ProviderVerificationError("Context size must be a positive integer.")

    def _verification_request_fields(self) -> Mapping[str, object]:
        return {}


class OpenAIProviderAdapter(OpenAICompatibleAdapter):
    """The official OpenAI Chat Completions protocol candidate."""

    @staticmethod
    def _max_tokens_field() -> str:
        return "max_completion_tokens"


class DeepSeekProviderAdapter(OpenAICompatibleAdapter):
    """DeepSeek setup verification with thinking disabled."""

    def _verification_request_fields(self) -> Mapping[str, object]:
        return {"thinking": {"type": "disabled"}}


class AnthropicProviderAdapter(OpenAICompatibleAdapter):
    """Anthropic's Messages API behind the shared provider contract."""

    def discover_models(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 15.0,
    ) -> tuple[str, ...]:
        request = Request(
            f"{_validated_base_url(self._definition, config.base_url)}/models",
            headers=self._request_headers(config),
            method="GET",
        )
        try:
            parsed = json.loads(transport(request, timeout_seconds).decode("utf-8"))
            data = parsed["data"]
            if not isinstance(data, list):
                raise TypeError("model data is not a list")
            models = tuple(
                sorted(
                    item["id"].strip()
                    for item in data
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"].strip()
                )
            )
        except HTTPError as error:
            raise ModelDiscoveryError(
                _redact(
                    f"Anthropic returned HTTP {error.code} while listing models.",
                    config.api_key,
                )
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ModelDiscoveryError(
                f"Anthropic could not be reached: {reason}"
            ) from error
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ModelDiscoveryError(
                "Anthropic returned an invalid model list."
            ) from error
        if not models:
            raise ModelDiscoveryError(
                "Anthropic listed no models; enter a model ID manually under Advanced."
            )
        return models

    def _chat(
        self,
        config: AdapterConfig,
        model: str,
        messages: tuple[tuple[str, str], ...],
        *,
        transport: Transport,
        timeout_seconds: float,
        max_retries: int,
        timer: Callable[[], float],
        max_tokens: int | None = None,
        verification_request_fields: Mapping[str, object] | None = None,
    ) -> ProviderVerification:
        del verification_request_fields
        system = "\n\n".join(content for role, content in messages if role == "system")
        conversation = []
        for role, content in messages:
            if role == "system":
                continue
            if role not in ("user", "assistant"):
                raise ProviderVerificationError(f"Unsupported message role: {role}")
            conversation.append({"role": role, "content": content})
        body: dict[str, object] = {
            "model": model,
            "messages": conversation,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
            "stream": False,
        }
        if system:
            body["system"] = system
        return self._send_anthropic(
            config,
            body,
            model=model,
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            timer=timer,
        )

    def _send_anthropic(
        self,
        config: AdapterConfig,
        body: dict[str, object],
        *,
        model: str,
        transport: Transport,
        timeout_seconds: float,
        max_retries: int,
        timer: Callable[[], float],
    ) -> ProviderVerification:
        payload = json.dumps(body).encode("utf-8")
        started = timer()
        last_error = "Anthropic did not return a response."
        for attempt in range(max(0, max_retries) + 1):
            request = Request(
                f"{_validated_base_url(self._definition, config.base_url)}/messages",
                data=payload,
                headers={
                    **self._request_headers(config),
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                raw = transport(request, timeout_seconds)
                return self._parse_anthropic_completion(
                    raw, model=model, started=started, attempt=attempt, timer=timer
                )
            except HTTPError as error:
                last_error = self._http_error_message(error, config)
                if error.code < 500 and error.code != 429:
                    break
            except (URLError, TimeoutError, OSError) as error:
                reason = getattr(error, "reason", error)
                last_error = f"Anthropic could not be reached: {reason}"
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                last_error = "Anthropic returned an invalid response."
                break
        raise ProviderVerificationError(
            _redact(last_error, config.api_key),
            model_calls=attempt + 1,
            latency_ms=round((timer() - started) * 1000),
            retries=attempt,
        )

    def _parse_anthropic_completion(
        self,
        raw: bytes,
        *,
        model: str,
        started: float,
        attempt: int,
        timer: Callable[[], float],
    ) -> ProviderVerification:
        parsed = json.loads(raw.decode("utf-8"))
        blocks = parsed["content"]
        if not isinstance(blocks, list):
            raise TypeError("content is not a list")
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()
        if not text:
            raise ValueError("content has no text")
        usage = parsed.get("usage") or {}
        if not isinstance(usage, dict):
            raise TypeError("usage is not an object")
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        served_model = parsed.get("model")
        return ProviderVerification(
            provider=self._definition.id,
            model=served_model if isinstance(served_model, str) else model,
            content=text,
            latency_ms=round((timer() - started) * 1000),
            retries=attempt,
            usage=ProviderUsage(
                input_tokens, output_tokens, input_tokens + output_tokens
            ),
        )

    def _request_headers(self, config: AdapterConfig) -> dict[str, str]:
        if config.headers:
            raise ProviderVerificationError("Anthropic does not accept custom headers.")
        return {
            "x-api-key": (config.api_key or "").strip(),
            "anthropic-version": "2023-06-01",
        }


class GeminiProviderAdapter(OpenAICompatibleAdapter):
    """Google Gemini's generateContent API behind the shared contract."""

    def discover_models(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 15.0,
    ) -> tuple[str, ...]:
        request = Request(
            f"{_validated_base_url(self._definition, config.base_url)}/models",
            headers=self._request_headers(config),
            method="GET",
        )
        try:
            parsed = json.loads(transport(request, timeout_seconds).decode("utf-8"))
            data = parsed["models"]
            if not isinstance(data, list):
                raise TypeError("model data is not a list")
            models = tuple(
                sorted(
                    item["name"].removeprefix("models/").strip()
                    for item in data
                    if isinstance(item, dict)
                    and isinstance(item.get("name"), str)
                    and "generateContent"
                    in item.get("supportedGenerationMethods", ())
                )
            )
        except HTTPError as error:
            raise ModelDiscoveryError(
                _redact(
                    f"Google Gemini returned HTTP {error.code} while listing models.",
                    config.api_key,
                )
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ModelDiscoveryError(
                f"Google Gemini could not be reached: {reason}"
            ) from error
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ModelDiscoveryError(
                "Google Gemini returned an invalid model list."
            ) from error
        if not models:
            raise ModelDiscoveryError(
                "Google Gemini listed no generation models; "
                "enter a model ID manually under Advanced."
            )
        return models

    def _chat(
        self,
        config: AdapterConfig,
        model: str,
        messages: tuple[tuple[str, str], ...],
        *,
        transport: Transport,
        timeout_seconds: float,
        max_retries: int,
        timer: Callable[[], float],
        max_tokens: int | None = None,
        verification_request_fields: Mapping[str, object] | None = None,
    ) -> ProviderVerification:
        del verification_request_fields
        system = "\n\n".join(content for role, content in messages if role == "system")
        contents = []
        for role, content in messages:
            if role == "system":
                continue
            if role not in ("user", "assistant"):
                raise ProviderVerificationError(f"Unsupported message role: {role}")
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": content}],
                }
            )
        body: dict[str, object] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if max_tokens is not None:
            body["generationConfig"] = {"maxOutputTokens": max_tokens}
        model_id = model.removeprefix("models/").strip()
        endpoint = (
            f"{_validated_base_url(self._definition, config.base_url)}/models/"
            f"{quote(model_id, safe='')}:generateContent"
        )
        payload = json.dumps(body).encode("utf-8")
        started = timer()
        last_error = "Google Gemini did not return a response."
        for attempt in range(max(0, max_retries) + 1):
            request = Request(
                endpoint,
                data=payload,
                headers={
                    **self._request_headers(config),
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                raw = transport(request, timeout_seconds)
                return self._parse_gemini_completion(
                    raw, model=model_id, started=started, attempt=attempt, timer=timer
                )
            except HTTPError as error:
                last_error = self._http_error_message(error, config)
                if error.code < 500 and error.code != 429:
                    break
            except (URLError, TimeoutError, OSError) as error:
                last_error = (
                    "Google Gemini could not be reached: "
                    f"{getattr(error, 'reason', error)}"
                )
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                last_error = "Google Gemini returned an invalid response."
                break
        raise ProviderVerificationError(
            _redact(last_error, config.api_key),
            model_calls=attempt + 1,
            latency_ms=round((timer() - started) * 1000),
            retries=attempt,
        )

    def _parse_gemini_completion(
        self,
        raw: bytes,
        *,
        model: str,
        started: float,
        attempt: int,
        timer: Callable[[], float],
    ) -> ProviderVerification:
        parsed = json.loads(raw.decode("utf-8"))
        parts = parsed["candidates"][0]["content"]["parts"]
        if not isinstance(parts, list):
            raise TypeError("parts is not a list")
        text = "".join(
            part["text"]
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()
        if not text:
            raise ValueError("content has no text")
        usage = parsed.get("usageMetadata") or {}
        if not isinstance(usage, dict):
            raise TypeError("usage metadata is not an object")
        input_tokens = int(usage.get("promptTokenCount", 0) or 0)
        output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
        total_tokens = int(
            usage.get("totalTokenCount", input_tokens + output_tokens) or 0
        )
        served_model = parsed.get("modelVersion")
        return ProviderVerification(
            provider=self._definition.id,
            model=served_model if isinstance(served_model, str) else model,
            content=text,
            latency_ms=round((timer() - started) * 1000),
            retries=attempt,
            usage=ProviderUsage(input_tokens, output_tokens, total_tokens),
        )

    def _request_headers(self, config: AdapterConfig) -> dict[str, str]:
        if config.headers:
            raise ProviderVerificationError(
                "Google Gemini does not accept custom headers."
            )
        return {"x-goog-api-key": (config.api_key or "").strip()}


class FakeProviderAdapter:
    """The deterministic offline provider; verification makes no requests."""

    def __init__(self) -> None:
        self._definition = find_provider("fake")

    @property
    def definition(self) -> ProviderDefinition:
        return self._definition

    def detect(
        self,
        *,
        environ: Mapping[str, str],
        configured: frozenset[str],
        probe: Callable[[str], bool] | None = None,
    ) -> tuple[DetectionHit, ...]:
        return ()

    def discover_models(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 15.0,
    ) -> tuple[str, ...]:
        return ("deterministic-fake",)

    def verify(
        self,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        timer: Callable[[], float] = monotonic,
    ) -> ProviderVerification:
        return ProviderVerification(
            provider="fake",
            model="deterministic-fake",
            latency_ms=0,
            retries=0,
            usage=ProviderUsage(),
            demo=True,
        )

    def complete(
        self,
        config: AdapterConfig,
        messages: tuple[tuple[str, str], ...],
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        timer: Callable[[], float] = monotonic,
    ) -> ChatCompletion:
        last_user = next(
            (content for role, content in reversed(messages) if role == "user"),
            "",
        )
        return ChatCompletion(
            content=f'Reckoning received your message: "{last_user}"',
            model="deterministic-fake",
            latency_ms=0,
            retries=0,
            usage=ProviderUsage(),
        )


def setup_adapter_for(provider_id: str) -> SetupProviderAdapter:
    """Build the adapter for an available registry provider."""
    definition = find_provider(provider_id)
    if not definition.available:
        raise KeyError(f"Provider is not available yet: {provider_id}")
    return candidate_adapter_for(provider_id)


def candidate_adapter_for(provider_id: str) -> SetupProviderAdapter:
    """Build a contract-test candidate without making it selectable in setup."""
    definition = find_provider(provider_id)
    if definition.id == "fake":
        return FakeProviderAdapter()
    if definition.id == "openai":
        return OpenAIProviderAdapter(definition)
    if definition.id == "deepseek":
        return DeepSeekProviderAdapter(definition)
    if definition.id == "anthropic":
        return AnthropicProviderAdapter(definition)
    if definition.id == "google-gemini":
        return GeminiProviderAdapter(definition)
    return OpenAICompatibleAdapter(definition)


def available_adapters() -> tuple[SetupProviderAdapter, ...]:
    return tuple(setup_adapter_for(item.id) for item in available_providers())


class RuntimeAdapterModelProvider:
    """Use the setup-verified adapter in the installed application."""

    def __init__(
        self,
        provider_id: str,
        config: AdapterConfig,
        *,
        transport: Transport = urlopen_transport,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._adapter = setup_adapter_for(provider_id)
        self._config = config
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._timer = timer

    def respond(self, request: object) -> ProviderResponse:
        history = tuple(
            (
                str(getattr(message, "role")),
                str(getattr(message, "content")),
            )
            for message in getattr(request, "history", ())
        )
        prompt_stack = getattr(request, "prompt_stack", None)
        layers = getattr(prompt_stack, "layers", ())
        system_content = "\n\n".join(
            f"[{getattr(layer, 'name', 'instruction')}]\n"
            f"{getattr(layer, 'content', '')}"
            for layer in layers
            if getattr(layer, "name", "") != "current_request"
        )
        messages = (("system", system_content), *history, (
            "user",
            str(getattr(request, "user_message", "")),
        ))
        try:
            completion = self._adapter.complete(
                self._config,
                messages,
                transport=self._transport,
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
                timer=self._timer,
            )
        except ProviderVerificationError as error:
            definition = self._adapter.definition
            raise ProviderFailure(
                str(error),
                provider=definition.id,
                model=self._config.model or definition.recommended_model or "unknown",
                model_calls=error.model_calls,
                latency_ms=error.latency_ms,
                retries=error.retries,
            ) from error
        return ProviderResponse(
            content=completion.content,
            provider=self._adapter.definition.id,
            model=completion.model,
            model_calls=completion.retries + 1,
            latency_ms=completion.latency_ms,
            retries=completion.retries,
            usage=completion.usage,
        )


def probe_local_endpoint(url: str, *, timeout_seconds: float = 1.5) -> bool:
    """Check one documented localhost address; nothing else is probed."""
    parsed = urlsplit(url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return False
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            response.read(256)
        return True
    except (URLError, TimeoutError, OSError):
        return False
