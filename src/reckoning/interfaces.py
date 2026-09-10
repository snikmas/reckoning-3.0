from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Literal, Protocol

from reckoning.json_store import atomic_write_json, read_json


ChannelName = Literal["web", "telegram"]
ActivityState = Literal["idle", "listening", "reasoning", "executing"]
VisualState = Literal[
    "idle", "listening", "reasoning", "approval", "execution", "warning", "degraded"
]
PlacementStatus = Literal["available", "limited", "blocked"]
InstallationProfile = Literal["local", "personal-server", "hybrid"]
Sensitivity = Literal["public", "private"]
NodeName = Literal["local", "server"]

_DEFAULT_MATTERS_NOW = ("Start with one meaningful conversation.",)
_DEFAULT_CHANGES = ("No material changes yet.",)
_DEFAULT_DECISIONS = ("Nothing is waiting for a decision.",)


@dataclass(frozen=True)
class SourcePlacement:
    category: str
    sensitivity: Sensitivity
    storage_node: NodeName
    processing_node: NodeName
    remote_approved: bool = False

    def __post_init__(self) -> None:
        if not self.category.strip():
            raise ValueError("A source category is required.")
        uses_remote_node = "server" in (self.storage_node, self.processing_node)
        if self.sensitivity == "private" and uses_remote_node and not self.remote_approved:
            raise ValueError(
                "Private source categories require explicit remote approval."
            )


class NodeAvailabilitySource(Protocol):
    def is_available(self, node: NodeName) -> bool: ...


@dataclass(frozen=True)
class PlacementOutcome:
    status: PlacementStatus
    available_categories: tuple[str, ...]
    unavailable_categories: tuple[str, ...]
    notice: str


@dataclass(frozen=True)
class PlacementPolicy:
    profile: InstallationProfile
    categories: tuple[SourcePlacement, ...]
    local_node_available: bool
    server_node_available: bool
    availability_source: NodeAvailabilitySource | None = None

    def __post_init__(self) -> None:
        category_names = tuple(item.category for item in self.categories)
        if len(category_names) != len(set(category_names)):
            raise ValueError("Each source category must have one placement rule.")

    def evaluate(
        self,
        required_categories: tuple[str, ...],
        *,
        safe_when_incomplete: bool,
    ) -> PlacementOutcome:
        rules = {item.category: item for item in self.categories}
        available: list[str] = []
        unavailable: list[str] = []
        for category in required_categories:
            rule = rules.get(category)
            if rule is None or not self._nodes_available(rule):
                unavailable.append(category)
            else:
                available.append(category)

        if not unavailable:
            return PlacementOutcome(
                "available",
                tuple(available),
                (),
                f"{self.profile} placement is fully available.",
            )

        missing = ", ".join(unavailable)
        if safe_when_incomplete:
            return PlacementOutcome(
                "limited",
                tuple(available),
                tuple(unavailable),
                f"Limited mode: unavailable context: {missing}.",
            )
        return PlacementOutcome(
            "blocked",
            tuple(available),
            tuple(unavailable),
            f"Work is blocked because required context is unavailable: {missing}.",
        )

    def current_outcome(self) -> PlacementOutcome:
        return self.evaluate(
            tuple(item.category for item in self.categories),
            safe_when_incomplete=True,
        )

    @property
    def required_categories(self) -> tuple[str, ...]:
        return tuple(item.category for item in self.categories)

    def _nodes_available(self, placement: SourcePlacement) -> bool:
        required_nodes = {placement.storage_node, placement.processing_node}
        return not (
            ("local" in required_nodes and not self._node_available("local"))
            or ("server" in required_nodes and not self._node_available("server"))
        )

    def _node_available(self, node: NodeName) -> bool:
        if self.availability_source is not None:
            return self.availability_source.is_available(node)
        return (
            self.local_node_available
            if node == "local"
            else self.server_node_available
        )


@dataclass(frozen=True)
class ChannelMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChannelSession:
    channel: ChannelName
    session_id: str = ""
    messages: tuple[ChannelMessage, ...] = ()


@dataclass(frozen=True)
class RunReceipt:
    id: str
    occurred_at: datetime
    status: str
    summary: str
    evidence: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    cost_units: int = 0
    routine_health: tuple[str, ...] = ()
    connector_health: tuple[str, ...] = ()
    suppressed_findings: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationalFailure:
    id: str
    run_id: str
    summary: str
    occurred_at: datetime


