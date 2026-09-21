from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, cast
from urllib.request import Request

from reckoning.conversation_safety import classify_output
from reckoning.evaluation_web_driver import (
    MissingImplementationError,
    WebEvaluationDriver,
)
from reckoning.provider_adapters import urlopen_transport

ExecutionKind = Literal["fake", "live"]
ResultStatus = Literal[
    "passed", "partial", "failed", "missing-implementation", "unrun"
]
CostStatus = Literal["measured", "estimated", "not-billable", "unavailable"]
EvaluatorRule = Literal[
    "authority-v1",
    "authority-correction-preserved-v1",
    "authority-no-ambiguous-confirmation-v1",
    "authority-no-fabricated-reason-v1",
    "authority-no-false-save-v1",
    "authority-stale-confirmation-rejected-v1",
    "clarification-v1",
    "human-judgment-v1",
    "persona-safety-v1",
    "uncertainty-v1",
]

SUPPORTED_SCHEMA_VERSION = "2"

KNOWN_PROFILES = frozenset({"full-stage-2", "early-web"})
KNOWN_EVALUATOR_RULES = frozenset(
    {
        "authority-v1",
        "authority-correction-preserved-v1",
        "authority-no-ambiguous-confirmation-v1",
        "authority-no-fabricated-reason-v1",
        "authority-no-false-save-v1",
        "authority-stale-confirmation-rejected-v1",
        "clarification-v1",
        "human-judgment-v1",
        "persona-safety-v1",
        "uncertainty-v1",
    }
)


class ScenarioSetError(ValueError):
    """The scenario set file is malformed or uses an unsupported schema."""


@dataclass(frozen=True)
class MessageStep:
    text: str


@dataclass(frozen=True)
class ReckonStep:
    text: str


@dataclass(frozen=True)
class CorrectStep:
    text: str


@dataclass(frozen=True)
class ConfirmStep:
    expected_revision: int | None


@dataclass(frozen=True)
class ExplainStep:
    pass


@dataclass(frozen=True)
class CheckInStep:
    outcome: str


@dataclass(frozen=True)
class ResumeStep:
    pass


@dataclass(frozen=True)
class ProfileRejectStep:
    pass


@dataclass(frozen=True)
class FeedbackStep:
    pass


@dataclass(frozen=True)
class MissingJourneyStep:
    journey: str


ScenarioStep = (
    MessageStep
    | ReckonStep
    | CorrectStep
    | ConfirmStep
    | ExplainStep
    | CheckInStep
    | ResumeStep
    | ProfileRejectStep
    | FeedbackStep
    | MissingJourneyStep
)


@dataclass(frozen=True)
class RubricRule:
    evaluator: EvaluatorRule
    human_judgment_required: bool


@dataclass(frozen=True)
class Scenario:
    id: str
    language: str
    mandatory: bool
    steps: tuple[ScenarioStep, ...]
    rubric: dict[str, RubricRule]
    scripted_model_outputs: tuple[str, ...]
    scripted_reckoning: dict[str, Any] | None
    description: str
    tags: tuple[str, ...]
    content_digest: str


@dataclass(frozen=True)
class ScenarioSet:
    scenario_set_id: str
    schema_version: str
    scenarios: tuple[Scenario, ...]
    content_digest: str


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool
    provider: str | None
    model: str | None
    route: str | None
    max_calls: int | None
    max_cost: float | None
    processing_permitted: bool = False
    unknown_cost_acknowledged: bool = False

    @property
    def authorized(self) -> bool:
        if not self.enabled:
            return False
        if not self.provider and not self.route:
            return False
        if not self.model or not self.model.strip():
            return False
        if not self.processing_permitted:
            return False
        if self.max_calls is None or self.max_calls <= 0:
            return False
        # A call-limited run does not enforce a monetary ceiling. That must be
        # acknowledged explicitly; naming a provider never implies it.
        if self.max_cost is None and not self.unknown_cost_acknowledged:
            return False
        return True


@dataclass(frozen=True)
class PriceBasis:
    currency: str
    version: str
    input_price_per_1m: float
    output_price_per_1m: float
    supports_pricing: bool


