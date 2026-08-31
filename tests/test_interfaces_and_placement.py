from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlencode

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    ModelRequest,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
    create_local_application,
)
from reckoning.automation import (
    JsonFileAutomationRepository,
    RoutineService,
    RoutineStep,
    StepResult,
)
from reckoning.connectors import ConnectorItem, ConnectorService
from reckoning.interfaces import (
    ApplicationChannelResponder,
    ChannelMessage,
    ChannelRequest,
    ChannelSession,
    InMemoryInterfaceRepository,
    InterfaceState,
    JsonFileInterfaceRepository,
    OperationalFailure,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    RunReceipt,
    SourcePlacement,
    create_local_interface_application,
)
from reckoning.telegram import (
    TelegramGateway,
    TelegramRequest,
    TelegramWebhookAdapter,
)
from reckoning.operations import load_installation_runtime, setup_instance
from reckoning.web import ReckoningWebApplication


NOW = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


class RecordingResponder:
    def __init__(self, response: str = "Use the smaller proof first.") -> None:
        self.response = response
        self.requests: list[ChannelRequest] = []

    def respond(self, request: ChannelRequest) -> str:
        self.requests.append(request)
        return self.response


class ActivityInspectingResponder:
    def __init__(self, repository: JsonFileInterfaceRepository) -> None:
        self._repository = repository
        self.activity_during_response = ""

    def respond(self, request: ChannelRequest) -> str:
        del request
        self.activity_during_response = self._repository.load().activity
        return "I considered the current trade-off."


class FixedClock:
    def now(self) -> datetime:
        return NOW


class HistoryRecordingModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def respond(self, request: ModelRequest) -> str:
        self.requests.append(request)
        return f"Reply {len(self.requests)}"


class LegacyApplicationStub:
    def open_session(self) -> tuple[object, ...]:
        return ()

    def send_message(self, text: str) -> object:
        raise AssertionError("The interface boundary should own channel messages.")


def request(
    application: Callable,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
    environ_overrides: dict[str, object] | None = None,
) -> tuple[str, dict[str, str], bytes]:
    body = urlencode(form or {}).encode("utf-8")
    captured_status = ""
    captured_headers: dict[str, str] = {}

    def start_response(
        status: str,
        headers: list[tuple[str, str]],
        exc_info: object | None = None,
    ) -> None:
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": BytesIO(body),
        "REMOTE_ADDR": "127.0.0.1",
    }
    environ.update(environ_overrides or {})
    response: Iterable[bytes] = application(environ, start_response)
    return captured_status, captured_headers, b"".join(response)


def local_policy(*, local_node_available: bool = True) -> PlacementPolicy:
    return PlacementPolicy(
        profile="local",
        categories=(
            SourcePlacement(
                category="personal-context",
                sensitivity="private",
                storage_node="local",
                processing_node="local",
            ),
        ),
        local_node_available=local_node_available,
        server_node_available=False,
    )


