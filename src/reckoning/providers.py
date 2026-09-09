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


class ResponseProviderLike(Protocol):
    def respond(self, request: ModelRequestLike) -> ProviderResponse: ...


Transport = Callable[[Request, float], bytes]


def _urlopen_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        return response.read()


class ProviderKeyVerificationError(RuntimeError):
    """A safe-to-display provider API-key verification failure."""


_PROVIDER_VERIFICATION_ENDPOINTS = {
    "orcarouter": ("OrcaRouter", "https://api.orcarouter.ai/v1"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com"),
}


def verify_provider_api_key(
    provider_name: str,
    api_key: str,
    *,
    transport: Transport = _urlopen_transport,
    timeout_seconds: float = 15.0,
) -> None:
    """Check an API key against the provider's model-listing endpoint."""
    try:
        display_name, base_url = _PROVIDER_VERIFICATION_ENDPOINTS[provider_name]
    except KeyError:
        raise ValueError(f"Unsupported model provider: {provider_name}") from None
    request = Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        transport(request, timeout_seconds)
    except HTTPError as error:
        if error.code in (401, 403):
            raise ProviderKeyVerificationError(
                f"{display_name} rejected the API key. "
                "Check the key and run setup again."
            ) from error
        raise ProviderKeyVerificationError(
            f"{display_name} returned HTTP {error.code} during API-key verification."
        ) from error
    except (URLError, TimeoutError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        raise ProviderKeyVerificationError(
            f"{display_name} could not be reached: {reason}"
        ) from error


class OrcaRouterModelProvider:
    """Compatibility wrapper around the shared registry-backed adapter."""

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
        _provider_name: str = "orcarouter",
        _display_name: str = "OrcaRouter",
        _api_key_name: str = "ORCAROUTER_API_KEY",
        _required_host: str = "api.orcarouter.ai",
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                f"{_api_key_name} is required for the {_display_name} provider."
            )
        if not model.strip():
            raise ValueError("MODEL cannot be empty.")
        parsed_base_url = urlsplit(base_url)
        if (
            parsed_base_url.scheme != "https"
            or parsed_base_url.hostname != _required_host
        ):
            raise ValueError(
                f"BASE_URL must use the official https://{_required_host} host."
            )
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative.")
        from reckoning.provider_adapters import (
            AdapterConfig,
            RuntimeAdapterModelProvider,
        )

        self._delegate = RuntimeAdapterModelProvider(
            _provider_name,
            AdapterConfig(api_key=api_key, base_url=base_url, model=model),
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            timer=timer,
        )

    def respond(self, request: ModelRequestLike) -> ProviderResponse:
        return self._delegate.respond(request)


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

    def __init__(self, model: ResponseProviderLike) -> None:
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
                "The model provider returned an invalid structured reckoning.",
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


class DeepSeekModelProvider(OrcaRouterModelProvider):
    """DeepSeek's OpenAI-compatible endpoint retained for #38 evidence."""

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
        super().__init__(
            api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            transport=transport,
            timer=timer,
            _provider_name="deepseek",
            _display_name="DeepSeek",
            _api_key_name="DEEPSEEK_API_KEY",
            _required_host="api.deepseek.com",
        )


class DeepSeekReckoningProvider(OrcaRouterReckoningProvider):
    """Typed continuity adapter using the retained DeepSeek model provider."""

    def __init__(self, model: DeepSeekModelProvider) -> None:
        super().__init__(model)