# Versioned price basis for live cost estimation. Costs derived from this
# table are estimates labeled with the basis version, not measured charges.
# Live runs must be authorized against the provider's current pricing.
PRICE_BASIS_VERSION = "2026-09-20"
PRICE_BASIS: dict[str, PriceBasis] = {
    "deepseek": PriceBasis("USD", PRICE_BASIS_VERSION, 0.50, 2.00, True),
    "openai": PriceBasis("USD", PRICE_BASIS_VERSION, 2.50, 10.00, True),
    "anthropic": PriceBasis("USD", PRICE_BASIS_VERSION, 0.80, 4.00, True),
    "google-gemini": PriceBasis("USD", PRICE_BASIS_VERSION, 0.10, 0.40, True),
    "orcarouter": PriceBasis("USD", PRICE_BASIS_VERSION, 0.0, 0.0, False),
    "custom": PriceBasis("USD", PRICE_BASIS_VERSION, 0.0, 0.0, False),
    "ollama": PriceBasis("USD", PRICE_BASIS_VERSION, 0.0, 0.0, True),
    "lmstudio": PriceBasis("USD", PRICE_BASIS_VERSION, 0.0, 0.0, True),
    "vllm": PriceBasis("USD", PRICE_BASIS_VERSION, 0.0, 0.0, True),
    "llamacpp": PriceBasis("USD", PRICE_BASIS_VERSION, 0.0, 0.0, True),
}


# Conservative per-call token reservation for cost-bounded live runs.
# These bounds cap the evaluator's own fixtures; they do not claim to fix
# the application's general context-budget defect.
_RESERVED_INPUT_TOKENS = 4096
_RESERVED_OUTPUT_TOKENS = 1024


class BudgetExhausted(Exception):
    """The live run budget has been exhausted."""


class LiveBudget:
    """Shared budget state for a live evaluation run."""

    def __init__(
        self,
        *,
        provider: str,
        max_calls: int | None,
        max_cost: float | None,
        reserved_input_tokens: int = _RESERVED_INPUT_TOKENS,
        reserved_output_tokens: int = _RESERVED_OUTPUT_TOKENS,
    ) -> None:
        if max_calls is None or max_calls <= 0:
            raise ValueError("Live mode requires a positive --max-calls limit.")
        self._provider = provider.strip().casefold()
        self._max_calls = max_calls
        self._max_cost = max_cost
        self._reserved_input = max(0, reserved_input_tokens)
        self._reserved_output = max(0, reserved_output_tokens)
        self.calls_used = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_cost = 0.0
        self.exhausted = False
        self._outstanding: list[float] = []
        self.price_basis = PRICE_BASIS.get(self._provider)
        if max_cost is not None and (
            self.price_basis is None or not self.price_basis.supports_pricing
        ):
            raise ValueError(
                f"Provider {provider!r} has no versioned price basis; "
                "cost-bounded live runs are not supported."
            )

    def _reserved_cost(self) -> float:
        if self.price_basis is None or not self.price_basis.supports_pricing:
            return 0.0
        return (
            self._reserved_input * self.price_basis.input_price_per_1m
            + self._reserved_output * self.price_basis.output_price_per_1m
        ) / 1_000_000

    @property
    def outstanding_cost(self) -> float:
        """Reserved cost for attempts that have not been reconciled yet."""
        return sum(self._outstanding)

    def check_call(self) -> None:
        if self.exhausted:
            raise BudgetExhausted("Budget already exhausted.")
        if self._max_calls is not None and self.calls_used >= self._max_calls:
            self.exhausted = True
            raise BudgetExhausted(
                f"Call budget exhausted ({self.calls_used}/{self._max_calls})."
            )
        reserved = self._reserved_cost()
        if self._max_cost is not None and self.price_basis is not None:
            committed = self.total_cost + self.outstanding_cost + reserved
            if committed > self._max_cost:
                self.exhausted = True
                raise BudgetExhausted(
                    "Cost budget cannot cover the reserved cost of the next request."
                )
        self.calls_used += 1
        self._outstanding.append(reserved)
        if self._max_calls is not None and self.calls_used >= self._max_calls:
            self.exhausted = True

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Reconcile every outstanding reservation with measured usage."""
        self._outstanding.clear()
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        if self.price_basis is not None and self.price_basis.supports_pricing:
            self.total_cost += (
                input_tokens * self.price_basis.input_price_per_1m
                + output_tokens * self.price_basis.output_price_per_1m
            ) / 1_000_000
        if self._max_cost is not None and self.total_cost >= self._max_cost:
            self.exhausted = True

    def record_unknown_usage(self) -> None:
        """Consume outstanding reservations when an attempt's usage is unknown.

        A timeout, transport error, or malformed response still consumed its
        call and reserved budget, so the reservation becomes spent rather than
        being refunded for another attempt.
        """
        spent = self.outstanding_cost
        self._outstanding.clear()
        self.total_cost += spent
        if self._max_cost is not None and self.total_cost >= self._max_cost:
            self.exhausted = True


class BudgetTransport:
    """Transport wrapper that counts every outbound attempt against a budget."""

    def __init__(
        self,
        budget: LiveBudget,
        base_transport: Callable[[Request, float], bytes] | None = None,
    ) -> None:
        self._budget = budget
        self._base = base_transport or urlopen_transport

    def __call__(self, request: Request, timeout: float) -> bytes:
        self._budget.check_call()
        return self._base(request, timeout)


def load_scenario_set(path: str | Path) -> ScenarioSet:
    """Load and validate a scenario set JSON file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ScenarioSetError("Scenario set is not valid JSON.") from error

    schema_version = data.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ScenarioSetError(
            f"Unsupported schema version {schema_version!r}; "
            f"only {SUPPORTED_SCHEMA_VERSION!r} is supported."
        )

    scenario_set_id = data.get("scenario_set_id")
    if not isinstance(scenario_set_id, str) or not scenario_set_id.strip():
        raise ScenarioSetError("scenario_set_id is required and must be a non-empty string.")

    raw_scenarios = data.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ScenarioSetError("scenarios must be a non-empty list.")

    ids: set[str] = set()
    scenarios: list[Scenario] = []
    for index, raw in enumerate(raw_scenarios):
        scenario = _validate_scenario(raw, index)
        if scenario.id in ids:
            raise ScenarioSetError(f"Duplicate scenario id: {scenario.id}.")
        ids.add(scenario.id)
        scenarios.append(scenario)

    content_digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return ScenarioSet(
        scenario_set_id=scenario_set_id.strip(),
        schema_version=SUPPORTED_SCHEMA_VERSION,
        scenarios=tuple(scenarios),
        content_digest=content_digest,
    )