def test_telegram_webhook_adapter_maps_only_bounded_commands(tmp_path: Path) -> None:
    application, responder = build_interface(
        tmp_path,
        state=InterfaceState(
            confirmed_records=("decision d-1",),
            permissions=("calendar.read",),
            pending_approvals=("approval-1",),
            receipts=(
                RunReceipt(
                    id="run-1",
                    occurred_at=NOW,
                    status="succeeded",
                    summary="Routine checked deadlines",
                ),
            ),
        ),
    )
    gateway = TelegramGateway(
        application,
        authentication_token="gateway-secret",
        allowed_chat_ids=("42",),
    )
    webhook = TelegramWebhookAdapter(
        gateway,
        authentication_token="gateway-secret",
        webhook_secret="telegram-webhook-secret",
    )

    result = webhook.handle_update(
        {"message": {"chat": {"id": 42}, "text": "/status"}},
        secret_token="telegram-webhook-secret",
    )

    assert result["method"] == "sendMessage"
    assert result["chat_id"] == "42"
    assert "healthy" in str(result["text"])
    assert "approval-1" in str(result["text"])

    message_result = webhook.handle_update(
        {"message": {"chat": {"id": 42}, "text": "What should I do?"}},
        secret_token="telegram-webhook-secret",
    )
    confirmation_result = webhook.handle_update(
        {"message": {"chat": {"id": 42}, "text": "/confirm approval-1"}},
        secret_token="telegram-webhook-secret",
    )
    routine_result = webhook.deliver_routine(
        chat_id="42",
        text="No deadline changes.",
        receipt_id="run-1",
    )

    assert message_result["text"] == "Use the smaller proof first."
    assert confirmation_result["text"] == "Confirmed approval-1."
    assert routine_result == {
        "method": "sendMessage",
        "chat_id": "42",
        "text": "No deadline changes.",
    }
    assert responder.requests[0].confirmed_records == ("decision d-1",)
    assert responder.requests[0].permissions == ("calendar.read",)
    with pytest.raises(PermissionError, match="webhook authentication"):
        webhook.handle_update(
            {"message": {"chat": {"id": 42}, "text": "/status"}},
            secret_token="wrong",
        )
    with pytest.raises(ValueError, match="Only Telegram message"):
        webhook.handle_update(
            {"callback_query": {"data": "admin"}},
            secret_token="telegram-webhook-secret",
        )


def build_interface(
    tmp_path: Path,
    *,
    state: InterfaceState | None = None,
    policy: PlacementPolicy | None = None,
    response: str = "Use the smaller proof first.",
) -> tuple[ReckoningInterfaceApplication, RecordingResponder]:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    if state is not None:
        repository.save(state)
    responder = RecordingResponder(response)
    return (
        ReckoningInterfaceApplication(
            repository=repository,
            responder=responder,
            placement=policy or local_policy(),
        ),
        responder,
    )


def test_new_user_opens_simon_then_returning_user_opens_progressive_home(
    tmp_path: Path,
) -> None:
    interface, _ = build_interface(tmp_path)
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    status, _, first_page = request(web, "GET", "/")
    assert status == "200 OK"
    assert b'data-area="simon"' in first_page

    interface.send_channel_message("web", "Help me choose.")
    status, _, home = request(web, "GET", "/")

    assert status == "200 OK"
    assert b'data-area="home"' in home
    assert b"What matters now" in home
    assert b"What changed" in home
    assert b"Needs your decision" in home
    assert b"System health" in home
    for area in (b"Home", b"Simon", b"Plan", b"Review", b"Control"):
        assert area in home
    assert b"Memory" not in home
    assert b"Evidence" not in home


def test_home_projects_real_run_changes_and_pending_approval_decisions(
    tmp_path: Path,
) -> None:
    interface, _ = build_interface(
        tmp_path,
        state=InterfaceState(
            returning_user=True,
            matters_now=("Protect the exam baseline.",),
            changes=(),
            decisions=(),
            pending_approvals=("calendar-write-7",),
        ),
    )
    interface.record_operational_run(
        RunReceipt(
            id="run-7",
            occurred_at=NOW,
            status="succeeded",
            summary="Deadline watch found one changed exam date",
        )
    )

    home = interface.home()

    assert home.matters_now == ("Protect the exam baseline.",)
    assert home.changes == (
        "Deadline watch found one changed exam date (succeeded).",
    )
    assert home.decisions == ("Approval required: calendar-write-7.",)
    assert home.health == "healthy"


def test_message_path_publishes_real_reasoning_state_then_returns_to_idle(
    tmp_path: Path,
) -> None:
    repository = JsonFileInterfaceRepository(tmp_path / "interfaces.json")
    responder = ActivityInspectingResponder(repository)
    interface = ReckoningInterfaceApplication(
        repository=repository,
        responder=responder,
        placement=local_policy(),
    )

    interface.send_channel_message("web", "Compare the two choices.")

    assert responder.activity_during_response == "reasoning"
    assert repository.load().activity == "idle"


