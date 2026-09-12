from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Literal, Protocol




ChannelName = Literal["terminal", "web", "telegram"]
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


class InterfaceSessionConflict(RuntimeError):
    def __init__(
        self, channel: str, session_id: str, expected: int, actual: int
    ) -> None:
        super().__init__(
            f"Interface session {channel}:{session_id} changed from revision "
            f"{expected} to {actual}."
        )
        self.channel = channel
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


class InterfaceProjectionConflict(RuntimeError):
    def __init__(self, projection: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Interface projection {projection} changed from revision "
            f"{expected} to {actual}."
        )
        self.projection = projection
        self.expected = expected
        self.actual = actual


def _find_session(
    state: InterfaceState, channel: ChannelName, session_id: str
) -> ChannelSession:
    return next(
        (
            item
            for item in state.sessions
            if item.channel == channel and item.session_id == session_id
        ),
        ChannelSession(channel, session_id),
    )


class InterfaceRepository(Protocol):
    def load(self) -> InterfaceState: ...

    def save(self, state: InterfaceState) -> None: ...

    def session_revision(self, channel: ChannelName, session_id: str) -> int: ...

    def projection_revision(self, projection: str) -> int: ...

    def append_completed_turn(
        self,
        channel: ChannelName,
        session_id: str,
        user_text: str,
        assistant_text: str,
        *,
        expected_revision: int,
    ) -> ChannelSession: ...

    def append_messages(
        self,
        channel: ChannelName,
        session_id: str,
        messages: tuple[ChannelMessage, ...],
        *,
        expected_revision: int,
        returning_user: bool = False,
    ) -> ChannelSession: ...

    def set_activity(
        self,
        activity: ActivityState,
        *,
        channel: ChannelName | str = "",
        session_id: str = "",
    ) -> None: ...

    def set_returning_user(self) -> None: ...

    def add_pending_approval(self, approval_id: str) -> None: ...

    def resolve_approval(
        self, approval_id: str, *, expected_revision: int | None = None
    ) -> bool: ...

    def replace_projection(
        self, projection: str, values: tuple[str, ...], *, expected_revision: int
    ) -> None: ...

    def record_operational_run(
        self,
        receipt: RunReceipt,
        *,
        failure: OperationalFailure | None = None,
        expected_revision: int | None = None,
    ) -> None: ...


class InMemoryInterfaceRepository:
    def __init__(self, state: InterfaceState | None = None) -> None:
        self._state = state or InterfaceState()
        self._session_revisions: dict[tuple[str, str], int] = {
            (session.channel, session.session_id): 1
            for session in self._state.sessions
        }
        self._projection_revisions: dict[str, int] = {}
        self._approval_revisions: dict[str, int] = {
            approval_id: 1 for approval_id in self._state.pending_approvals
        }

    def load(self) -> InterfaceState:
        return self._state

    def save(self, state: InterfaceState) -> None:
        self._state = state
        self._session_revisions = {
            (session.channel, session.session_id): 1 for session in state.sessions
        }
        self._projection_revisions = {}
        self._approval_revisions = {
            approval_id: 1 for approval_id in state.pending_approvals
        }

    def session_revision(self, channel: ChannelName, session_id: str) -> int:
        return self._session_revisions.get((channel, session_id), 0)

    def projection_revision(self, projection: str) -> int:
        return self._projection_revisions.get(projection, 0)

    def append_completed_turn(
        self,
        channel: ChannelName,
        session_id: str,
        user_text: str,
        assistant_text: str,
        *,
        expected_revision: int,
    ) -> ChannelSession:
        return self.append_messages(
            channel,
            session_id,
            (
                ChannelMessage("user", user_text),
                ChannelMessage("assistant", assistant_text),
            ),
            expected_revision=expected_revision,
            returning_user=True,
        )

    def append_messages(
        self,
        channel: ChannelName,
        session_id: str,
        messages: tuple[ChannelMessage, ...],
        *,
        expected_revision: int,
        returning_user: bool = False,
    ) -> ChannelSession:
        actual = self.session_revision(channel, session_id)
        if actual != expected_revision:
            raise InterfaceSessionConflict(
                channel, session_id, expected_revision, actual
            )
        existing = _find_session(self._state, channel, session_id)
        combined = existing.messages + messages
        sessions = tuple(
            session
            for session in self._state.sessions
            if not (
                session.channel == channel and session.session_id == session_id
            )
        ) + (ChannelSession(channel, session_id, combined),)
        self._state = replace(
            self._state,
            sessions=sessions,
            returning_user=self._state.returning_user or returning_user,
        )
        self._session_revisions[(channel, session_id)] = actual + 1
        return ChannelSession(channel, session_id, combined)

    def set_activity(
        self,
        activity: ActivityState,
        *,
        channel: ChannelName | str = "",
        session_id: str = "",
    ) -> None:
        del channel, session_id
        self._state = replace(self._state, activity=activity)

    def set_returning_user(self) -> None:
        self._state = replace(self._state, returning_user=True)

    def add_pending_approval(self, approval_id: str) -> None:
        if approval_id in self._approval_revisions:
            return
        self._state = replace(
            self._state, pending_approvals=self._state.pending_approvals + (approval_id,)
        )
        self._approval_revisions[approval_id] = 1

    def resolve_approval(
        self, approval_id: str, *, expected_revision: int | None = None
    ) -> bool:
        if approval_id not in self._approval_revisions:
            return False
        if (
            expected_revision is not None
            and self._approval_revisions[approval_id] != expected_revision
        ):
            raise InterfaceProjectionConflict(
                approval_id, expected_revision, self._approval_revisions[approval_id]
            )
        self._state = replace(
            self._state,
            pending_approvals=tuple(
                item for item in self._state.pending_approvals if item != approval_id
            ),
        )
        del self._approval_revisions[approval_id]
        return True

    def replace_projection(
        self, projection: str, values: tuple[str, ...], *, expected_revision: int
    ) -> None:
        actual = self.projection_revision(projection)
        if actual != expected_revision:
            raise InterfaceProjectionConflict(projection, expected_revision, actual)
        self._state = replace(
            self._state, **{projection: tuple(values)}  # type: ignore[arg-type]
        )
        self._projection_revisions[projection] = actual + 1

    def record_operational_run(
        self,
        receipt: RunReceipt,
        *,
        failure: OperationalFailure | None = None,
        expected_revision: int | None = None,
    ) -> None:
        del expected_revision
        if failure is not None and failure.run_id != receipt.id:
            raise ValueError("An operational failure must identify the same run.")
        receipts = tuple(
            item for item in self._state.receipts if item.id != receipt.id
        ) + (receipt,)
        failures = tuple(
            item for item in self._state.failures if item.run_id != receipt.id
        )
        if failure is not None:
            failures += (failure,)
        self._state = replace(
            self._state,
            activity="idle",
            receipts=receipts,
            failures=failures,
        )