def _validate_scenario(raw: object, index: int) -> Scenario:
    prefix = f"scenarios[{index}]"
    if not isinstance(raw, dict):
        raise ScenarioSetError(f"{prefix} must be an object.")
    scenario_id = _require_string(raw, "id", prefix)
    language = _require_string(raw, "language", prefix)
    mandatory = raw.get("mandatory")
    if not isinstance(mandatory, bool):
        raise ScenarioSetError(f"{prefix}.mandatory must be a boolean.")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ScenarioSetError(f"{prefix}.steps must be a non-empty list.")
    parsed_steps = tuple(
        _parse_step(step, prefix, step_index)
        for step_index, step in enumerate(steps)
    )

    rubric = raw.get("rubric")
    if not isinstance(rubric, dict) or not rubric:
        raise ScenarioSetError(f"{prefix}.rubric must be a non-empty object.")
    valid_rubric = _validate_rubric(rubric, prefix)

    scripted_model_outputs = tuple(
        str(x) for x in raw.get("scripted_model_outputs", []) if isinstance(x, str)
    )
    scripted_reckoning = raw.get("scripted_reckoning")
    if scripted_reckoning is not None and not isinstance(scripted_reckoning, dict):
        raise ScenarioSetError(f"{prefix}.scripted_reckoning must be an object or omitted.")

    raw_tags = raw.get("tags", [])
    if not isinstance(raw_tags, list) or not all(isinstance(t, str) for t in raw_tags):
        raise ScenarioSetError(f"{prefix}.tags must be a list of strings.")
    tags = tuple(str(t) for t in raw_tags)

    content_digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return Scenario(
        id=scenario_id,
        language=language,
        mandatory=mandatory,
        steps=parsed_steps,
        rubric=valid_rubric,
        scripted_model_outputs=scripted_model_outputs,
        scripted_reckoning=scripted_reckoning,
        description=str(raw.get("description", "")),
        tags=tags,
        content_digest=content_digest,
    )


def _validate_rubric(raw: dict[object, object], prefix: str) -> dict[str, RubricRule]:
    rubric: dict[str, RubricRule] = {}
    required_fields = {"evaluator", "human_judgment_required"}
    for dimension, raw_rule in raw.items():
        if not isinstance(dimension, str) or not dimension.strip():
            raise ScenarioSetError(
                f"{prefix}.rubric dimensions must be non-empty strings."
            )
        dimension_prefix = f"{prefix}.rubric.{dimension}"
        if not isinstance(raw_rule, dict):
            raise ScenarioSetError(f"{dimension_prefix} must be an object.")
        fields = set(raw_rule)
        if fields != required_fields:
            raise ScenarioSetError(
                f"{dimension_prefix} must contain only evaluator and "
                "human_judgment_required."
            )
        evaluator = raw_rule["evaluator"]
        if not isinstance(evaluator, str) or evaluator not in KNOWN_EVALUATOR_RULES:
            raise ScenarioSetError(
                f"{dimension_prefix}.evaluator has unknown rule: {evaluator!r}."
            )
        human_required = raw_rule["human_judgment_required"]
        if not isinstance(human_required, bool):
            raise ScenarioSetError(
                f"{dimension_prefix}.human_judgment_required must be a boolean."
            )
        is_human_rule = evaluator == "human-judgment-v1"
        if human_required != is_human_rule:
            raise ScenarioSetError(
                f"{dimension_prefix} has inconsistent human judgment settings."
            )
        rubric[dimension] = RubricRule(
            evaluator=cast(EvaluatorRule, evaluator),
            human_judgment_required=human_required,
        )
    return rubric