@pytest.mark.parametrize("area", ("home", "simon", "plan", "review", "control"))
def test_each_task_area_keeps_clear_navigation_and_narrow_layout(
    tmp_path: Path, area: str
) -> None:
    interface, _ = build_interface(
        tmp_path, state=InterfaceState(returning_user=True)
    )
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    _, _, page = request(web, "GET", f"/{'' if area == 'home' else area}")

    assert b'<nav aria-label="Task areas">' in page
    assert b"See your current situation" in page
    assert b"Think with Simon" in page
    assert b"Choose the work" in page
    assert b"Learn from outcomes" in page
    assert b"Inspect operations" in page
    assert b"@media (max-width: 720px)" in page
    assert b"grid-template-columns: 1fr" in page
    assert b"min-height: 44px" in page


@pytest.mark.parametrize(
    ("state", "expected_visual"),
    [
        (InterfaceState(activity="listening"), "listening"),
        (InterfaceState(activity="reasoning"), "reasoning"),
        (InterfaceState(pending_approvals=("approval-1",)), "approval"),
        (InterfaceState(activity="executing"), "execution"),
        (
            InterfaceState(
                failures=(
                    OperationalFailure("failure-1", "run-1", "Calendar failed", NOW),
                )
            ),
            "warning",
        ),
    ],
)
def test_simon_visual_is_bound_to_real_runtime_state_at_both_viewports(
    tmp_path: Path, state: InterfaceState, expected_visual: str
) -> None:
    interface, _ = build_interface(tmp_path, state=state)
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    _, _, page = request(web, "GET", "/simon")

    assert f'data-simon-state="{expected_visual}"'.encode() in page
    assert b'aria-label="Simon, original mechanical core"' in page
    assert b"@media (max-width: 720px)" in page
    assert b'<meta name="viewport"' in page


def test_simon_visual_is_degraded_when_required_context_node_is_offline(
    tmp_path: Path,
) -> None:
    interface, _ = build_interface(
        tmp_path,
        state=InterfaceState(activity="reasoning"),
        policy=local_policy(local_node_available=False),
    )
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    _, _, page = request(web, "GET", "/simon")

    assert b'data-simon-state="degraded"' in page
    assert b'aria-label="Simon, original mechanical core"' in page
    assert b"@media (max-width: 720px)" in page
    assert b'<meta name="viewport"' in page


def test_control_and_simon_explanation_use_same_durable_operational_record(
    tmp_path: Path,
) -> None:
    receipt = RunReceipt(
        id="run-7",
        occurred_at=NOW,
        status="failed",
        summary="Check the calendar for deadline changes",
        evidence=("calendar event evt-1",),
        memory=("goal goal-3",),
        permissions=("calendar.read",),
        approvals=("approval-2",),
        cost_units=24,
        routine_health=("deadline-watch: degraded",),
        connector_health=("calendar: offline",),
        suppressed_findings=("one low-confidence change",),
        tools=("calendar.read",),
        actions=("read calendar",),
    )
    failure = OperationalFailure(
        "failure-7", "run-7", "Calendar connector timed out", NOW
    )
    state = InterfaceState(
        returning_user=True,
        confirmed_records=("goal goal-3",),
        permissions=("calendar.read",),
        receipts=(receipt,),
        failures=(failure,),
    )
    interface, _ = build_interface(tmp_path, state=state)
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    _, _, control = request(web, "GET", "/control")
    explanation = interface.explain_run("run-7")
    reopened, _ = build_interface(tmp_path)

    for visible_truth in (
        b"calendar event evt-1",
        b"goal goal-3",
        b"calendar.read",
        b"approval-2",
        b"24",
        b"deadline-watch: degraded",
        b"calendar: offline",
        b"one low-confidence change",
        b"read calendar",
    ):
        assert visible_truth in control
    assert b'<section id="failure-log"' in control
    assert b"Calendar connector timed out" in control
    assert explanation.receipt == reopened.explain_run("run-7").receipt
    assert explanation.failure == reopened.explain_run("run-7").failure
    assert "run-7" in interface.ask_simon_to_explain("run-7")
    assert "Calendar connector timed out" in interface.ask_simon_to_explain("run-7")
    assert interface.channel_session("web") == ()