class JsonFileInterfaceRepository:
    """Compatibility entry point backed by per-root SQLite transactions."""

    def __init__(self, path: Path) -> None:
        from reckoning.interface_store import SQLiteInterfaceRepository

        self._delegate = SQLiteInterfaceRepository(path)

    @property
    def database_path(self) -> Path:
        return self._delegate.database_path

    def load(self) -> InterfaceState:
        return self._delegate.load()

    def save(self, state: InterfaceState) -> None:
        self._delegate.save(state)

    def session_revision(self, channel: ChannelName, session_id: str) -> int:
        return self._delegate.session_revision(channel, session_id)

    def projection_revision(self, projection: str) -> int:
        return self._delegate.projection_revision(projection)

    def append_completed_turn(
        self,
        channel: ChannelName,
        session_id: str,
        user_text: str,
        assistant_text: str,
        *,
        expected_revision: int,
    ) -> ChannelSession:
        return self._delegate.append_completed_turn(
            channel,
            session_id,
            user_text,
            assistant_text,
            expected_revision=expected_revision,
        )

    def append_messages(
        self,
        channel: ChannelName,
        session_id: str,
        messages: tuple[ChannelMessage, ...],
        *,
        expected_revision: int,
        returning_user: bool = False,
    ) -> ChannelSession:
        return self._delegate.append_messages(
            channel,
            session_id,
            messages,
            expected_revision=expected_revision,
            returning_user=returning_user,
        )

    def set_activity(
        self,
        activity: ActivityState,
        *,
        channel: ChannelName | str = "",
        session_id: str = "",
    ) -> None:
        self._delegate.set_activity(
            activity, channel=channel, session_id=session_id
        )

    def set_returning_user(self) -> None:
        self._delegate.set_returning_user()

    def add_pending_approval(self, approval_id: str) -> None:
        self._delegate.add_pending_approval(approval_id)

    def resolve_approval(
        self, approval_id: str, *, expected_revision: int | None = None
    ) -> bool:
        return self._delegate.resolve_approval(
            approval_id, expected_revision=expected_revision
        )

    def replace_projection(
        self, projection: str, values: tuple[str, ...], *, expected_revision: int
    ) -> None:
        self._delegate.replace_projection(
            projection, values, expected_revision=expected_revision
        )

    def record_operational_run(
        self,
        receipt: RunReceipt,
        *,
        failure: OperationalFailure | None = None,
        expected_revision: int | None = None,
    ) -> None:
        self._delegate.record_operational_run(
            receipt, failure=failure, expected_revision=expected_revision
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
        self._repository.set_activity(activity)

    def record_operational_run(
        self,
        receipt: RunReceipt,
        *,
        failure: OperationalFailure | None = None,
    ) -> None:
        """Publish one durable operational record to Control and Simon."""
        self._repository.record_operational_run(receipt, failure=failure)

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
        expected_revision = self._repository.session_revision(channel, session_id)
        self._repository.set_activity(
            "listening", channel=channel, session_id=session_id
        )
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
            self._repository.set_activity(
                "reasoning", channel=channel, session_id=session_id
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

            self._repository.append_completed_turn(
                channel,
                session_id,
                message,
                response,
                expected_revision=expected_revision,
            )
            self._repository.set_activity(
                "idle", channel=channel, session_id=session_id
            )
            return ChannelReply(response, placement)
        except Exception:
            self._repository.set_activity(
                "idle", channel=channel, session_id=session_id
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
        return self._repository.resolve_approval(confirmation_id)

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
        delivered = ChannelMessage("assistant", content)
        self._repository.append_messages(
            channel,
            session_id,
            (delivered,),
            expected_revision=self._repository.session_revision(channel, session_id),
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