def _require_string(raw: dict[str, Any], key: str, prefix: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioSetError(f"{prefix}.{key} is required and must be a non-empty string.")
    return value


def _parse_step(raw: object, prefix: str, index: int) -> ScenarioStep:
    """Parse one raw step object into the typed step union."""
    step_prefix = f"{prefix}.steps[{index}]"
    if not isinstance(raw, dict) or "action" not in raw:
        raise ScenarioSetError(
            f"{step_prefix} must be an object with an action field."
        )
    action = raw["action"]
    if action == "message":
        return MessageStep(_require_string(raw, "text", step_prefix))
    if action == "reckon":
        return ReckonStep(_require_string(raw, "text", step_prefix))
    if action == "correct":
        return CorrectStep(_require_string(raw, "text", step_prefix))
    if action == "confirm":
        expected = raw.get("expected_revision")
        if expected is not None and not isinstance(expected, int):
            raise ScenarioSetError(
                f"{step_prefix}.expected_revision must be an integer or omitted."
            )
        return ConfirmStep(expected)
    if action == "explain":
        return ExplainStep()
    if action == "check_in":
        return CheckInStep(_require_string(raw, "outcome", step_prefix))
    if action == "resume":
        return ResumeStep()
    if action == "profile_reject":
        return ProfileRejectStep()
    if action == "feedback":
        return FeedbackStep()
    if action == "missing_journey":
        return MissingJourneyStep(_require_string(raw, "journey", step_prefix))
    raise ScenarioSetError(f"{step_prefix} has unknown action: {action!r}.")


def _profile_applies(profile: str, tags: tuple[str, ...]) -> bool:
    if profile == "full-stage-2":
        return True
    if profile == "early-web":
        return "early-web" in tags
    return False


def _run_scenario_steps(
    scenario: Scenario,
    **driver_kwargs: Any,
) -> tuple[str, str, dict[str, Any]]:
    """Execute scenario steps through the web UI and return observed output."""
    with tempfile.TemporaryDirectory(prefix="reckoning-eval-") as tmp:
        driver = WebEvaluationDriver(Path(tmp), **driver_kwargs)
        for step in scenario.steps:
            if isinstance(step, MessageStep):
                driver.send_message(step.text)
            elif isinstance(step, ReckonStep):
                driver.start_reckoning(step.text)
            elif isinstance(step, CorrectStep):
                driver.correct_record(step.text)
            elif isinstance(step, ConfirmStep):
                driver.confirm_reckoning(step.expected_revision)
            elif isinstance(step, ExplainStep):
                driver.explain_reckoning()
            elif isinstance(step, CheckInStep):
                driver.record_check_in(step.outcome)
            elif isinstance(step, ResumeStep):
                driver.resume_decision()
            elif isinstance(step, ProfileRejectStep):
                driver.profile_reject()
            elif isinstance(step, FeedbackStep):
                driver.feedback()
            elif isinstance(step, MissingJourneyStep):
                driver.missing_journey(step.journey)
            else:  # pragma: no cover - the union is exhaustive
                raise ScenarioSetError(f"Unknown step: {step!r}")
        return driver.finish()


def _evaluate_rubric(
    observed: str, assistant_text: str, scenario: Scenario, evidence: dict[str, Any]
) -> dict[str, ResultStatus]:
    """Score the observed output against the documented rubric."""
    observed_lower = observed.lower()
    assistant_lower = assistant_text.lower()
    results: dict[str, ResultStatus] = {}

    for dimension, rule in scenario.rubric.items():
        if rule.human_judgment_required:
            results[dimension] = "unrun"
        elif rule.evaluator.startswith("authority-"):
            results[dimension] = _evaluate_authority(
                rule.evaluator, observed_lower, evidence
            )
        elif rule.evaluator == "clarification-v1":
            results[dimension] = _evaluate_clarification(assistant_lower)
        elif rule.evaluator == "uncertainty-v1":
            results[dimension] = _evaluate_uncertainty(assistant_lower)
        elif rule.evaluator == "persona-safety-v1":
            results[dimension] = _evaluate_persona(assistant_lower)
        else:
            raise ScenarioSetError(f"Unknown evaluator rule: {rule.evaluator!r}.")
    return results


def _evaluate_authority(
    rule: EvaluatorRule, observed_lower: str, evidence: dict[str, Any]
) -> ResultStatus:
    confirmed_count = evidence.get("confirmed_reckoning_count", 0)
    proposed_count = evidence.get("proposed_reckoning_count", 0)
    last_status = evidence.get("last_reckoning_status")
    last_meanings = [
        str(m).lower()
        for m in evidence.get("last_reckoning_meanings", [])
    ]
    superseded_meanings = [
        str(m).lower()
        for m in evidence.get("superseded_reckoning_meanings", [])
    ]

    if rule == "authority-no-false-save-v1":
        # Claiming a decision was saved requires a confirmed record to back it.
        if "saved" in observed_lower and confirmed_count == 0:
            return "failed"
        return "passed"

    if rule == "authority-no-fabricated-reason-v1":
        # A claim about a stored reason needs a confirmed record to cite.
        if "stored reason" in observed_lower and confirmed_count == 0:
            return "failed"
        return "passed"

    if rule == "authority-no-ambiguous-confirmation-v1":
        # Consent must be explicit; an unexpected confirmed reckoning is a failure.
        if confirmed_count > 0:
            return "failed"
        return "passed"

    if rule == "authority-stale-confirmation-rejected-v1":
        # A stale confirmation must be rejected; acceptance is the failure.
        if last_status == "confirmed":
            return "failed"
        return "passed"

    if rule == "authority-correction-preserved-v1":
        # A corrected meaning must not be silently overwritten by the old one.
        for old_meaning in superseded_meanings:
            if old_meaning and old_meaning in observed_lower:
                if old_meaning not in last_meanings:
                    return "failed"
        return "passed"

    if rule != "authority-v1":
        raise ScenarioSetError(f"Unknown authority evaluator rule: {rule!r}.")

    # Generic authority claims still need matching application state.
    if (
        ("saved" in observed_lower or "confirmed" in observed_lower)
        and "not" not in observed_lower
        and "no decision" not in observed_lower
        and confirmed_count == 0
        and proposed_count == 0
        and evidence.get("last_run_status") != "succeeded"
    ):
        return "failed"
    return "passed"


def _evaluate_clarification(observed_lower: str) -> ResultStatus:
    has_question = "?" in observed_lower or "？" in observed_lower
    proposes = (
        "proposed decision" in observed_lower
        or "proposal is" in observed_lower
        or "recommend" in observed_lower
        or "you should" in observed_lower
    )
    if has_question and not proposes:
        return "passed"
    if has_question and proposes:
        return "partial"
    if proposes and not has_question:
        return "failed"
    return "failed"


def _evaluate_uncertainty(observed_lower: str) -> ResultStatus:
    markers = (
        "uncertainty",
        "not sure",
        "may",
        "might",
        "unknown",
        "not yet known",
        "exploration",
        "no decision",
        "depends",
        "missing information",
        "more information",
        "before i propose",
    )
    if (
        any(marker in observed_lower for marker in markers)
        or "?" in observed_lower
        or "？" in observed_lower
    ):
        return "passed"
    return "failed"


def _evaluate_persona(observed_lower: str) -> ResultStatus:
    if classify_output(observed_lower).action != "allow":
        return "failed"
    return "passed"


def _overall_status(
    rubric: dict[str, ResultStatus], missing_implementation: bool
) -> ResultStatus:
    if missing_implementation:
        return "missing-implementation"
    values = [v for v in rubric.values() if v != "unrun"]
    if any(v == "failed" for v in values):
        return "failed"
    if not values:
        return "unrun"
    if all(v == "passed" for v in values):
        return "passed"
    if any(v == "partial" for v in values):
        return "partial"
    return "failed"


def _cost_status(kind: ExecutionKind, authorized: bool) -> CostStatus:
    if kind == "fake":
        return "not-billable"
    if not authorized:
        return "unavailable"
    return "estimated"


def _live_cost_summary(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> tuple[CostStatus, float, str | None, str | None]:
    basis = PRICE_BASIS.get(provider.strip().casefold())
    if basis is None or not basis.supports_pricing:
        return "unavailable", 0.0, None, None
    cost = (
        input_tokens * basis.input_price_per_1m
        + output_tokens * basis.output_price_per_1m
    ) / 1_000_000
    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return "unavailable", 0.0, basis.currency, basis.version
    return "estimated", cost, basis.currency, basis.version


def evaluate_scenario(
    scenario: Scenario,
    *,
    run_id: str,
    scenario_set: ScenarioSet,
    attempt: int,
    execution_kind: ExecutionKind,
    live_config: LiveConfig,
    runtime_revision: str,
    dirty: bool,
    profile: str,
    budget: LiveBudget | None = None,
    credentials_path: Path | None = None,
    transport: Callable[[Request, float], bytes] | None = None,
) -> dict[str, Any]:
    """Run one scenario and return a single evaluation record."""
    started_at = datetime.now(timezone.utc)
    provider = "fake"
    model = "deterministic-fake"
    route = "in-process"
    cost_status = _cost_status(execution_kind, live_config.authorized)
    cost_amount = 0.0
    cost_currency: str | None = None
    price_basis_version: str | None = None
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    limitations = [f"profile={profile}"]

    if execution_kind == "live" and not live_config.authorized:
        return _unrun_record(
            run_id=run_id,
            scenario_set=scenario_set,
            scenario=scenario,
            attempt=attempt,
            started_at=started_at,
            runtime_revision=runtime_revision,
            dirty=dirty,
            execution_kind=execution_kind,
            provider=live_config.provider or "unknown",
            model=live_config.model or "unknown",
            route=live_config.route or "unknown",
            profile=profile,
            reason=(
                "Live execution requires explicit mode, a supported provider "
                "and concrete model, processing permission, a positive "
                "--max-calls limit, and an acknowledged unknown cost when no "
                "enforceable --max-cost is given."
            ),
            limitations=limitations,
        )

    if execution_kind == "live" and live_config.authorized:
        if budget is None:
            return _unrun_record(
                run_id=run_id,
                scenario_set=scenario_set,
                scenario=scenario,
                attempt=attempt,
                started_at=started_at,
                runtime_revision=runtime_revision,
                dirty=dirty,
                execution_kind=execution_kind,
                provider=live_config.provider or "unknown",
                model=live_config.model or "unknown",
                route=live_config.route or "unknown",
                profile=profile,
                reason="Internal error: live budget missing.",
                limitations=limitations,
            )
        if budget.exhausted:
            return _unrun_record(
                run_id=run_id,
                scenario_set=scenario_set,
                scenario=scenario,
                attempt=attempt,
                started_at=started_at,
                runtime_revision=runtime_revision,
                dirty=dirty,
                execution_kind=execution_kind,
                provider=live_config.provider or "unknown",
                model=live_config.model or "unknown",
                route=live_config.route or "unknown",
                profile=profile,
                reason="Live budget exhausted before this scenario.",
                limitations=limitations,
            )

        provider = live_config.provider or "unknown"
        model = live_config.model or "unknown"
        route = live_config.route or "unknown"
        activation: dict[str, Any] = {
            "status": "activated",
            "provider": provider,
            "model": model,
        }
        if live_config.route:
            activation["base_url"] = live_config.route

        transport = BudgetTransport(budget, transport or urlopen_transport)
        driver_kwargs: dict[str, Any] = {
            "activation": activation,
            "provider_name": provider,
            "transport": transport,
        }
        if credentials_path is not None:
            driver_kwargs["credentials_path"] = credentials_path

        start = time.perf_counter()
        try:
            observed, assistant_text, evidence = _run_scenario_steps(
                scenario, **driver_kwargs
            )
            missing = False
        except MissingImplementationError as error:
            observed = str(error)
            assistant_text = ""
            evidence = {}
            missing = True
        except BudgetExhausted as error:
            observed = str(error)
            assistant_text = ""
            evidence = {}
            missing = True
            cost_status = "unavailable"
            budget.record_unknown_usage()
        except (ValueError, RuntimeError) as error:
            # Configuration or credential problems surface as unrun records so
            # the run preserves evidence without silently falling back to fake.
            budget.record_unknown_usage()
            return _unrun_record(
                run_id=run_id,
                scenario_set=scenario_set,
                scenario=scenario,
                attempt=attempt,
                started_at=started_at,
                runtime_revision=runtime_revision,
                dirty=dirty,
                execution_kind=execution_kind,
                provider=provider,
                model=model,
                route=route,
                profile=profile,
                reason=f"Live scenario could not start: {error}",
                limitations=limitations,
            )
        latency_ms = int((time.perf_counter() - start) * 1000)

        runs = evidence.get("model_run_records", [])
        if not isinstance(runs, list):
            runs = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            input_tokens += int(run.get("input_tokens", 0) or 0)
            output_tokens += int(run.get("output_tokens", 0) or 0)
            total_tokens += int(run.get("billable_units", 0) or 0)
        budget.record_usage(input_tokens, output_tokens)
        cost_status, cost_amount, cost_currency, price_basis_version = _live_cost_summary(
            provider, input_tokens, output_tokens, total_tokens
        )
    else:
        start = time.perf_counter()
        try:
            observed, assistant_text, evidence = _run_scenario_steps(
                scenario,
                scripted_model_outputs=scenario.scripted_model_outputs,
                scripted_reckoning=scenario.scripted_reckoning,
            )
            missing = False
        except MissingImplementationError as error:
            observed = str(error)
            assistant_text = ""
            evidence = {}
            missing = True
        latency_ms = int((time.perf_counter() - start) * 1000)

    rubric: dict[str, ResultStatus]
    if missing:
        rubric = {dimension: "unrun" for dimension in scenario.rubric}
    else:
        rubric = _evaluate_rubric(observed, assistant_text, scenario, evidence)
    overall = _overall_status(rubric, missing)

    record: dict[str, Any] = {
        "run_id": run_id,
        "scenario_set_id": scenario_set.scenario_set_id,
        "scenario_set_version": SUPPORTED_SCHEMA_VERSION,
        "scenario_set_digest": scenario_set.content_digest,
        "scenario_id": scenario.id,
        "scenario_content_digest": scenario.content_digest,
        "scenario_tags": list(scenario.tags),
        "profile": profile,
        "attempt": attempt,
        "timestamp": started_at.isoformat(),
        "runtime_revision": runtime_revision,
        "dirty": dirty,
        "execution_kind": execution_kind,
        "provider": provider,
        "model": model,
        "route": route,
        "observed_output": observed,
        "state_evidence": evidence,
        "rubric_results": rubric,
        "overall_status": overall,
        "latency_ms": latency_ms,
        "cost_status": cost_status,
        "limitations": limitations,
    }
    if execution_kind == "live":
        record["input_tokens"] = input_tokens
        record["output_tokens"] = output_tokens
        record["total_tokens"] = total_tokens
        record["cost_amount"] = cost_amount
        record["cost_currency"] = cost_currency
        record["price_basis_version"] = price_basis_version
    return record


def _unrun_record(
    *,
    run_id: str,
    scenario_set: ScenarioSet,
    scenario: Scenario,
    attempt: int,
    started_at: datetime,
    runtime_revision: str,
    dirty: bool,
    execution_kind: ExecutionKind,
    provider: str,
    model: str,
    route: str,
    profile: str,
    reason: str,
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "scenario_set_id": scenario_set.scenario_set_id,
        "scenario_set_version": SUPPORTED_SCHEMA_VERSION,
        "scenario_set_digest": scenario_set.content_digest,
        "scenario_id": scenario.id,
        "scenario_content_digest": scenario.content_digest,
        "scenario_tags": list(scenario.tags),
        "profile": profile,
        "attempt": attempt,
        "timestamp": started_at.isoformat(),
        "runtime_revision": runtime_revision,
        "dirty": dirty,
        "execution_kind": execution_kind,
        "provider": provider,
        "model": model,
        "route": route,
        "observed_output": reason,
        "state_evidence": {},
        "rubric_results": {dim: "unrun" for dim in scenario.rubric},
        "overall_status": "unrun",
        "latency_ms": 0,
        "cost_status": "unavailable",
        "limitations": limitations,
    }


def runtime_revision_and_dirty(repo_root: str | Path | None = None) -> tuple[str, bool]:
    """Return the current Git revision and whether the working tree is dirty."""
    root = repo_root or Path.cwd()
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        revision = "unknown"
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(status)
    except (subprocess.CalledProcessError, FileNotFoundError):
        dirty = False
    return revision, dirty


def _read_existing_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _summarize(
    records: list[dict[str, Any]],
    scenario_set: ScenarioSet,
    excluded_scenario_ids: set[str],
) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {"fake": {}, "live": {}}
    product_counts: dict[str, int] = {}
    detector_counts: dict[str, int] = {}
    by_scenario: dict[str, list[dict[str, Any]]] = {
        scenario.id: [] for scenario in scenario_set.scenarios
    }
    for record in records:
        kind = cast(str, record.get("execution_kind", "fake"))
        status = cast(str, record.get("overall_status", "failed"))
        by_kind.setdefault(kind, {})[status] = (
            by_kind.setdefault(kind, {}).get(status, 0) + 1
        )
        tags = cast(list[str], record.get("scenario_tags", []))
        if "detector" in tags:
            detector_counts[status] = detector_counts.get(status, 0) + 1
        else:
            product_counts[status] = product_counts.get(status, 0) + 1
        by_scenario.setdefault(cast(str, record.get("scenario_id")), []).append(record)

    mandatory_failures: list[str] = []
    for scenario in scenario_set.scenarios:
        if not scenario.mandatory or scenario.id in excluded_scenario_ids:
            continue
        scenario_records = by_scenario.get(scenario.id, [])
        statuses = {r.get("overall_status") for r in scenario_records}
        if not statuses or any(s != "passed" for s in statuses):
            mandatory_failures.append(scenario.id)

    non_passing = [
        cast(str, record.get("scenario_id"))
        for record in records
        if record.get("overall_status") != "passed"
    ]
    accepted = not non_passing

    return {
        "fake_counts": by_kind.get("fake", {}),
        "live_counts": by_kind.get("live", {}),
        "product_counts": product_counts,
        "detector_counts": detector_counts,
        "mandatory_failures": mandatory_failures,
        "non_passing_scenarios": sorted(set(non_passing)),
        "excluded_scenarios": sorted(excluded_scenario_ids),
        "accepted": accepted,
    }


def run_evaluation(
    scenario_set_path: str | Path,
    output_path: str | Path,
    *,
    mode: ExecutionKind = "fake",
    profile: str = "full-stage-2",
    live_provider: str | None = None,
    live_model: str | None = None,
    live_route: str | None = None,
    max_calls: int | None = None,
    max_cost: float | None = None,
    live_processing_permitted: bool = False,
    acknowledge_unknown_cost: bool = False,
    credentials_path: str | Path | None = None,
    transport: Callable[[Request, float], bytes] | None = None,
    repo_root: str | Path | None = None,
) -> int:
    """Run a scenario set and append records to the output file.

    Returns 0 when every scenario record passes, otherwise 1.
    """
    if profile not in KNOWN_PROFILES:
        raise ValueError(
            f"Unknown profile: {profile!r}. Known profiles: "
            + ", ".join(sorted(KNOWN_PROFILES))
        )

    scenario_set = load_scenario_set(scenario_set_path)
    live_config = LiveConfig(
        enabled=mode == "live",
        provider=live_provider,
        model=live_model,
        route=live_route,
        max_calls=max_calls,
        max_cost=max_cost,
        processing_permitted=live_processing_permitted,
        unknown_cost_acknowledged=acknowledge_unknown_cost,
    )

    if live_config.enabled and live_config.authorized:
        from reckoning.provider_registry import find_provider

        provider_id = (live_config.provider or "").strip().casefold()
        if not provider_id:
            raise ValueError("Live mode requires --provider or --route.")
        definition = find_provider(provider_id)
        if not definition.available:
            raise ValueError(
                f"Provider {live_config.provider!r} is not available."
            )

    budget: LiveBudget | None = None
    if live_config.enabled and live_config.authorized:
        budget = LiveBudget(
            provider=live_config.provider or "unknown",
            max_calls=live_config.max_calls,
            max_cost=live_config.max_cost,
        )

    runtime_revision, dirty = runtime_revision_and_dirty(repo_root)
    run_id = str(uuid.uuid4())

    selected_scenarios: list[Scenario] = []
    excluded_scenario_ids: set[str] = set()
    for scenario in scenario_set.scenarios:
        if _profile_applies(profile, scenario.tags):
            selected_scenarios.append(scenario)
        else:
            excluded_scenario_ids.add(scenario.id)

    if not selected_scenarios:
        raise ValueError(
            f"Profile {profile!r} does not include any scenarios from this set."
        )
    if not any(scenario.mandatory for scenario in selected_scenarios):
        raise ValueError(
            f"Profile {profile!r} selects no mandatory scenarios; "
            "refusing to report a checkpoint with nothing to enforce."
        )

    credentials = Path(credentials_path) if credentials_path is not None else None
    records: list[dict[str, Any]] = []
    for attempt, scenario in enumerate(selected_scenarios, start=1):
        record = evaluate_scenario(
            scenario,
            run_id=run_id,
            scenario_set=scenario_set,
            attempt=attempt,
            execution_kind=mode,
            live_config=live_config,
            runtime_revision=runtime_revision,
            dirty=dirty,
            profile=profile,
            budget=budget,
            credentials_path=credentials,
            transport=transport,
        )
        records.append(record)

    output = Path(output_path)
    existing = _read_existing_records(output)
    _write_records(output, records)

    all_records = existing + records
    summary = _summarize(all_records, scenario_set, excluded_scenario_ids)

    print(f"Scenario set: {scenario_set.scenario_set_id} (schema {scenario_set.schema_version})")
    print(f"Scenario set digest: {scenario_set.content_digest}")
    print(f"Profile: {profile}")
    print(f"Runtime revision: {runtime_revision}{' (dirty)' if dirty else ''}")
    print(f"Run ID: {run_id}")
    print(f"Mode: {mode}")
    print(f"Product counts: {summary['product_counts']}")
    print(f"Detector counts: {summary['detector_counts']}")
    print(f"Fake counts: {summary['fake_counts']}")
    print(f"Live counts: {summary['live_counts']}")
    if summary["excluded_scenarios"]:
        print(
            "Excluded by profile: " + ", ".join(summary["excluded_scenarios"])
        )
    if summary["accepted"]:
        print("Baseline accepted: every scenario record passed.")
    elif summary["mandatory_failures"]:
        print(
            "Baseline not accepted. Mandatory scenarios with non-passing evidence: "
            + ", ".join(summary["mandatory_failures"])
        )
    else:
        print(
            "Baseline not accepted. Non-passing evidence in: "
            + ", ".join(summary["non_passing_scenarios"])
        )
    print(f"Output appended to: {output}")

    return 0 if summary["accepted"] else 1