def test_runtime_can_publish_activity_and_operational_truth(tmp_path: Path) -> None:
    interface, _ = build_interface(tmp_path)
    interface.set_activity("executing")
    assert interface.visual_state() == "execution"

    receipt = RunReceipt(
        id="run-live",
        occurred_at=NOW,
        status="failed",
        summary="Calendar synchronization failed",
        connector_health=("calendar: failed",),
    )
    failure = OperationalFailure(
        "failure-live", "run-live", "Calendar timed out", NOW
    )
    interface.record_operational_run(receipt, failure=failure)

    assert interface.visual_state() == "warning"
    assert interface.control().receipts == (receipt,)
    assert interface.ask_simon_to_explain("run-live").endswith(
        "Failure: Calendar timed out."
    )


def test_control_projects_a_durable_routine_and_simon_explains_the_same_run(
    tmp_path: Path,
) -> None:
    routine_service = RoutineService(
        JsonFileAutomationRepository(tmp_path / "automation.json")
    )
    proposal = routine_service.propose(
        proposal_id="weekly-review-v1",
        routine_id="weekly-review",
        source_request="Review the confirmed weekly evidence.",
        created_at=NOW,
        trigger="Sunday 18:00",
        source_scope=("confirmed-check-ins",),
        context_scope=("goal:finish-semester",),
        tools=("read-check-ins",),
        permissions=("read:confirmed-check-ins",),
        delivery="private-web",
        model_policy="no model",
        cost_ceiling=0,
        retry_limit=0,
        delegation_policy="direct execution only",
        failure_behavior="record failure",
    )
    routine_service.confirm(proposal.id)
    routine_service.start_run(
        run_id="weekly-review-run-1",
        proposal_id=proposal.id,
        scheduled_for=NOW,
        idempotency_key="weekly-review:2026-08-31",
        steps=(RoutineStep("read", "deterministic", "read-check-ins"),),
    )

    class OfflineReader:
        def execute(self, step: RoutineStep, idempotency_key: str) -> StepResult:
            del step, idempotency_key
            raise OSError("Confirmed check-in source is offline")

    routine_service.resume_run(
        "weekly-review-run-1",
        deterministic_executor=OfflineReader(),
        model_executor=None,
        completed_at=NOW,
    )
    core = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=HistoryRecordingModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
        )
    )
    interface = create_local_interface_application(
        core, tmp_path / "interfaces.json"
    )
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    control = interface.control()
    explanation = interface.ask_simon_to_explain("weekly-review-run-1")
    _, _, control_page = request(web, "GET", "/control")

    assert control.routine_health == ("weekly-review: failed",)
    assert control.evidence == ("Confirmed check-in source is offline",)
    assert control.permissions == ("read:confirmed-check-ins",)
    assert control.tools == ("read-check-ins",)
    assert control.actions == ("read-check-ins",)
    assert "Confirmed check-in source is offline" in explanation
    assert b'<section id="failure-log"' in control_page
    assert b"Confirmed check-in source is offline" in control_page
    assert interface.channel_session("web") == ()


