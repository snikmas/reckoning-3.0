"""Issue #114 stage-gate scenario: one synthetic repaired installation.

Assembles one synthetic installation and drives it through the first
trust-repair batch's contracts, end to end, with fake transports only:

1. Independent web/Gateway-role applications share one placement root.
2. Model-run receipts persist per run from both instances.
3. Channel sessions from two channels survive independent writers.
4. Denied processing scope blocks ungranted categories; stale grant writes
   conflict instead of overwriting.
5. Personal-context correction, confirmation, and deletion stay
   authoritative, with a suppression marker for the deleted record.
6. Continuity decisions keep expected-version conflicts visible; a stale
   mutation is rejected, check-ins from both instances survive.
7. Telegram delivery recovers after a provider outage without repeating
   completed work.
8. Trial acceptance stays pending until evidence is complete and an explicit
   decision is recorded.
9. After a restart, every visible result and every pending item is intact.
10. An encrypted clean restore preserves identifiers, versions, permissions,
    suppression markers, and usability through the public boundaries.

Run with:  PYTHONPATH=src python docs/reviews/2026-09-12-issue-114-stage-gate-scenario.py
Exit code 0 means every phase passed. No network, secrets, or real user data.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reckoning.application import (
    create_local_application,
)
from reckoning.continuity import (
    Evidence,
    Inference,
    PersonalRecordProposal,
    ReckoningDraft,
    ReckoningRevisionConflict,
    SourcedFact,
)
from reckoning.interfaces import (
    JsonFileInterfaceRepository,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    SourcePlacement,
)
from reckoning.operations import (
    create_transfer,
    load_installation_runtime,
    restore_transfer,
    setup_instance,
)
from reckoning.personal_context import (
    ContextRevisionConflict,
    JsonFilePersonalContextRepository,
    PersonalContextService,
)
from reckoning.persistence import JsonFileReckoningRepository
from reckoning.processing import (
    JsonFileProcessingGrantRepository,
    ProcessingDestination,
    ProcessingGrantConflict,
    ProcessingScope,
)
from reckoning.telegram import TelegramPollingApplication
from reckoning.telegram_delivery import SQLiteTelegramDeliveryRepository
from reckoning.trials import (
    JsonFileTrialRepository,
    TrialEvidence,
    TrialRecorder,
)

NOW = datetime(2026, 9, 12, 8, 0, tzinfo=UTC)
PASSPHRASE = "issue-114-synthetic-passphrase"
REVISION = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()

results: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str) -> None:
    results.append((name, f"{'PASS' if condition else 'FAIL'}: {detail}"))
    if not condition:
        raise AssertionError(f"{name}: {detail}")


def pass_line(name: str, detail: str) -> None:
    results.append((name, f"PASS: {detail}"))


class FixedClock:
    def __init__(self, minute: int = 0) -> None:
        self._instant = NOW + timedelta(minutes=minute)

    def now(self) -> datetime:
        return self._instant


class SequenceIdentifiers:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.next_value = 0

    def new(self) -> str:
        self.next_value += 1
        return f"{self.prefix}-{self.next_value}"


class DecisionProvider:
    """Deterministic reckoning provider; one decision per source input."""

    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        evidence = Evidence(
            f"evidence:{unstructured_input}", "current user message", unstructured_input
        )
        return ReckoningDraft(
            conflict=f"Conflict for {unstructured_input}",
            questions=(),
            matters_now=("Protect the fixed commitment.",),
            maintained=("Keep one small project block.",),
            parked=(),
            uncertainties=("Outcome not known yet.",),
            known=(SourcedFact(unstructured_input, (evidence.id,)),),
            inferences=(
                Inference("Capacity is shared.", (evidence.id,), "Still uncertain."),
            ),
            evidence=(evidence,),
            next_step="Choose the next block.",
            proposed_records=(
                PersonalRecordProposal(
                    "decision", f"Decision for {unstructured_input}", (evidence.id,)
                ),
            ),
        )


class UnusedConversationModel:
    def respond(self, request: object) -> str:
        raise AssertionError("continuity must use the reckoning provider")


def build_continuity_app(state_path: Path, prefix: str, minute: int = 0):
    from reckoning.application import (
        ApplicationDependencies,
        InMemoryConversationStorage,
        NoConnectors,
        PlacementState,
        ReckoningApplication,
    )

    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(minute),
            model=UnusedConversationModel(),
            placement=PlacementState("local", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            reckoning_provider=DecisionProvider(),
            reckoning_repository=JsonFileReckoningRepository(state_path),
            identifiers=SequenceIdentifiers(prefix),
        )
    )


class RecordingResponder:
    def respond(self, request: object) -> str:
        return "Use the smaller proof first."


def local_policy() -> PlacementPolicy:
    return PlacementPolicy(
        profile="local",
        categories=(
            SourcePlacement("personal-context", "private", "local", "local"),
        ),
        local_node_available=True,
        server_node_available=False,
    )


class ScriptedTelegramAdapter:
    """Replies to each update; update 10 fails once with a provider outage."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.remaining_failures = {10: 1}

    def handle_update(self, update: dict[str, object]) -> object:
        from reckoning.providers import ProviderFailure
        from reckoning.telegram import TelegramReply

        update_id = int(str(update["update_id"]))
        self.calls.append(update_id)
        if self.remaining_failures.get(update_id, 0):
            self.remaining_failures[update_id] -= 1
            failure = ProviderFailure(
                "temporary outage",
                provider="fake",
                model="fake-model",
                model_calls=1,
                latency_ms=5,
                retries=0,
            )
            raise RuntimeError("The fake run failed. temporary outage") from failure
        chat = update["message"]["chat"]  # type: ignore[index]
        return TelegramReply("message", str(chat["id"]), f"reply for {update_id}")  # type: ignore[index]


