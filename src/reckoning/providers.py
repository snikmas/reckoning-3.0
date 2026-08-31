from __future__ import annotations

from dataclasses import dataclass
import json
from time import monotonic
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from reckoning.continuity import (
    Evidence,
    Inference,
    MaterialQuestion,
    PersonalRecordProposal,
    ReckoningDraft,
    ReckoningProviderResult,
    SourcedFact,
)


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
    @property
    def user_message(self) -> str: ...

    @property
    def history(self) -> tuple[object, ...]: ...

    @property
    def prompt_stack(self) -> object: ...


Transport = Callable[[Request, float], bytes]


def _urlopen_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read()


class OrcaRouterModelProvider:
    """Small OrcaRouter Chat Completions adapter with bounded retries."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "orcarouter/auto",
        base_url: str = "https://api.orcarouter.ai/v1",
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        transport: Transport = _urlopen_transport,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "ORCAROUTER_API_KEY is required for the OrcaRouter provider."
            )
        if not model.strip():
            raise ValueError("MODEL cannot be empty.")
        parsed_base_url = urlsplit(base_url)
        if (
            parsed_base_url.scheme != "https"
            or parsed_base_url.hostname != "api.orcarouter.ai"
        ):
            raise ValueError(
                "BASE_URL must use the official https://api.orcarouter.ai host."
            )
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
                    provider="orcarouter",
                    model=str(parsed.get("model", self._model)),
                    model_calls=model_calls,
                    latency_ms=round((self._timer() - started) * 1000),
                    retries=attempt,
                    usage=usage,
                )
            except HTTPError as error:
                last_error = self._http_error_message(error)
                if error.code < 500 and error.code != 429:
                    break
            except (URLError, TimeoutError) as error:
                reason = error.reason if isinstance(error, URLError) else error
                last_error = f"OrcaRouter could not be reached: {reason}"
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                last_error = "OrcaRouter returned an invalid response."
                break

        raise ProviderFailure(
            last_error,
            provider="orcarouter",
            model=self._model,
            model_calls=model_calls,
            latency_ms=round((self._timer() - started) * 1000),
            retries=max(0, model_calls - 1),
        )

    def _http_error_message(self, error: HTTPError) -> str:
        fallback = f"OrcaRouter returned HTTP {error.code}."
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
        safe_message = message.replace(self._api_key, "[redacted]")[:500]
        code_text = f" ({code})" if code else ""
        return f"OrcaRouter returned HTTP {error.code}{code_text}: {safe_message}"

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


@dataclass(frozen=True)
class _ReckoningPromptLayer:
    name: str
    content: str


@dataclass(frozen=True)
class _ReckoningPromptStack:
    layers: tuple[_ReckoningPromptLayer, ...]


@dataclass(frozen=True)
class _ReckoningModelRequest:
    user_message: str
    history: tuple[object, ...]
    prompt_stack: _ReckoningPromptStack


class OrcaRouterReckoningProvider:
    """Turns one unstructured situation into the typed first reckoning."""

    _record_types = {
        "profile_fact",
        "current_state",
        "direction",
        "goal",
        "decision",
        "preference",
        "boundary",
    }

    def __init__(self, model: OrcaRouterModelProvider) -> None:
        self._model = model

    def reckon(self, unstructured_input: str) -> ReckoningProviderResult:
        request = _ReckoningModelRequest(
            user_message=unstructured_input,
            history=(),
            prompt_stack=_ReckoningPromptStack(
                (
                    _ReckoningPromptLayer(
                        "protected_product_contract",
                        "Be truthful. Challenge reasoning, never personal worth. "
                        "Do not manipulate dependence, demand exclusivity, encourage "
                        "isolation, or override the user's final authority.",
                    ),
                    _ReckoningPromptLayer(
                        "structured_reckoning",
                        self._structured_output_contract(),
                    ),
                )
            ),
        )
        response = self._model.respond(request)
        try:
            data = json.loads(response.content)
            draft = self._draft_from_data(data, unstructured_input)
            draft.validate()
        except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            raise ProviderFailure(
                "OrcaRouter returned an invalid structured reckoning.",
                provider=response.provider,
                model=response.model,
                model_calls=response.model_calls,
                latency_ms=response.latency_ms,
                retries=response.retries,
            ) from error
        return ReckoningProviderResult(
            draft=draft,
            provider=response.provider,
            model=response.model,
            model_calls=response.model_calls,
            latency_ms=response.latency_ms,
            retries=response.retries,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            billable_units=response.usage.total_tokens,
        )

    @classmethod
    def _draft_from_data(
        cls, data: object, source_input: str
    ) -> ReckoningDraft:
        if not isinstance(data, dict):
            raise ValueError("The structured reckoning must be a JSON object.")
        evidence = Evidence(
            id="current-message",
            source="current user message",
            content=source_input,
        )
        questions_data = data.get("questions", [])
        known_data = data.get("known", [])
        inferences_data = data.get("inferences", [])
        proposals_data = data.get("proposed_records", [])
        if not all(
            isinstance(value, list)
            for value in (
                questions_data,
                known_data,
                inferences_data,
                proposals_data,
            )
        ):
            raise ValueError("Structured reckoning collections must be arrays.")
        if any(not isinstance(item, dict) for item in questions_data):
            raise ValueError("A structured reckoning question is invalid.")
        if any(not isinstance(item, dict) for item in inferences_data):
            raise ValueError("A structured reckoning inference is invalid.")
        proposals: list[PersonalRecordProposal] = []
        for item in proposals_data:
            if not isinstance(item, dict) or item.get("record_type") not in cls._record_types:
                raise ValueError("The structured reckoning has an invalid record type.")
            proposals.append(
                PersonalRecordProposal(
                    record_type=item["record_type"],
                    meaning=str(item["meaning"]),
                    evidence_ids=(evidence.id,),
                )
            )
        return ReckoningDraft(
            conflict=str(data["conflict"]),
            questions=tuple(
                MaterialQuestion(
                    text=str(item["text"]),
                    effect_on_recommendation=str(
                        item["effect_on_recommendation"]
                    ),
                )
                for item in questions_data
            ),
            matters_now=cls._string_tuple(data["matters_now"]),
            maintained=cls._string_tuple(data["maintained"]),
            parked=cls._string_tuple(data["parked"]),
            uncertainties=cls._string_tuple(data["uncertainties"]),
            known=tuple(
                SourcedFact(str(item), (evidence.id,)) for item in known_data
            ),
            inferences=tuple(
                Inference(
                    text=str(item["text"]),
                    evidence_ids=(evidence.id,),
                    uncertainty=str(item["uncertainty"]),
                )
                for item in inferences_data
            ),
            evidence=(evidence,),
            next_step=str(data["next_step"]),
            proposed_records=tuple(proposals),
        )

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise ValueError("A structured reckoning list is invalid.")
        return tuple(str(item) for item in value)

    @staticmethod
    def _structured_output_contract() -> str:
        return (
            "Return only one JSON object with keys conflict, questions, "
            "matters_now, maintained, parked, uncertainties, known, inferences, "
            "next_step, and proposed_records. Ask at most three questions. Each "
            "question has text and effect_on_recommendation. known is an array of "
            "directly supported strings. inferences is an array of objects with "
            "text and uncertainty. proposed_records is an array of objects with "
            "record_type and meaning. Valid record_type values are profile_fact, "
            "current_state, direction, goal, decision, preference, and boundary."
        )
