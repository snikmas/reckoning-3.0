from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import Request

import pytest

from reckoning.application import (
    ApplicationDependencies,
    InMemoryConversationStorage,
    NoConnectors,
    PlacementState,
    ReckoningApplication,
)
from reckoning.continuity import DeterministicFakeReckoningProvider
from reckoning.external_content import ExternalContentResult
from reckoning.interfaces import (
    ApplicationChannelResponder,
    ChannelMessage,
    ChannelSession,
    InMemoryInterfaceRepository,
    InterfaceState,
    PlacementPolicy,
    ReckoningInterfaceApplication,
    SourcePlacement,
)
from reckoning.processing import (
    JsonFileProcessingGrantRepository,
    ProcessingDestination,
    ProcessingGrantConflict,
    ProcessingScope,
)
from reckoning.personal_context import (
    InMemoryPersonalContextRepository,
    PersonalContextService,
)
from reckoning.provider_adapters import AdapterConfig, RuntimeAdapterModelProvider


NOW = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> bytes:
        del timeout
        self.requests.append(request)
        return json.dumps(
            {
                "model": "deepseek-chat",
                "choices": [
                    {"message": {"role": "assistant", "content": "bounded reply"}}
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            }
        ).encode()


def cloud_application(
    tmp_path: Path,
    scope: ProcessingScope,
    transport: RecordingTransport,
    *,
    personal_context: PersonalContextService | None = None,
    reckoning_provider: object | None = None,
) -> ReckoningApplication:
    config = AdapterConfig(api_key="synthetic-key", model="deepseek-chat")
    return ReckoningApplication(
        ApplicationDependencies(
            clock=FixedClock(),
            model=RuntimeAdapterModelProvider(
                "deepseek", config, transport=transport
            ),
            placement=PlacementState("server", "local", True),
            connectors=NoConnectors(),
            storage=InMemoryConversationStorage(),
            retrieved_context=("SYNTHETIC SUPPLIED CONTEXT",),
            processing_scope=scope,
            personal_context=personal_context,
            **(
                {"reckoning_provider": reckoning_provider}
                if reckoning_provider is not None
                else {}
            ),
        )
    )


def test_cloud_destination_blocks_missing_grant_before_transport_and_provider_change(
    tmp_path: Path,
) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    destination = ProcessingDestination(
        "deepseek", "https://api.deepseek.com", "cloud"
    )
    scope = ProcessingScope(repository, destination)
    transport = RecordingTransport()
    application = cloud_application(tmp_path, scope, transport)

    with pytest.raises(RuntimeError, match="destination lacks a grant"):
        application.respond_with_channel_context(
            "SYNTHETIC CURRENT REQUEST",
            (("user", "SYNTHETIC PRIOR TURN"),),
            ("SYNTHETIC CONFIRMED STATE",),
            ("synthetic.permission",),
        )

    assert transport.requests == []
    assert application.processing_scope_status().explicit is False

    status = application.change_processing_scope(
        ("current-request",), expected_revision=0
    )
    assert status.grant_version == 1
    assert status.destination.id == destination.id

    changed_provider = ProcessingScope(
        repository,
        ProcessingDestination("anthropic", "https://api.anthropic.com/v1", "cloud"),
    )
    assert changed_provider.review().allowed_categories == ()
    assert changed_provider.review().explicit is False


def test_local_destination_defaults_to_all_context_with_fake_transport(
    tmp_path: Path,
) -> None:
    scope = ProcessingScope(
        JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json"),
        ProcessingDestination("deepseek", "http://127.0.0.1:9000", "local"),
    )
    transport = RecordingTransport()
    application = cloud_application(tmp_path, scope, transport)

    application.respond_with_channel_context(
        "SYNTHETIC LOCAL REQUEST",
        (("user", "SYNTHETIC LOCAL HISTORY"),),
        ("SYNTHETIC LOCAL STATE",),
        ("synthetic.local.permission",),
    )

    payload = transport.requests[0].data.decode()
    for content in (
        "SYNTHETIC LOCAL REQUEST",
        "SYNTHETIC LOCAL HISTORY",
        "SYNTHETIC LOCAL STATE",
        "synthetic.local.permission",
        "SYNTHETIC SUPPLIED CONTEXT",
    ):
        assert content in payload
    assert scope.review().explicit is False


def test_each_context_route_is_filtered_before_real_adapter_transport(
    tmp_path: Path,
) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    transport = RecordingTransport()
    application = cloud_application(tmp_path, scope, transport)
    application.change_processing_scope(("current-request",), expected_revision=0)

    limited = application.respond_with_channel_context(
        "SYNTHETIC CURRENT REQUEST",
        (("user", "SYNTHETIC PRIOR TURN"),),
        ("SYNTHETIC CONFIRMED STATE",),
        ("synthetic.permission",),
    )

    first_payload = transport.requests[0].data.decode()
    assert "SYNTHETIC CURRENT REQUEST" in first_payload
    assert "SYNTHETIC PRIOR TURN" not in first_payload
    assert "SYNTHETIC CONFIRMED STATE" not in first_payload
    assert "synthetic.permission" not in first_payload
    assert "SYNTHETIC SUPPLIED CONTEXT" not in first_payload
    assert limited.content.startswith("Limited context: unavailable processing categories:")

    application.change_processing_scope(
        (
            "current-request",
            "recent-channel-history",
            "confirmed-state",
            "permissions",
            "derived-summaries",
        ),
        expected_revision=1,
    )
    application.respond_with_channel_context(
        "SYNTHETIC SECOND REQUEST",
        (("user", "SYNTHETIC PRIOR TURN"),),
        ("SYNTHETIC CONFIRMED STATE",),
        ("synthetic.permission",),
    )
    second_payload = transport.requests[1].data.decode()
    for content in (
        "SYNTHETIC SECOND REQUEST",
        "SYNTHETIC PRIOR TURN",
        "SYNTHETIC CONFIRMED STATE",
        "synthetic.permission",
        "SYNTHETIC SUPPLIED CONTEXT",
    ):
        assert content in second_payload

    assert application.processing_scope_status().grant_version == 2
    restarted = ProcessingScope(
        JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json"),
        scope.destination,
    )
    assert restarted.review().allowed_categories == scope.review().allowed_categories


def test_web_and_telegram_calls_share_the_destination_filter(tmp_path: Path) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    scope.change(("current-request",), changed_at=NOW, expected_version=0)
    transport = RecordingTransport()
    core = cloud_application(tmp_path, scope, transport)
    state = InterfaceState(
        confirmed_records=("SYNTHETIC PRIVATE RECORD",),
        sessions=(
            ChannelSession(
                "telegram",
                "chat-1",
                (ChannelMessage("user", "SYNTHETIC TELEGRAM HISTORY"),),
            ),
        ),
    )
    policy = PlacementPolicy(
        "local",
        (
            SourcePlacement("personal-context", "private", "local", "local"),
            SourcePlacement("confirmed-state", "private", "local", "local"),
        ),
        True,
        False,
    )
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(state),
        responder=ApplicationChannelResponder(core),
        placement=policy,
    )

    interface.send_channel_message("web", "SYNTHETIC WEB MESSAGE")
    interface.send_channel_message(
        "telegram", "SYNTHETIC TELEGRAM MESSAGE", session_id="chat-1"
    )

    assert len(transport.requests) == 2
    payloads = tuple(request.data.decode() for request in transport.requests)
    assert "SYNTHETIC WEB MESSAGE" in payloads[0]
    assert "SYNTHETIC TELEGRAM MESSAGE" in payloads[1]
    assert all("SYNTHETIC PRIVATE RECORD" not in payload for payload in payloads)
    assert "SYNTHETIC TELEGRAM HISTORY" not in payloads[1]