def test_control_projects_durable_read_connector_health_and_failure(
    tmp_path: Path,
) -> None:
    class OfflineCalendarConnector:
        connector_id = "calendar"
        read_scopes = ("events:read",)
        write_scopes: tuple[str, ...] = ()

        def verify_identity(self) -> str:
            return "mary-calendar"

        def synchronize(
            self, read_scope: tuple[str, ...]
        ) -> tuple[ConnectorItem, ...]:
            assert read_scope == ("events:read",)
            raise TimeoutError("Calendar connector timed out")

    connector_path = tmp_path / "connectors.json"
    connectors = ConnectorService(path=connector_path)
    connectors.connect(OfflineCalendarConnector(), read_scope=("events:read",))
    with pytest.raises(TimeoutError, match="timed out"):
        connectors.synchronize("calendar", synchronized_at=NOW)

    assert ConnectorService(path=connector_path).health("calendar").status == "failed"

    core = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=HistoryRecordingModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
        )
    )
    interface = create_local_interface_application(
        core, tmp_path / "interfaces.json"
    )
    control = interface.control()
    connector_run = next(
        receipt for receipt in control.receipts if receipt.id.startswith("connector:")
    )

    assert control.connector_health == (
        "calendar: failed - Calendar connector timed out",
    )
    assert "Calendar connector timed out" in interface.ask_simon_to_explain(
        connector_run.id
    )


def test_telegram_uses_bounded_authenticated_application_capabilities(
    tmp_path: Path,
) -> None:
    state = InterfaceState(
        confirmed_records=("decision d-1",),
        permissions=("calendar.read",),
        pending_approvals=("approval-1",),
        receipts=(
            RunReceipt(
                id="run-1",
                occurred_at=NOW,
                status="succeeded",
                summary="Routine checked deadlines",
            ),
        ),
    )
    interface, responder = build_interface(tmp_path, state=state)
    gateway = TelegramGateway(
        interface,
        authentication_token="telegram-secret",
        allowed_chat_ids=("mary",),
    )

    reply = gateway.handle(
        "telegram-secret",
        TelegramRequest(kind="message", chat_id="mary", text="What should I do?"),
    )
    status = gateway.handle(
        "telegram-secret", TelegramRequest(kind="status", chat_id="mary")
    )
    confirmation = gateway.handle(
        "telegram-secret",
        TelegramRequest(
            kind="confirm", chat_id="mary", confirmation_id="approval-1"
        ),
    )
    delivery = gateway.deliver_routine(
        "telegram-secret", "mary", "No deadline changes.", receipt_id="run-1"
    )

    assert reply.text == "Use the smaller proof first."
    assert "healthy" in status.text
    assert "approval-1" in status.text
    assert confirmation.text == "Confirmed approval-1."
    assert delivery.text == "No deadline changes."
    assert responder.requests[0].confirmed_records == ("decision d-1",)
    assert responder.requests[0].permissions == ("calendar.read",)
    assert interface.channel_session("web") == ()
    assert [
        message.content
        for message in interface.channel_session("telegram", session_id="mary")
    ] == [
        "What should I do?",
        "Use the smaller proof first.",
        "No deadline changes.",
    ]

    with pytest.raises(PermissionError, match="authentication"):
        gateway.handle(
            "wrong", TelegramRequest(kind="status", chat_id="mary")
        )
    with pytest.raises(ValueError, match="bounded"):
        gateway.handle(
            "telegram-secret", TelegramRequest(kind="admin", chat_id="mary")
        )

    with pytest.raises(PermissionError, match="chat is not authorized"):
        gateway.handle(
            "telegram-secret", TelegramRequest(kind="status", chat_id="mallory")
        )