@dataclass(frozen=True)
class InterfaceState:
    returning_user: bool = False
    matters_now: tuple[str, ...] = _DEFAULT_MATTERS_NOW
    changes: tuple[str, ...] = _DEFAULT_CHANGES
    decisions: tuple[str, ...] = _DEFAULT_DECISIONS
    activity: ActivityState = "idle"
    confirmed_records: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    pending_approvals: tuple[str, ...] = ()
    receipts: tuple[RunReceipt, ...] = ()
    failures: tuple[OperationalFailure, ...] = ()
    sessions: tuple[ChannelSession, ...] = ()


class InterfaceRepository(Protocol):
    def load(self) -> InterfaceState: ...

    def save(self, state: InterfaceState) -> None: ...


class InMemoryInterfaceRepository:
    def __init__(self, state: InterfaceState | None = None) -> None:
        self._state = state or InterfaceState()

    def load(self) -> InterfaceState:
        return self._state

    def save(self, state: InterfaceState) -> None:
        self._state = state


class JsonFileInterfaceRepository:
    """Durable shared state for the interface slice."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> InterfaceState:
        data = read_json(
            self._path,
            default={"schema_version": 1, "state": {}},
        )
        if data.get("schema_version") != 1 or not isinstance(data.get("state"), dict):
            raise RuntimeError("Unsupported interface storage schema.")
        try:
            return _state_from_data(data["state"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Interface storage is invalid.") from error

    def save(self, state: InterfaceState) -> None:
        atomic_write_json(
            self._path,
            {"schema_version": 1, "state": _state_to_data(state)},
        )


@dataclass(frozen=True)
class ChannelRequest:
    channel: ChannelName
    text: str
    recent_history: tuple[ChannelMessage, ...]
    confirmed_records: tuple[str, ...]
    permissions: tuple[str, ...]
    available_categories: tuple[str, ...]
    limited: bool
    required_processing_categories: tuple[str, ...] = ()
    safe_when_incomplete: bool = True


class ChannelResponder(Protocol):
    def respond(self, request: ChannelRequest) -> str: ...


class MessageApplication(Protocol):
    def respond_with_channel_context(
        self,
        text: str,
        history: tuple[tuple[Literal["user", "assistant"], str], ...],
        confirmed_records: tuple[str, ...],
        permissions: tuple[str, ...],
        *,
        required_processing_categories: tuple[str, ...] = (),
        available_processing_categories: tuple[str, ...] | None = None,
        safe_when_incomplete: bool = True,
    ) -> object: ...


class ApplicationChannelResponder:
    """Connect the established conversation boundary to a channel interface."""

    def __init__(self, application: MessageApplication) -> None:
        self._application = application

    def respond(self, request: ChannelRequest) -> str:
        history = tuple(
            (message.role, message.content) for message in request.recent_history
        )
        response = self._application.respond_with_channel_context(
            request.text,
            history,
            request.confirmed_records,
            request.permissions,
            required_processing_categories=request.required_processing_categories,
            available_processing_categories=_processing_categories_for(
                request.available_categories
            ),
            safe_when_incomplete=request.safe_when_incomplete,
        )
        content = getattr(response, "content", None)
        if not isinstance(content, str):
            raise RuntimeError("The conversation boundary returned an invalid message.")
        return content


@dataclass(frozen=True)
class ChannelReply:
    text: str
    placement: PlacementOutcome


@dataclass(frozen=True)
class ChannelStatus:
    health: str
    placement: PlacementOutcome
    pending_approvals: tuple[str, ...]


@dataclass(frozen=True)
class HomeView:
    matters_now: tuple[str, ...]
    changes: tuple[str, ...]
    decisions: tuple[str, ...]
    health: str
    placement: PlacementOutcome
    visual_state: VisualState


@dataclass(frozen=True)
class ControlView:
    receipts: tuple[RunReceipt, ...]
    failures: tuple[OperationalFailure, ...]
    evidence: tuple[str, ...]
    memory: tuple[str, ...]
    permissions: tuple[str, ...]
    approvals: tuple[str, ...]
    cost_units: int
    routine_health: tuple[str, ...]
    connector_health: tuple[str, ...]
    suppressed_findings: tuple[str, ...]
    tools: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True)
class RunExplanation:
    receipt: RunReceipt
    failure: OperationalFailure | None


@dataclass(frozen=True)
class OperationalSnapshot:
    receipts: tuple[RunReceipt, ...] = ()
    failures: tuple[OperationalFailure, ...] = ()
    evidence: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    routine_health: tuple[str, ...] = ()
    connector_health: tuple[str, ...] = ()
    suppressed_findings: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()


class OperationalRecordSource(Protocol):
    def snapshot(self) -> OperationalSnapshot: ...


class EmptyOperationalRecordSource:
    def snapshot(self) -> OperationalSnapshot:
        return OperationalSnapshot()


class ConfirmationHandler(Protocol):
    def pending_confirmation_ids(self) -> tuple[str, ...]: ...

    def confirm(self, confirmation_id: str) -> bool: ...


class NoDurableConfirmations:
    def pending_confirmation_ids(self) -> tuple[str, ...]:
        return ()

    def confirm(self, confirmation_id: str) -> bool:
        del confirmation_id
        return False


class ReckoningInterfaceApplication:
    """Channel-independent boundary for task areas and remote continuation."""

    def __init__(
        self,
        *,
        repository: InterfaceRepository,
        responder: ChannelResponder,
        placement: PlacementPolicy,
        operational_records: OperationalRecordSource | None = None,
        confirmations: ConfirmationHandler | None = None,
    ) -> None:
        self._repository = repository
        self._responder = responder
        self._placement = placement
        self._operational_records = operational_records or EmptyOperationalRecordSource()
        self._confirmations = confirmations or NoDurableConfirmations()

    def landing_area(self) -> Literal["home", "simon"]:
        return "home" if self._repository.load().returning_user else "simon"

    def set_activity(self, activity: ActivityState) -> None:
        self._repository.save(replace(self._repository.load(), activity=activity))

    def record_operational_run(
        self,
        receipt: RunReceipt,
        *,
        failure: OperationalFailure | None = None,
    ) -> None:
        """Publish one durable operational record to Control and Simon."""
        state = self._repository.load()
        receipts = tuple(item for item in state.receipts if item.id != receipt.id)
        failures = tuple(item for item in state.failures if item.run_id != receipt.id)
        if failure is not None:
            if failure.run_id != receipt.id:
                raise ValueError("An operational failure must identify the same run.")
            failures += (failure,)
        self._repository.save(
            replace(
                state,
                activity="idle",
                receipts=receipts + (receipt,),
                failures=failures,
            )
        )

    def home(self) -> HomeView:
        state = self._repository.load()
        status = self.status("web")
        receipts = self.control().receipts
        published_changes = (
            () if state.changes == _DEFAULT_CHANGES else state.changes
        )
        run_changes = tuple(
            f"{receipt.summary} ({receipt.status})."
            for receipt in reversed(receipts[-3:])
        )
        published_decisions = (
            () if state.decisions == _DEFAULT_DECISIONS else state.decisions
        )
        approval_decisions = tuple(
            f"Approval required: {approval_id}."
            for approval_id in state.pending_approvals
        )
        return HomeView(
            matters_now=state.matters_now or _DEFAULT_MATTERS_NOW,
            changes=_unique((*run_changes, *published_changes)) or _DEFAULT_CHANGES,
            decisions=_unique((*approval_decisions, *published_decisions))
            or _DEFAULT_DECISIONS,
            health=status.health,
            placement=status.placement,
            visual_state=self.visual_state(),
        )

    def send_channel_message(
        self,
        channel: ChannelName,
        text: str,
        *,
        session_id: str = "",
        required_categories: tuple[str, ...] | None = None,
        safe_when_incomplete: bool = True,
    ) -> ChannelReply:
        message = text.strip()
        if not message:
            raise ValueError("A message cannot be empty.")
        state = self._repository.load()
        self._repository.save(replace(state, activity="listening"))
        try:
            categories = (
                self._placement.required_categories
                if required_categories is None
                else required_categories
            )
            placement = self._placement.evaluate(
                categories,
                safe_when_incomplete=safe_when_incomplete,
            )
            if placement.status == "blocked":
                raise RuntimeError(placement.notice)

            history = self._session(state, channel, session_id).messages
            placement_categories = self._placement.required_categories
            confirmed_state_category = (
                "confirmed-state"
                if "confirmed-state" in placement_categories
                else (
                    "personal-context"
                    if "personal-context" in placement_categories
                    else None
                )
            )
            confirmed_state_available = (
                confirmed_state_category is None
                or confirmed_state_category in placement.available_categories
            )
            self._repository.save(
                replace(self._repository.load(), activity="reasoning")
            )
            response = self._responder.respond(
                ChannelRequest(
                    channel=channel,
                    text=message,
                    recent_history=history if confirmed_state_available else (),
                    confirmed_records=(
                        state.confirmed_records if confirmed_state_available else ()
                    ),
                    permissions=(
                        state.permissions if confirmed_state_available else ()
                    ),
                    available_categories=placement.available_categories,
                    limited=placement.status == "limited",
                    required_processing_categories=_processing_categories_for(
                        categories
                    ),
                    safe_when_incomplete=safe_when_incomplete,
                )
            ).strip()
            if not response:
                raise RuntimeError("The responder returned an empty response.")
            if placement.status == "limited":
                response = f"{placement.notice} {response}"

            messages = history + (
                ChannelMessage("user", message),
                ChannelMessage("assistant", response),
            )
            current_state = replace(
                self._repository.load(), returning_user=True, activity="idle"
            )
            self._save_session(current_state, channel, session_id, messages)
            return ChannelReply(response, placement)
        except Exception:
            self._repository.save(
                replace(self._repository.load(), activity="idle")
            )
            raise

    def channel_session(
        self,
        channel: ChannelName,
        *,
        session_id: str = "",
    ) -> tuple[ChannelMessage, ...]:
        return self._session(self._repository.load(), channel, session_id).messages

    def status(self, channel: ChannelName) -> ChannelStatus:
        del channel  # Placement and operational truth are shared across channels.
        state = self._repository.load()
        operational = self._operational_records.snapshot()
        control = self.control()
        placement = self._placement.current_outcome()
        if placement.status != "available":
            health = "degraded"
        elif control.failures:
            health = "warning"
        else:
            health = "healthy"
        pending_approvals = _unique(
            (
                *state.pending_approvals,
                *operational.approvals,
                *self._confirmations.pending_confirmation_ids(),
            )
        )
        return ChannelStatus(health, placement, pending_approvals)

    def confirm(self, confirmation_id: str) -> bool:
        if self._confirmations.confirm(confirmation_id):
            return True
        state = self._repository.load()
        if confirmation_id not in state.pending_approvals:
            return False
        self._repository.save(
            replace(
                state,
                pending_approvals=tuple(
                    item for item in state.pending_approvals if item != confirmation_id
                ),
            )
        )
        return True

    def deliver_routine(
        self,
        channel: ChannelName,
        text: str,
        *,
        session_id: str = "",
    ) -> ChannelMessage:
        content = text.strip()
        if not content:
            raise ValueError("A routine delivery cannot be empty.")
        state = self._repository.load()
        session = self._session(state, channel, session_id)
        delivered = ChannelMessage("assistant", content)
        self._save_session(
            state,
            channel,
            session_id,
            session.messages + (delivered,),
        )
        return delivered

    def control(self) -> ControlView:
        state = self._repository.load()
        operational = self._operational_records.snapshot()
        receipts = _merge_by_id(state.receipts, operational.receipts)
        failures = _merge_by_id(state.failures, operational.failures)
        return ControlView(
            receipts=receipts,
            failures=failures,
            evidence=_unique((*_flatten(receipts, "evidence"), *operational.evidence)),
            memory=_unique(
                (
                    *state.confirmed_records,
                    *_flatten(receipts, "memory"),
                    *operational.memory,
                )
            ),
            permissions=_unique(
                (*state.permissions, *_flatten(receipts, "permissions"), *operational.permissions)
            ),
            approvals=_unique(
                (
                    *state.pending_approvals,
                    *_flatten(receipts, "approvals"),
                    *operational.approvals,
                    *self._confirmations.pending_confirmation_ids(),
                )
            ),
            cost_units=sum(receipt.cost_units for receipt in receipts),
            routine_health=_unique(
                (*_flatten(receipts, "routine_health"), *operational.routine_health)
            ),
            connector_health=_unique(
                (*_flatten(receipts, "connector_health"), *operational.connector_health)
            ),
            suppressed_findings=_unique(
                (
                    *_flatten(receipts, "suppressed_findings"),
                    *operational.suppressed_findings,
                )
            ),
            tools=_unique((*_flatten(receipts, "tools"), *operational.tools)),
            actions=_unique((*_flatten(receipts, "actions"), *operational.actions)),
        )

    def explain_run(self, run_id: str) -> RunExplanation:
        control = self.control()
        receipt = next((item for item in control.receipts if item.id == run_id), None)
        if receipt is None:
            raise KeyError(f"Unknown run: {run_id}")
        failure = next(
            (item for item in control.failures if item.run_id == run_id),
            None,
        )
        return RunExplanation(receipt, failure)

    def ask_simon_to_explain(self, run_id: str) -> str:
        explanation = self.explain_run(run_id)
        receipt = explanation.receipt
        parts = [
            f"Run {receipt.id} was {receipt.status}: {receipt.summary}.",
            f"Evidence: {', '.join(receipt.evidence) or 'none recorded'}.",
            f"Memory: {', '.join(receipt.memory) or 'none used'}.",
            f"Permissions: {', '.join(receipt.permissions) or 'none used'}.",
            f"Approvals: {', '.join(receipt.approvals) or 'none used'}.",
            f"Routine health: {', '.join(receipt.routine_health) or 'not applicable'}.",
            f"Connector health: {', '.join(receipt.connector_health) or 'not applicable'}.",
            f"Suppressed findings: {', '.join(receipt.suppressed_findings) or 'none'}.",
            f"Tools: {', '.join(receipt.tools) or 'none recorded'}.",
            f"Actions: {', '.join(receipt.actions) or 'none recorded'}.",
            f"Cost: {receipt.cost_units} billable units.",
        ]
        if explanation.failure is not None:
            parts.append(f"Failure: {explanation.failure.summary}.")
        return " ".join(parts)

    def visual_state(self) -> VisualState:
        state = self._repository.load()
        control = self.control()
        if self._placement.current_outcome().status != "available":
            return "degraded"
        if control.failures:
            return "warning"
        if self.status("web").pending_approvals:
            return "approval"
        return {
            "idle": "idle",
            "listening": "listening",
            "reasoning": "reasoning",
            "executing": "execution",
        }[state.activity]  # type: ignore[return-value]

    @staticmethod
    def administration_allowed(
        remote_address: str,
        *,
        private_network_authenticated: bool,
        via_ssh_tunnel: bool,
    ) -> bool:
        try:
            address = ip_address(remote_address)
        except ValueError:
            return False
        if address.is_loopback or via_ssh_tunnel:
            return True
        private_networks = (
            ip_network("10.0.0.0/8"),
            ip_network("172.16.0.0/12"),
            ip_network("192.168.0.0/16"),
        )
        return private_network_authenticated and any(
            address in network for network in private_networks
        )

    @staticmethod
    def _session(
        state: InterfaceState,
        channel: ChannelName,
        session_id: str = "",
    ) -> ChannelSession:
        return next(
            (
                item
                for item in state.sessions
                if item.channel == channel and item.session_id == session_id
            ),
            ChannelSession(channel, session_id),
        )

    def _save_session(
        self,
        state: InterfaceState,
        channel: ChannelName,
        session_id: str,
        messages: tuple[ChannelMessage, ...],
    ) -> None:
        sessions = tuple(
            item
            for item in state.sessions
            if not (item.channel == channel and item.session_id == session_id)
        )
        self._repository.save(
            replace(
                state,
                sessions=sessions + (ChannelSession(channel, session_id, messages),),
            )
        )


def create_local_interface_application(
    application: MessageApplication,
    data_path: Path | None = None,
    *,
    placement: PlacementPolicy | None = None,
    connector_data_dir: Path | None = None,
) -> ReckoningInterfaceApplication:
    from reckoning.confirmations import LocalDurableConfirmationHandler
    from reckoning.operational_records import LocalOperationalRecordSource

    state_path = data_path or (
        Path.home() / ".local" / "state" / "reckoning" / "interfaces.json"
    )
    return ReckoningInterfaceApplication(
        repository=JsonFileInterfaceRepository(state_path),
        responder=ApplicationChannelResponder(application),
        operational_records=LocalOperationalRecordSource(
            state_path.parent,
            connector_data_dir=connector_data_dir,
        ),
        confirmations=LocalDurableConfirmationHandler(state_path.parent),
        placement=placement
        or PlacementPolicy(
            profile="local",
            categories=(
                SourcePlacement(
                    category="personal-context",
                    sensitivity="private",
                    storage_node="local",
                    processing_node="local",
                ),
            ),
            local_node_available=True,
            server_node_available=False,
        ),
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _processing_categories_for(
    placement_categories: tuple[str, ...],
) -> tuple[str, ...]:
    processing_by_placement = {
        "personal-context": "personal-context",
        "confirmed-state": "confirmed-state",
        "approved-remote-sources": "supplied-context",
    }
    return _unique(
        tuple(
            processing_by_placement[category]
            for category in placement_categories
            if category in processing_by_placement
        )
    )


def _merge_by_id[T](
    first: tuple[T, ...], second: tuple[T, ...]
) -> tuple[T, ...]:
    values = {getattr(item, "id"): item for item in first}
    values.update({getattr(item, "id"): item for item in second})
    return tuple(values.values())


def _flatten(receipts: tuple[RunReceipt, ...], field_name: str) -> tuple[str, ...]:
    values = tuple(
        value
        for receipt in receipts
        for value in getattr(receipt, field_name)
    )
    return _unique(values)


def _state_to_data(state: InterfaceState) -> dict[str, object]:
    return {
        "returning_user": state.returning_user,
        "matters_now": list(state.matters_now),
        "changes": list(state.changes),
        "decisions": list(state.decisions),
        "activity": state.activity,
        "confirmed_records": list(state.confirmed_records),
        "permissions": list(state.permissions),
        "pending_approvals": list(state.pending_approvals),
        "receipts": [
            {**asdict(receipt), "occurred_at": receipt.occurred_at.isoformat()}
            for receipt in state.receipts
        ],
        "failures": [
            {**asdict(failure), "occurred_at": failure.occurred_at.isoformat()}
            for failure in state.failures
        ],
        "sessions": [asdict(session) for session in state.sessions],
    }


def _state_from_data(data: dict[str, Any]) -> InterfaceState:
    return InterfaceState(
        returning_user=bool(data.get("returning_user", False)),
        matters_now=tuple(
            str(item)
            for item in data.get(
                "matters_now", _DEFAULT_MATTERS_NOW
            )
        ),
        changes=tuple(
            str(item) for item in data.get("changes", _DEFAULT_CHANGES)
        ),
        decisions=tuple(
            str(item)
            for item in data.get("decisions", _DEFAULT_DECISIONS)
        ),
        activity=str(data.get("activity", "idle")),  # type: ignore[arg-type]
        confirmed_records=tuple(str(item) for item in data.get("confirmed_records", [])),
        permissions=tuple(str(item) for item in data.get("permissions", [])),
        pending_approvals=tuple(
            str(item) for item in data.get("pending_approvals", [])
        ),
        receipts=tuple(
            RunReceipt(
                id=str(item["id"]),
                occurred_at=datetime.fromisoformat(str(item["occurred_at"])),
                status=str(item["status"]),
                summary=str(item["summary"]),
                evidence=tuple(str(value) for value in item.get("evidence", [])),
                memory=tuple(str(value) for value in item.get("memory", [])),
                permissions=tuple(
                    str(value) for value in item.get("permissions", [])
                ),
                approvals=tuple(str(value) for value in item.get("approvals", [])),
                cost_units=int(item.get("cost_units", 0)),
                routine_health=tuple(
                    str(value) for value in item.get("routine_health", [])
                ),
                connector_health=tuple(
                    str(value) for value in item.get("connector_health", [])
                ),
                suppressed_findings=tuple(
                    str(value) for value in item.get("suppressed_findings", [])
                ),
                tools=tuple(str(value) for value in item.get("tools", [])),
                actions=tuple(str(value) for value in item.get("actions", [])),
            )
            for item in data.get("receipts", [])
        ),
        failures=tuple(
            OperationalFailure(
                id=str(item["id"]),
                run_id=str(item["run_id"]),
                summary=str(item["summary"]),
                occurred_at=datetime.fromisoformat(str(item["occurred_at"])),
            )
            for item in data.get("failures", [])
        ),
        sessions=tuple(
            ChannelSession(
                channel=str(item["channel"]),  # type: ignore[arg-type]
                session_id=str(item.get("session_id", "")),
                messages=tuple(
                    ChannelMessage(
                        role=str(message["role"]),  # type: ignore[arg-type]
                        content=str(message["content"]),
                    )
                    for message in item.get("messages", [])
                ),
            )
            for item in data.get("sessions", [])
        ),
    )