def test_non_degradable_channel_work_requires_matching_processing_grants(
    tmp_path: Path,
) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    scope.change(("current-request",), changed_at=NOW, expected_version=0)
    transport = RecordingTransport()
    personal_context = PersonalContextService(InMemoryPersonalContextRepository())
    core = cloud_application(
        tmp_path,
        scope,
        transport,
        personal_context=personal_context,
    )
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(),
        responder=ApplicationChannelResponder(core),
        placement=PlacementPolicy(
            "local",
            (SourcePlacement("personal-context", "private", "local", "local"),),
            True,
            False,
        ),
    )

    with pytest.raises(RuntimeError, match="grant for: personal-context"):
        interface.send_channel_message(
            "web",
            "Use my profile for this required operation.",
            required_categories=("personal-context",),
            safe_when_incomplete=False,
        )

    assert transport.requests == []


def test_unavailable_placement_context_stays_out_even_when_provider_grant_allows_it(
    tmp_path: Path,
) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    scope.change(
        ("current-request", "personal-context"),
        changed_at=NOW,
        expected_version=0,
    )
    personal_context = PersonalContextService(InMemoryPersonalContextRepository())
    personal_context.remember(
        record_id="private-placement-1",
        original_text="SYNTHETIC PLACEMENT SECRET FIR",
        language="en",
        canonical_meaning="SYNTHETIC PLACEMENT SECRET FIR",
        source="synthetic test",
        created_at=NOW,
        sensitivity="private",
        processing_location="server",
    )
    transport = RecordingTransport()
    core = cloud_application(
        tmp_path,
        scope,
        transport,
        personal_context=personal_context,
    )
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(),
        responder=ApplicationChannelResponder(core),
        placement=PlacementPolicy(
            "hybrid",
            (SourcePlacement("personal-context", "private", "local", "local"),),
            False,
            True,
        ),
    )

    reply = interface.send_channel_message("web", "Use only available context.")

    assert reply.placement.status == "limited"
    assert "SYNTHETIC PLACEMENT SECRET FIR" not in transport.requests[0].data.decode()


def test_storage_execution_and_provider_destination_are_distinct(tmp_path: Path) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    scope.change(("current-request",), changed_at=NOW, expected_version=0)
    transport = RecordingTransport()
    application = cloud_application(tmp_path, scope, transport)

    application.send_message("Inspect the three locations.")

    status = application.processing_scope_status()
    assert status.storage_location == "local"
    assert status.executing_node == "server"
    assert status.destination.endpoint == "https://api.deepseek.com"


