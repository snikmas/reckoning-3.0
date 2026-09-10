"""Independent synthetic probes for GitHub issue #104.

All state lives in temporary directories. Provider and Telegram calls use fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlencode

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
    SystemClock,
    create_local_application,
)
from reckoning.continuity import (
    Evidence,
    Inference,
    PersonalRecordProposal,
    ReckoningDraft,
    SourcedFact,
    UuidIdentifierFactory,
)
from reckoning.interfaces import create_local_interface_application
from reckoning.persistence import JsonFileReckoningRepository
from reckoning.personal_context import (
    JsonFilePersonalContextRepository,
    PersonalContextService,
    RetrievalQuery,
)
from reckoning.planning import (
    CapacitySnapshot,
    GoalPlanningService,
    InMemoryPlanningRepository,
)
from reckoning.provider_adapters import AdapterConfig
from reckoning.telegram import TelegramBotApiError, TelegramPollingApplication
from reckoning.trials import (
    JsonFileTrialRepository,
    REQUIRED_REAL_USE_METRICS,
    TrialEvidence,
    TrialRecorder,
)
from reckoning.web import ReckoningWebApplication


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


class NoConversationModel:
    def respond(self, request: object) -> str:
        del request
        raise AssertionError("The correction probe does not use conversation.")


class ProposedRecordProvider:
    def reckon(self, text: str) -> ReckoningDraft:
        return ReckoningDraft(
            conflict="Choose one priority.",
            questions=(),
            matters_now=("Choose.",),
            maintained=(),
            parked=(),
            uncertainties=(),
            known=(SourcedFact("Two commitments compete.", ("e1",)),),
            inferences=(Inference("Capacity is limited.", ("e1",), "Unconfirmed."),),
            evidence=(Evidence("e1", "synthetic message", text),),
            next_step="Confirm one priority.",
            proposed_records=(
                PersonalRecordProposal(
                    "current_state",
                    "No priority confirmed.",
                    ("e1",),
                ),
            ),
        )


def correction_application(path: Path) -> ReckoningApplication:
    return ReckoningApplication(
        ApplicationDependencies(
            clock=SystemClock(),
            model=NoConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=ProposedRecordProvider(),
            reckoning_repository=JsonFileReckoningRepository(path),
            identifiers=UuidIdentifierFactory(),
        )
    )


def remember_local_context(path: Path) -> None:
    service = PersonalContextService(JsonFilePersonalContextRepository(path))
    service.remember(
        record_id="private-1",
        original_text="astronomy plans",
        language="en",
        canonical_meaning="Private astronomy plan stored for local processing",
        source="synthetic",
        created_at=NOW,
        sensitivity="private",
        processing_location="local",
    )


def wsgi_request(
    application: ReckoningWebApplication,
    method: str,
    path: str,
    fields: dict[str, str] | None = None,
    *,
    foreign: bool = False,
) -> tuple[dict[str, object], str]:
    body = urlencode(fields or {}).encode()
    captured: dict[str, object] = {}
    origin = "https://foreign.invalid" if foreign else "http://127.0.0.1:8000"
    host = "foreign.invalid:8000" if foreign else "127.0.0.1:8000"
    site = "cross-site" if foreign else "same-origin"
    response = application(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "wsgi.input": BytesIO(body),
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_HOST": host,
            "HTTP_ORIGIN": origin,
            "HTTP_SEC_FETCH_SITE": site,
        },
        lambda status, headers: captured.update(
            status=status,
            headers=dict(headers),
        ),
    )
    return captured, b"".join(response).decode()


def probe_shared_state(root: Path) -> dict[str, object]:
    continuity_path = root / "shared" / "continuity.json"
    first = create_local_application(continuity_path)
    second = create_local_application(continuity_path)
    first_decision = first.start_reckoning("Exam versus project.")
    second_decision = second.start_reckoning("Health versus deadline.")
    stored = json.loads(continuity_path.read_text(encoding="utf-8"))

    correction_path = root / "correction" / "continuity.json"
    seed = correction_application(correction_path)
    decision = seed.start_reckoning("Exam versus project.")
    record_id = decision.current_records[0].record_id
    writer_a = correction_application(correction_path)
    writer_b = correction_application(correction_path)
    writer_a.correct_personal_record(
        decision.id,
        record_id,
        "Prioritize the exam.",
    )
    writer_b.correct_personal_record(
        decision.id,
        record_id,
        "Prioritize the project.",
    )
    corrected = json.loads(correction_path.read_text(encoding="utf-8"))
    meanings = [
        item["meaning"]
        for item in corrected["reckonings"][0]["record_versions"]
        if item["record_id"] == record_id
    ]

    context_path = root / "deletion" / "personal-context.json"
    original = PersonalContextService(JsonFilePersonalContextRepository(context_path))
    original.remember(
        record_id="deleted-record",
        original_text="synthetic astronomy preference",
        language="en",
        canonical_meaning="synthetic astronomy preference",
        source="synthetic",
        created_at=NOW,
        sensitivity="private",
        processing_location="local",
    )
    stale = PersonalContextService(JsonFilePersonalContextRepository(context_path))
    original.delete("deleted-record", NOW)
    stale_result = stale.retrieve(
        RetrievalQuery(
            text="astronomy",
            now=NOW,
            allowed_sources=("synthetic",),
            allowed_sensitivities=("private",),
            processing_location="local",
        )
    )
    stale.remember(
        record_id="unrelated-record",
        original_text="synthetic gardening preference",
        language="en",
        canonical_meaning="synthetic gardening preference",
        source="synthetic",
        created_at=NOW,
        sensitivity="private",
        processing_location="local",
    )
    reloaded = JsonFilePersonalContextRepository(context_path)

    return {
        "created_decisions": 2,
        "persisted_decisions": len(stored["reckonings"]),
        "first_decision_lost": first_decision.id
        not in {item["id"] for item in stored["reckonings"]},
        "second_decision_present": second_decision.id
        in {item["id"] for item in stored["reckonings"]},
        "concurrent_correction_meanings": meanings,
        "first_correction_lost": "Prioritize the exam." not in meanings,
        "conflict_returned": False,
        "deleted_record_seen_by_stale_instance": bool(stale_result),
        "deleted_record_resurrected": any(
            item.record_id == "deleted-record" for item in reloaded.all_versions()
        ),
        "deletion_marker_lost": not reloaded.suppression_markers(),
    }


def probe_model_destination(root: Path) -> dict[str, object]:
    context_path = root / "placement" / "personal-context.json"
    remember_local_context(context_path)
    calls: list[dict[str, object]] = []

    def conversation_transport(request: object, timeout: float) -> bytes:
        del timeout
        calls.append(
            {
                "url": request.full_url,
                "payload": json.loads(request.data),
            }
        )
        return json.dumps(
            {
                "model": "deepseek-chat",
                "choices": [{"message": {"content": "Synthetic answer"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()

    cloud = create_local_application(
        root / "placement" / "continuity.json",
        personal_context_path=context_path,
        provider_name="deepseek",
        provider_config=AdapterConfig(api_key="synthetic", model="deepseek-chat"),
        provider_transport=conversation_transport,
    )
    channel = create_local_interface_application(
        cloud,
        root / "placement" / "interfaces.json",
    )
    channel.send_channel_message("web", "astronomy plans")
    channel.send_channel_message("web", "continue that plan")

    structured_calls: list[dict[str, object]] = []
    structured_content = json.dumps(
        {
            "conflict": "One priority is needed.",
            "questions": [],
            "matters_now": ["Choose."],
            "maintained": [],
            "parked": [],
            "uncertainties": [],
            "known": [],
            "inferences": [],
            "next_step": "Confirm.",
            "proposed_records": [],
        }
    )

    def structured_transport(request: object, timeout: float) -> bytes:
        del timeout
        structured_calls.append(
            {
                "url": request.full_url,
                "payload": json.loads(request.data),
            }
        )
        return json.dumps(
            {
                "model": "deepseek-chat",
                "choices": [{"message": {"content": structured_content}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()

    structured = create_local_application(
        root / "placement" / "structured.json",
        provider_name="deepseek",
        provider_config=AdapterConfig(api_key="synthetic", model="deepseek-chat"),
        provider_transport=structured_transport,
    )
    structured.start_reckoning("Private conflict for local processing")

    first_payload = json.dumps(calls[0]["payload"])
    second_payload = json.dumps(calls[1]["payload"])
    structured_payload = json.dumps(structured_calls[0]["payload"])
    return {
        "conversation_destination": calls[0]["url"],
        "local_private_context_sent": "Private astronomy plan" in first_payload,
        "recent_channel_history_sent": "astronomy plans" in second_payload
        and "Synthetic answer" in second_payload,
        "structured_destination": structured_calls[0]["url"],
        "structured_input_sent": "Private conflict for local processing"
        in structured_payload,
        "network_calls": 0,
    }


def probe_channel_journey(root: Path) -> dict[str, object]:
    continuity_path = root / "channel" / "continuity.json"
    application = create_local_application(
        continuity_path,
        personal_context_path=root / "channel" / "personal-context.json",
    )
    interfaces = create_local_interface_application(
        application,
        root / "channel" / "interfaces.json",
    )
    web = ReckoningWebApplication(application, interface_application=interfaces)
    post, _ = wsgi_request(
        web,
        "POST",
        "/messages",
        {"message": "Help me choose between study and a project."},
    )
    _, home = wsgi_request(web, "GET", "/")
    _, simon = wsgi_request(web, "GET", "/simon")
    _, review = wsgi_request(web, "GET", "/review")
    return {
        "post_status": post["status"],
        "redirect_location": post["headers"].get("Location"),
        "redirect_page_is_home": "<h1>Home</h1>" in home,
        "reply_visible_on_redirect_page": "Reckoning received your message" in home,
        "reply_visible_on_simon": "Reckoning received your message" in simon,
        "structured_continuity_file_exists": continuity_path.exists(),
        "review_is_placeholder": "Compare intentions with what happened" in review,
        "channel_messages": len(interfaces.channel_session("web")),
    }


def proposed_profile(path: Path) -> None:
    PersonalContextService(JsonFilePersonalContextRepository(path)).propose(
        record_id="p1",
        original_text="old",
        language="en",
        canonical_meaning="old",
        source="synthetic",
        created_at=NOW,
    )


def probe_web_mutations(root: Path) -> dict[str, object]:
    results: dict[str, object] = {}
    message_root = root / "web" / "message"
    application = create_local_application(message_root / "continuity.json")
    interfaces = create_local_interface_application(
        application,
        message_root / "interfaces.json",
    )
    web = ReckoningWebApplication(application, interface_application=interfaces)
    response, _ = wsgi_request(
        web,
        "POST",
        "/messages",
        {"message": "synthetic"},
        foreign=True,
    )
    results["message"] = response["status"]
    results["message_state_changed"] = len(interfaces.channel_session("web")) == 2

    for action in ("correct", "confirm", "reject"):
        action_root = root / "web" / action
        context_path = action_root / "personal-context.json"
        proposed_profile(context_path)
        application = create_local_application(
            action_root / "continuity.json",
            personal_context_path=context_path,
        )
        interfaces = create_local_interface_application(
            application,
            action_root / "interfaces.json",
        )
        web = ReckoningWebApplication(application, interface_application=interfaces)
        fields = {"meaning": "new"} if action == "correct" else {}
        response, _ = wsgi_request(
            web,
            "POST",
            f"/profile/p1/{action}",
            fields,
            foreign=True,
        )
        results[action] = response["status"]
        reloaded = JsonFilePersonalContextRepository(context_path)
        results[f"{action}_state_changed"] = (
            bool(reloaded.suppression_markers())
            if action == "reject"
            else len(reloaded.versions("p1")) == 2
        )

    page_status, page = wsgi_request(web, "GET", "/simon")
    results["page_status"] = page_status["status"]
    results["message_form_has_csrf_field"] = "csrf" in page.casefold()
    return results


class CountingUpdateAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def handle_update(self, update: dict[str, object]) -> object:
        del update
        self.calls += 1
        return SimpleNamespace(chat_id="42", text="computed-once")


class AmbiguousTelegramClient:
    def __init__(self) -> None:
        self.remote: list[str] = []
        self.fail = True

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
    ) -> tuple[dict[str, object], ...]:
        del offset, timeout
        return ({"update_id": 7},)

    def send_message(self, chat_id: str, text: str) -> None:
        del chat_id
        self.remote.append(text)
        if self.fail:
            self.fail = False
            raise TelegramBotApiError("synthetic timeout after remote acceptance")


class ProviderFailureAdapter:
    def handle_update(self, update: dict[str, object]) -> object:
        del update
        raise RuntimeError("synthetic provider outage")


def probe_telegram() -> dict[str, object]:
    adapter = CountingUpdateAdapter()
    client = AmbiguousTelegramClient()
    polling = TelegramPollingApplication(adapter, client, poll_timeout=0)
    offset = None
    try:
        offset = polling.run_once(offset)
    except TelegramBotApiError:
        pass
    offset = polling.run_once(offset)

    provider_escaped = False
    try:
        TelegramPollingApplication(
            ProviderFailureAdapter(),
            client,
            poll_timeout=0,
        ).run_forever(bot_username="synthetic", output=lambda _: None)
    except RuntimeError:
        provider_escaped = True

    return {
        "update_processing_calls": adapter.calls,
        "remote_deliveries": client.remote,
        "final_offset": offset,
        "ambiguous_send_duplicated": len(client.remote) == 2,
        "provider_error_stops_loop": provider_escaped,
    }


def probe_trial(root: Path) -> dict[str, object]:
    path = root / "trial" / "trials.json"
    recorder = TrialRecorder(JsonFileTrialRepository(path))
    trial = recorder.start("t1", "continuity", NOW)
    trial = recorder.record(
        "t1",
        TrialEvidence(
            "check",
            "automated_check",
            None,
            "All required checks failed",
            NOW,
        ),
    )
    for index, metric in enumerate(REQUIRED_REAL_USE_METRICS):
        trial = recorder.record(
            "t1",
            TrialEvidence(
                f"e{index}",
                "real_use",
                metric,
                "Rejected, no useful outcome, not worth keeping",
                NOW,
            ),
        )
    restarted = JsonFileTrialRepository(path).get("t1")
    return {
        "missing_evidence": restarted.missing_evidence,
        "accepted_after_restart": restarted.accepted,
        "stored_decision_field": False,
        "stored_evidence_count": len(restarted.evidence),
    }


def probe_planning() -> dict[str, object]:
    service = GoalPlanningService(InMemoryPlanningRepository())
    service.create_direction("d", "Synthetic", NOW)
    goal = service.create_goal(
        goal_id="g",
        direction_id="d",
        target="Synthetic goal",
        success_evidence=("done",),
        constraints=(),
        dependencies=(),
        deadline=None,
        importance=5,
        difficulty=5,
        weekly_effort_hours=8,
        created_at=NOW,
    )
    result = service.calculate_feasible_set(
        (goal,),
        CapacitySnapshot(
            available_hours=9,
            obligation_hours=0,
            current_activity_hours=0,
            prior_outcome_factor=1,
        ),
    )
    fit = (result.included + result.displaced)[0]
    return {
        "raw_weekly_effort": 8,
        "difficulty": 5,
        "adjusted_effort": fit.adjusted_effort_hours,
        "capacity": result.capacity.forecast_hours,
        "included": bool(result.included),
        "reason": fit.reason,
    }


with TemporaryDirectory(prefix="reckoning-issue-104-") as folder:
    root = Path(folder)
    results = {
        "shared_state": probe_shared_state(root),
        "model_destination": probe_model_destination(root),
        "channel_journey": probe_channel_journey(root),
        "web_mutations": probe_web_mutations(root),
        "telegram": probe_telegram(),
        "trial_acceptance": probe_trial(root),
        "planning_heuristic": probe_planning(),
    }
    print(json.dumps(results, indent=2))