def test_telegram_chat_sessions_are_durable_and_isolated_from_each_other(
    tmp_path: Path,
) -> None:
    interface, responder = build_interface(tmp_path)
    gateway = TelegramGateway(
        interface,
        authentication_token="telegram-secret",
        allowed_chat_ids=("mary-phone", "mary-tablet"),
    )

    gateway.handle(
        "telegram-secret",
        TelegramRequest(kind="message", chat_id="mary-phone", text="Phone one."),
    )
    gateway.handle(
        "telegram-secret",
        TelegramRequest(kind="message", chat_id="mary-tablet", text="Tablet one."),
    )
    gateway.handle(
        "telegram-secret",
        TelegramRequest(kind="message", chat_id="mary-phone", text="Phone two."),
    )

    assert responder.requests[0].recent_history == ()
    assert responder.requests[1].recent_history == ()
    assert [item.content for item in responder.requests[2].recent_history] == [
        "Phone one.",
        "Use the smaller proof first.",
    ]
    reopened, _ = build_interface(tmp_path)
    assert [
        item.content
        for item in reopened.channel_session("telegram", session_id="mary-phone")
    ] == [
        "Phone one.",
        "Use the smaller proof first.",
        "Phone two.",
        "Use the smaller proof first.",
    ]
    assert [
        item.content
        for item in reopened.channel_session("telegram", session_id="mary-tablet")
    ] == ["Tablet one.", "Use the smaller proof first."]
    assert reopened.channel_session("web") == ()


def test_real_application_boundary_keeps_web_and_telegram_history_separate() -> None:
    model = HistoryRecordingModel()
    legacy_storage = InMemoryConversationStorage()
    core = ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=model,
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=legacy_storage,
        )
    )
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(),
        responder=ApplicationChannelResponder(core),
        placement=local_policy(),
    )

    interface.send_channel_message("web", "Web question one.")
    interface.send_channel_message("telegram", "Telegram question.")
    interface.send_channel_message("web", "Web question two.")

    assert model.requests[0].history == ()
    assert model.requests[1].history == ()
    assert [(item.role, item.content) for item in model.requests[2].history] == [
        ("user", "Web question one."),
        ("assistant", "Reply 1"),
    ]
    assert legacy_storage.list_messages() == ()


def test_hybrid_placement_prevents_private_copy_and_declares_limited_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="explicit remote approval"):
        SourcePlacement(
            category="personal-context",
            sensitivity="private",
            storage_node="server",
            processing_node="server",
        )

    policy = PlacementPolicy(
        profile="hybrid",
        categories=(
            SourcePlacement(
                category="personal-context",
                sensitivity="private",
                storage_node="local",
                processing_node="local",
            ),
            SourcePlacement(
                category="public-research",
                sensitivity="public",
                storage_node="server",
                processing_node="server",
            ),
        ),
        local_node_available=False,
        server_node_available=True,
    )
    interface, responder = build_interface(tmp_path, policy=policy)

    limited = interface.send_channel_message(
        "web",
        "Use public sources only.",
        required_categories=("personal-context", "public-research"),
        safe_when_incomplete=True,
    )
    with pytest.raises(RuntimeError, match="blocked.*personal-context"):
        interface.send_channel_message(
            "telegram",
            "Change my confirmed plan.",
            required_categories=("personal-context",),
            safe_when_incomplete=False,
        )

    assert limited.placement.status == "limited"
    assert limited.text.startswith("Limited mode:")
    assert responder.requests[0].available_categories == ("public-research",)
    assert interface.status("web").placement == interface.status("telegram").placement
    assert interface.visual_state() == "degraded"


def test_real_channel_defaults_enforce_placement_and_withhold_unavailable_context(
    tmp_path: Path,
) -> None:
    interface, responder = build_interface(
        tmp_path,
        state=InterfaceState(confirmed_records=("private decision d-1",)),
        policy=local_policy(local_node_available=False),
    )

    reply = interface.send_channel_message("telegram", "What can I do remotely?")

    assert reply.placement.status == "limited"
    assert responder.requests[0].available_categories == ()
    assert responder.requests[0].confirmed_records == ()
    assert reply.text.startswith("Limited mode:")