def test_processing_grants_reject_empty_categories_and_stale_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "processing-grants.json"
    destination = ProcessingDestination(
        "deepseek", "https://api.deepseek.com", "cloud"
    )
    first = ProcessingScope(JsonFileProcessingGrantRepository(path), destination)
    second = ProcessingScope(JsonFileProcessingGrantRepository(path), destination)

    with pytest.raises(ValueError, match="cannot be empty"):
        first.change(
            ("current-request", ""), changed_at=NOW, expected_version=0
        )

    first.change(("current-request",), changed_at=NOW, expected_version=0)
    with pytest.raises(ProcessingGrantConflict, match="changed from revision 0 to 1"):
        second.change(("current-request",), changed_at=NOW, expected_version=0)


def test_ambiguous_persisted_grants_fail_closed_before_transport(
    tmp_path: Path,
) -> None:
    path = tmp_path / "processing-grants.json"
    destination = ProcessingDestination(
        "deepseek", "https://api.deepseek.com", "cloud"
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "grants": [
                    {
                        "destination_id": destination.id,
                        "version": 1,
                        "allowed_categories": ["current-request"],
                        "changed_at": NOW.isoformat(),
                    },
                    {
                        "destination_id": destination.id,
                        "version": 2,
                        "allowed_categories": [],
                        "changed_at": NOW.isoformat(),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    scope = ProcessingScope(JsonFileProcessingGrantRepository(path), destination)
    transport = RecordingTransport()
    application = cloud_application(tmp_path, scope, transport)

    with pytest.raises(RuntimeError, match="duplicate destinations"):
        application.send_message("Do not send this ambiguous request.")

    assert transport.requests == []


def test_personal_supplied_and_derived_context_need_their_own_grants(
    tmp_path: Path,
) -> None:
    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    scope.change(("current-request",), changed_at=NOW, expected_version=0)
    personal_context = PersonalContextService(InMemoryPersonalContextRepository())
    personal_context.remember(
        record_id="private-1",
        original_text="SYNTHETIC PERSONAL SECRET CEDAR",
        language="en",
        canonical_meaning="SYNTHETIC PERSONAL SECRET CEDAR",
        source="synthetic test",
        created_at=NOW,
        sensitivity="private",
        processing_location="server",
    )
    transport = RecordingTransport()
    application = cloud_application(
        tmp_path,
        scope,
        transport,
        personal_context=personal_context,
    )
    supplied = ExternalContentResult(
        "external-1",
        "synthetic source",
        "SYNTHETIC EXTERNAL CONTENT",
        (
            "protected_product_contract",
            "product_identity",
            "external_untrusted_data",
        ),
        (),
        (),
        (),
    )

    application.send_message("Ask about cedar.")
    application.respond_with_external_content(
        "Use the external data.", (), (supplied,)
    )
    denied_payloads = tuple(request.data.decode() for request in transport.requests)
    assert all("SYNTHETIC PERSONAL SECRET CEDAR" not in item for item in denied_payloads)
    assert "SYNTHETIC SUPPLIED CONTEXT" not in denied_payloads[0]
    assert "SYNTHETIC EXTERNAL CONTENT" not in denied_payloads[1]

    scope.change(
        (
            "current-request",
            "personal-context",
            "derived-summaries",
            "supplied-context",
        ),
        changed_at=NOW,
        expected_version=1,
    )
    application.send_message("Ask about cedar.")
    application.respond_with_external_content(
        "Use the external data.", (), (supplied,)
    )
    allowed_payloads = tuple(
        request.data.decode() for request in transport.requests[-2:]
    )
    assert "SYNTHETIC PERSONAL SECRET CEDAR" in allowed_payloads[0]
    assert "SYNTHETIC SUPPLIED CONTEXT" in allowed_payloads[0]
    assert "SYNTHETIC EXTERNAL CONTENT" in allowed_payloads[1]


def test_raw_reckoning_input_is_blocked_before_provider_execution(
    tmp_path: Path,
) -> None:
    class CountingReckoningProvider:
        def __init__(self) -> None:
            self.calls = 0
            self._provider = DeterministicFakeReckoningProvider()

        def reckon(self, unstructured_input: str):
            self.calls += 1
            return self._provider.reckon(unstructured_input)

    repository = JsonFileProcessingGrantRepository(tmp_path / "processing-grants.json")
    scope = ProcessingScope(
        repository,
        ProcessingDestination("deepseek", "https://api.deepseek.com", "cloud"),
    )
    provider = CountingReckoningProvider()
    application = cloud_application(
        tmp_path,
        scope,
        RecordingTransport(),
        reckoning_provider=provider,
    )

    with pytest.raises(RuntimeError, match="raw-reckoning-input"):
        application.start_reckoning("SYNTHETIC STRUCTURED INPUT")
    assert provider.calls == 0

    scope.change(("raw-reckoning-input",), changed_at=NOW, expected_version=0)
    decision = application.start_reckoning("SYNTHETIC STRUCTURED INPUT")
    assert decision.source_input == "SYNTHETIC STRUCTURED INPUT"
    assert provider.calls == 1