class FakeTelegramClient:
    def __init__(self, batches: list[tuple[dict[str, object], ...]]) -> None:
        self._batches = list(batches)
        self.sent: list[tuple[str, str]] = []
        self.polls: list[tuple[int | None, int]] = []

    def get_updates(
        self, *, offset: int | None, timeout: int
    ) -> list[dict[str, object]]:
        self.polls.append((offset, timeout))
        return list(self._batches.pop(0)) if self._batches else []

    def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def telegram_update(update_id: int) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": 42}, "text": "What next?"},
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="reckoning-114-"))
    data_dir = root / "installation"

    # Phase 0: essential setup path produces a clean local installation.
    setup_instance(data_dir, "local")
    runtime = load_installation_runtime(data_dir)
    confirmed = runtime.state_path("confirmed-state", "continuity.json").parent
    pass_line("setup", f"synthetic local installation at {data_dir}")

    # Phase 1+2: independent web/Gateway-role instances; one receipt per run.
    continuity_path = confirmed / "continuity.json"
    web_role = create_local_application(continuity_path, provider_name="fake")
    gateway_role = create_local_application(continuity_path, provider_name="fake")
    web_role.send_message("Record the web-side bounded run.")
    gateway_role.send_message("Record the gateway-side bounded run.")
    receipts = create_local_application(
        continuity_path, provider_name="fake"
    ).inspect_model_runs()
    check(
        "model receipts",
        len(receipts) == 2 and len({run.id for run in receipts}) == 2,
        f"{len(receipts)} receipts from two independent instances",
    )

    # Phase 3: channel sessions from two channels survive independent writers.
    interfaces_path = confirmed / "interfaces.json"
    web_interface = ReckoningInterfaceApplication(
        repository=JsonFileInterfaceRepository(interfaces_path),
        responder=RecordingResponder(),  # type: ignore[arg-type]
        placement=local_policy(),
    )
    gateway_interface = ReckoningInterfaceApplication(
        repository=JsonFileInterfaceRepository(interfaces_path),
        responder=RecordingResponder(),  # type: ignore[arg-type]
        placement=local_policy(),
    )
    web_interface.send_channel_message("web", "Choose today's focus.")
    gateway_interface.send_channel_message("telegram", "What changed overnight?")
    restored_state = JsonFileInterfaceRepository(interfaces_path).load()
    check(
        "channel sessions",
        {session.channel for session in restored_state.sessions} == {"web", "telegram"},
        "web and Telegram sessions both survived independent writers",
    )

    # Phase 4: denied processing scope; stale grant writes conflict.
    destination = ProcessingDestination("fake-cloud", "llm.example.test", "cloud")
    grants_path = confirmed / "processing-grants.json"
    scope_web = ProcessingScope(JsonFileProcessingGrantRepository(grants_path), destination)
    scope_gateway = ProcessingScope(
        JsonFileProcessingGrantRepository(grants_path), destination
    )
    denied = scope_web.evaluate(
        ("personal-context",), required_categories=("personal-context",)
    )
    check(
        "processing denied by default",
        denied.blocked_categories == ("personal-context",)
        and not denied.allowed_categories,
        "cloud destination without a grant blocks the required category",
    )
    scope_web.change((), changed_at=NOW, expected_version=0)
    try:
        scope_gateway.change(
            ("personal-context",), changed_at=NOW, expected_version=0
        )
        raise AssertionError("stale grant write was not rejected")
    except ProcessingGrantConflict:
        pass_line("processing grant conflict", "stale grant write rejected")

    # Phase 5: personal-context correction and deletion stay authoritative.
    context_service = PersonalContextService(
        JsonFilePersonalContextRepository(confirmed / "personal-context.json")
    )
    context_service.remember(
        record_id="fact-work",
        original_text="I work on one project.",
        language="en",
        canonical_meaning="Mary works on one project.",
        source="guided setup",
        created_at=NOW,
    )
    corrected = context_service.correct(
        "fact-work",
        original_text="I work on one project and one exam.",
        language="en",
        canonical_meaning="Mary works on one project and one exam.",
        corrected_at=NOW,
        expected_revision=1,
    )
    context_service.remember(
        record_id="fact-temporary",
        original_text="Temporary note.",
        language="en",
        canonical_meaning="Temporary note.",
        source="guided setup",
        created_at=NOW,
    )
    marker = context_service.delete("fact-temporary", NOW)
    try:
        context_service.correct(
            "fact-work",
            original_text="Stale correction.",
            language="en",
            canonical_meaning="Stale correction.",
            corrected_at=NOW,
            expected_revision=1,
        )
        raise AssertionError("stale context correction was not rejected")
    except ContextRevisionConflict:
        pass
    check(
        "personal context",
        corrected.version == 2 and marker.deleted_record_id == "fact-temporary",
        "correction at version 2; deletion left a suppression marker; "
        "stale correction rejected",
    )

    # Phase 6: continuity conflicts stay visible across two instances.
    first = build_continuity_app(continuity_path, "web")
    second = build_continuity_app(continuity_path, "gateway", minute=1)
    web_decision = first.start_reckoning("exam")
    gateway_decision = second.start_reckoning("project")
    corrected_decision = second.correct_personal_record(
        web_decision.id,
        web_decision.current_records[0].record_id,
        "Protect the exam and keep one project block.",
        expected_revision=web_decision.version,
    )
    try:
        first.confirm_reckoning(
            web_decision.id, expected_revision=web_decision.version
        )
        raise AssertionError("stale confirmation was not rejected")
    except ReckoningRevisionConflict:
        pass
    confirmed_decision = first.confirm_reckoning(
        web_decision.id, expected_revision=corrected_decision.version
    )
    first.record_check_in(web_decision.id, "The exam block happened.")
    second.record_check_in(web_decision.id, "The project block happened.")
    check(
        "continuity conflict",
        confirmed_decision.version == 3,
        "stale confirmation rejected; both instances' check-ins recorded",
    )

    # Phase 7: Telegram recovery after a provider outage.
    telegram_path = confirmed / "telegram-delivery.json"
    adapter = ScriptedTelegramAdapter()
    first_client = FakeTelegramClient([(telegram_update(10), telegram_update(11))])
    polling = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        first_client,
        repository=SQLiteTelegramDeliveryRepository(telegram_path),
        poll_timeout=1,
        worker_id="gateway-one",
        clock=FixedClock().now,
    )
    polling.run_once()
    states = {
        record.update_id: record
        for record in SQLiteTelegramDeliveryRepository(telegram_path).list_updates()
    }
    check(
        "telegram first pass",
        states[10].processing_state == "provider-failed"
        and states[11].delivery_state == "delivered",
        "outage marked provider-failed; the later update still delivered",
    )
    recovered_client = FakeTelegramClient([()])
    recovered = TelegramPollingApplication(
        adapter,  # type: ignore[arg-type]
        recovered_client,
        repository=SQLiteTelegramDeliveryRepository(telegram_path),
        poll_timeout=1,
        worker_id="gateway-two",
        clock=FixedClock().now,
    )
    recovered.run_once()
    check(
        "telegram recovery",
        recovered_client.sent == [("42", "reply for 10")]
        and adapter.calls == [10, 11, 10],
        "restart reprocessed only the failed update and delivered its reply",
    )

    # Phase 8: trial reporting stays pending until evidence and an explicit
    # decision exist.
    recorder = TrialRecorder(JsonFileTrialRepository(confirmed / "trials.json"))
    trial = recorder.start(
        "trial-stage-1", "trust-repair-batch", NOW, assessed_revision=REVISION
    )
    pending = recorder.inspect(trial.id)
    try:
        recorder.decide(
            trial.id,
            "accepted",
            reason="premature",
            decided_at=NOW,
            assessed_revision=REVISION,
            expected_revision=trial.version,
        )
        raise AssertionError("trial acceptance was allowed without evidence")
    except ValueError:
        pass
    check(
        "trial gate",
        pending.decision.status == "pending" and not pending.release_accepted,
        "acceptance refused without complete evidence; report stays pending",
    )
    recorder.record(
        trial.id,
        TrialEvidence(
            "evidence-checks",
            "automated_check",
            None,
            "required checks passed",
            NOW,
            check_outcome="passed",
        ),
    )
    for metric in (
        "useful_outcomes",
        "correction_burden",
        "interruption_burden",
        "spending",
        "reliability",
        "repeated_use",
        "mary_judgment",
    ):
        recorder.record(
            trial.id,
            TrialEvidence(
                f"evidence-{metric}",
                "real_use",
                metric,  # type: ignore[arg-type]
                "synthetic positive observation",
                NOW,
                real_use_outcome="positive",
            ),
        )
    current = recorder.inspect(trial.id)
    decided = recorder.decide(
        trial.id,
        "accepted",
        reason="synthetic evidence complete",
        decided_at=NOW,
        assessed_revision=REVISION,
        expected_revision=current.record_version,
    )
    check(
        "trial acceptance",
        decided.accepted,
        "explicit decision accepted only after complete evidence",
    )

    # Phase 9: restart — every visible result and pending item survives.
    restarted = build_continuity_app(continuity_path, "restart", minute=2)
    resumed = restarted.resume_decision(web_decision.id)
    other = restarted.inspect_reckoning(gateway_decision.id)
    restarted_context = PersonalContextService(
        JsonFilePersonalContextRepository(confirmed / "personal-context.json")
    )
    markers = restarted_context._repository.suppression_markers()  # noqa: SLF001
    telegram_records = SQLiteTelegramDeliveryRepository(telegram_path).list_updates()
    restarted_receipts = create_local_application(
        continuity_path, provider_name="fake"
    ).inspect_model_runs()
    check(
        "restart",
        [item.outcome for item in resumed.check_ins]
        == ["The exam block happened.", "The project block happened."]
        and other.source_input == "project"
        and len(restarted_receipts) == 2
        and any(m.deleted_record_id == "fact-temporary" for m in markers)
        and {r.update_id for r in telegram_records} == {10, 11},
        "decisions, check-ins, receipts, suppression marker, and Telegram "
        "records all visible after restart",
    )

    # Phase 10: encrypted clean restore into an empty root.
    archive = root / "backup.reckoning"
    create_transfer(data_dir, archive, PASSPHRASE, kind="backup")
    restored = root / "restored"
    restore_transfer(archive, restored, PASSPHRASE)
    restored_runtime = load_installation_runtime(restored)
    restored_continuity = restored_runtime.state_path(
        "confirmed-state", "continuity.json"
    )
    restored_app = build_continuity_app(restored_continuity, "restored", minute=3)
    restored_decision = restored_app.resume_decision(web_decision.id)
    restored_app.send_message  # keep linters quiet about the import seam
    live = create_local_application(restored_continuity, provider_name="fake")
    live.send_message("Post-restore run still works.")
    restored_receipts = live.inspect_model_runs()
    restored_context = PersonalContextService(
        JsonFilePersonalContextRepository(
            restored_continuity.parent / "personal-context.json"
        )
    )
    restored_fact = restored_context.inspect("fact-work")
    restored_markers = restored_context._repository.suppression_markers()  # noqa: SLF001
    restored_trial = TrialRecorder(
        JsonFileTrialRepository(restored_continuity.parent / "trials.json")
    ).inspect("trial-stage-1")
    restored_telegram = SQLiteTelegramDeliveryRepository(
        restored_continuity.parent / "telegram-delivery.json"
    ).list_updates()
    mode_problems = [
        path
        for path in restored.rglob("*")
        if path.is_file()
        and stat.S_IMODE(path.stat().st_mode) != 0o600
    ]
    check(
        "restore identifiers and versions",
        restored_decision.decision.id == web_decision.id
        and restored_decision.decision.version == confirmed_decision.version
        and restored_fact.current.canonical_meaning == corrected.canonical_meaning
        and restored_trial.decision.status == "accepted"
        and {r.update_id for r in restored_telegram} == {10, 11},
        "identifiers, versions, trial decision, and Telegram records intact",
    )
    check(
        "restore suppression markers",
        any(m.deleted_record_id == "fact-temporary" for m in restored_markers),
        "deletion suppression marker survived the encrypted restore",
    )
    check(
        "restore permissions",
        not mode_problems,
        f"all restored files are 0600 (violations: {mode_problems})",
    )
    check(
        "restore usability",
        len(restored_receipts) == 3,
        "the restored installation accepts new work through its public boundary",
    )

    width = max(len(name) for name, _ in results)
    for name, line in results:
        print(f"{name.ljust(width)}  {line}")
    print(f"\nrevision under test: {REVISION}")
    print(f"synthetic root: {root}")
    print(f"{len(results)} phases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