def test_hybrid_remote_run_withholds_all_local_context_categories(
    tmp_path: Path,
) -> None:
    policy = PlacementPolicy(
        profile="hybrid",
        categories=(
            SourcePlacement(
                category="personal-context",
                sensitivity="private",
                storage_node="local",
                processing_node="local",
            ),
            SourcePlacement(
                category="confirmed-state",
                sensitivity="private",
                storage_node="local",
                processing_node="local",
            ),
            SourcePlacement(
                category="approved-remote-sources",
                sensitivity="public",
                storage_node="server",
                processing_node="server",
            ),
        ),
        local_node_available=False,
        server_node_available=True,
    )
    state = InterfaceState(
        confirmed_records=("private decision d-1",),
        permissions=("calendar.read",),
        sessions=(
            ChannelSession(
                "telegram",
                "mary",
                (
                    ChannelMessage("user", "Private earlier question."),
                    ChannelMessage("assistant", "Private earlier answer."),
                ),
            ),
        ),
    )
    interface, responder = build_interface(tmp_path, state=state, policy=policy)

    reply = interface.send_channel_message(
        "telegram", "Use only what the server can access.", session_id="mary"
    )

    assert reply.placement.status == "limited"
    assert responder.requests[0].available_categories == (
        "approved-remote-sources",
    )
    assert responder.requests[0].confirmed_records == ()
    assert responder.requests[0].permissions == ()
    assert responder.requests[0].recent_history == ()


def test_installed_hybrid_outage_gives_web_and_telegram_identical_truth(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "installed-hybrid"
    setup_instance(data_dir, "hybrid")
    runtime = load_installation_runtime(
        data_dir,
        local_node_available=True,
        server_node_available=False,
    )
    core = create_local_application(
        data_dir / "continuity.json",
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    interface = create_local_interface_application(
        core,
        data_dir / "interfaces.json",
        placement=runtime.interface_placement,
    )
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )
    telegram = TelegramGateway(
        interface,
        authentication_token="telegram-secret",
        allowed_chat_ids=("mary",),
    )

    _, _, home = request(web, "GET", "/home")
    telegram_status = telegram.handle(
        "telegram-secret", TelegramRequest(kind="status", chat_id="mary")
    )
    expected_notice = runtime.interface_placement.current_outcome().notice

    assert expected_notice.encode() in home
    assert expected_notice in telegram_status.text
    assert interface.status("web").placement == interface.status(
        "telegram"
    ).placement


@pytest.mark.parametrize(
    ("remote_address", "private_authenticated", "ssh_tunnel", "allowed"),
    [
        ("127.0.0.1", False, False, True),
        ("192.168.1.20", True, False, True),
        ("203.0.113.7", False, True, True),
        ("192.168.1.20", False, False, False),
        ("203.0.113.7", True, False, False),
    ],
)
def test_administration_is_restricted_to_private_access_paths(
    remote_address: str,
    private_authenticated: bool,
    ssh_tunnel: bool,
    allowed: bool,
) -> None:
    assert (
        ReckoningInterfaceApplication.administration_allowed(
            remote_address,
            private_network_authenticated=private_authenticated,
            via_ssh_tunnel=ssh_tunnel,
        )
        is allowed
    )


def test_control_route_enforces_administration_network_boundary(tmp_path: Path) -> None:
    interface, _ = build_interface(
        tmp_path, state=InterfaceState(returning_user=True)
    )
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    denied, _, denied_page = request(
        web,
        "GET",
        "/control",
        environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
    )
    allowed, _, _ = request(
        web,
        "GET",
        "/control",
        environ_overrides={
            "REMOTE_ADDR": "192.168.1.20",
            "reckoning.private_network_authenticated": True,
        },
    )

    assert denied == "403 Forbidden"
    assert denied_page == b"Administration requires a private access path."
    assert allowed == "200 OK"


def test_every_control_subpath_is_inside_the_private_administration_boundary(
    tmp_path: Path,
) -> None:
    interface, _ = build_interface(
        tmp_path, state=InterfaceState(returning_user=True)
    )
    web = ReckoningWebApplication(
        LegacyApplicationStub(), interface_application=interface
    )

    status, _, page = request(
        web,
        "GET",
        "/control/runs/run-7",
        environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
    )

    assert status == "403 Forbidden"
    assert page == b"Administration requires a private access path."
