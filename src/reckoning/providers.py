from __future__ import annotations

from dataclasses import dataclass
import json
from time import monotonic
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    provider: str
    model: str
    model_calls: int
    latency_ms: int
    retries: int
    usage: ProviderUsage


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        model_calls: int,
        latency_ms: int,
        retries: int,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.model_calls = model_calls
        self.latency_ms = latency_ms
        self.retries = retries


class ModelRequestLike(Protocol):
    user_message: str
    history: tuple[object, ...]
    prompt_stack: object


Transport = Callable[[Request, float], bytes]


def _urlopen_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read()


class DeepSeekModelProvider:
    """Small DeepSeek Chat Completions adapter with bounded retries."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        transport: Transport = _urlopen_transport,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DEEPSEEK_API_KEY is required for the DeepSeek provider.")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        self._api_key = api_key
        self._model = model
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._transport = transport
        self._timer = timer

    def respond(self, request: ModelRequestLike) -> ProviderResponse:
        payload = json.dumps(
            {
                "model": self._model,
                "messages": self._messages(request),
                "stream": False,
            }
        ).encode("utf-8")
        started = self._timer()
        model_calls = 0
        last_error = "The provider did not return a response."

        for attempt in range(self._max_retries + 1):
            model_calls += 1
            provider_request = Request(
                self._endpoint,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                raw_response = self._transport(
                    provider_request, self._timeout_seconds
                )
                parsed = json.loads(raw_response.decode("utf-8"))
                content = str(parsed["choices"][0]["message"]["content"]).strip()
                if not content:
                    raise ValueError("The provider returned empty content.")
                usage_data = parsed.get("usage", {})
                usage = ProviderUsage(
                    input_tokens=int(usage_data.get("prompt_tokens", 0)),
                    output_tokens=int(usage_data.get("completion_tokens", 0)),
                    total_tokens=int(usage_data.get("total_tokens", 0)),
                )
                return ProviderResponse(
                    content=content,
                    provider="deepseek",
                    model=str(parsed.get("model", self._model)),
                    model_calls=model_calls,
                    latency_ms=round((self._timer() - started) * 1000),
                    retries=attempt,
                    usage=usage,
                )
            except HTTPError as error:
                last_error = f"DeepSeek returned HTTP {error.code}."
                if error.code < 500 and error.code != 429:
                    break
            except (URLError, TimeoutError) as error:
                reason = error.reason if isinstance(error, URLError) else error
                last_error = f"DeepSeek could not be reached: {reason}"
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                last_error = "DeepSeek returned an invalid response."
                break

        raise ProviderFailure(
            last_error,
            provider="deepseek",
            model=self._model,
            model_calls=model_calls,
            latency_ms=round((self._timer() - started) * 1000),
            retries=max(0, model_calls - 1),
        )

    @staticmethod
    def _messages(request: ModelRequestLike) -> list[dict[str, str]]:
        layers = getattr(request.prompt_stack, "layers", ())
        system_content = "\n\n".join(
            f"[{getattr(layer, 'name', 'instruction')}]\n{getattr(layer, 'content', '')}"
            for layer in layers
            if getattr(layer, "name", "") != "current_request"
        )
        messages = [{"role": "system", "content": system_content}]
        messages.extend(
            {
                "role": str(getattr(message, "role")),
                "content": str(getattr(message, "content")),
            }
            for message in request.history
        )
        messages.append({"role": "user", "content": request.user_message})
        return messages
