"""Read-only review probes. All application state is synthetic and temporary."""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlencode
import json

from reckoning.application import create_local_application, JsonFileModelRunRepository
from reckoning.interfaces import create_local_interface_application
from reckoning.personal_context import PersonalContextService, JsonFilePersonalContextRepository, RetrievalQuery
from reckoning.provider_adapters import AdapterConfig
from reckoning.telegram import TelegramPollingApplication, TelegramBotApiError
from reckoning.web import ReckoningWebApplication
from reckoning.trials import TrialRecorder, InMemoryTrialRepository, TrialEvidence, REQUIRED_REAL_USE_METRICS

NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)
results = {}

def remember(service, record_id, text):
    return service.remember(record_id=record_id, original_text=text, language="en",
        canonical_meaning=text, source="synthetic user", created_at=NOW,
        sensitivity="private", processing_location="local")

with TemporaryDirectory(prefix="reckoning-probes-") as folder:
    root = Path(folder)
    a = create_local_application(root / "runs" / "continuity.json")
    b = create_local_application(root / "runs" / "continuity.json")
    a.send_message("web message")
    b.send_message("telegram message")
    count = len(JsonFileModelRunRepository(root / "runs" / "model-runs.json").list_runs())
    assert count == 1
    results["two_application_instances"] = {"completed_calls": 2, "persisted_run_receipts": count}

    context_path = root / "memory" / "personal-context.json"
    writer = PersonalContextService(JsonFilePersonalContextRepository(context_path))
    remember(writer, "old-record", "synthetic astronomy preference")
    stale = PersonalContextService(JsonFilePersonalContextRepository(context_path))
    writer.delete("old-record", NOW)
    query = RetrievalQuery(text="astronomy", now=NOW, allowed_sources=("synthetic user",),
        allowed_sensitivities=("private",), processing_location="local")
    stale_ids = [r.record_id for r in stale.retrieve(query)]
    remember(stale, "new-record", "synthetic gardening preference")
    reloaded = JsonFilePersonalContextRepository(context_path)
    assert stale_ids == ["old-record"]
    assert any(r.record_id == "old-record" for r in reloaded.all_versions())
    assert not reloaded.suppression_markers()
    results["deletion_across_instances"] = {"deleted_record_still_retrievable": True,
        "unrelated_write_resurrects_record": True, "deletion_marker_lost": True}

    app = create_local_application(root / "web" / "continuity.json")
    interfaces = create_local_interface_application(app, root / "web" / "interfaces.json")
    web = ReckoningWebApplication(app, interface_application=interfaces)
    body = urlencode({"message": "Help me choose between studying and a project."}).encode()
    status = []
    environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/messages", "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded", "wsgi.input": BytesIO(body),
        "REMOTE_ADDR": "127.0.0.1", "HTTP_ORIGIN": "http://foreign.invalid",
        "HTTP_HOST": "foreign.invalid:8000", "HTTP_SEC_FETCH_SITE": "cross-site"}
    list(web(environ, lambda s, h: status.append(s)))
    assert status == ["303 See Other"]
    assert len(interfaces.channel_session("web")) == 2
    assert not (root / "web" / "continuity.json").exists()
    results["web_message"] = {"status": status[0], "conversation_messages": 2,
        "structured_continuity_created": False, "foreign_origin_and_host_accepted": True,
        "redirect_landing": interfaces.landing_area()}

    private_path = root / "cloud" / "personal-context.json"
    private = PersonalContextService(JsonFilePersonalContextRepository(private_path))
    remember(private, "local-only", "synthetic astronomy preference, designated local processing")
    requests = []
    def transport(request, timeout):
        requests.append(json.loads(request.data))
        return json.dumps({"model": "deepseek-chat", "choices": [{"message": {"content": "Synthetic response"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}).encode()
    cloud_app = create_local_application(root / "cloud" / "continuity.json", personal_context_path=private_path,
        provider_name="deepseek", provider_config=AdapterConfig(api_key="synthetic-key", model="deepseek-chat"),
        provider_transport=transport)
    cloud_app.send_message("astronomy")
    outgoing = json.dumps(requests[0])
    assert "designated local processing" in outgoing
    results["processing_placement"] = {"local_private_record_in_cloud_request": True, "network_calls": 0}

    class Adapter:
        def __init__(self): self.handled = []
        def handle_update(self, update):
            self.handled.append(update["update_id"])
            return SimpleNamespace(chat_id="synthetic-chat", text=str(update["update_id"]))
    class Client:
        def __init__(self): self.fail_once = True; self.sent = []
        def get_updates(self, *, offset, timeout): return ({"update_id": 1}, {"update_id": 2})
        def send_message(self, chat_id, text):
            if text == "2" and self.fail_once:
                self.fail_once = False
                raise TelegramBotApiError("synthetic delivery outage")
            self.sent.append(text)
    adapter, client = Adapter(), Client()
    polling = TelegramPollingApplication(adapter, client)
    offset = None
    try: offset = polling.run_once(offset)
    except TelegramBotApiError: pass
    offset = polling.run_once(offset)
    assert adapter.handled == [1, 2, 1, 2]
    assert client.sent == ["1", "1", "2"]
    results["telegram_batch_retry"] = {"updates_processed": adapter.handled, "replies_sent": client.sent}

    class FailedModelAdapter:
        def handle_update(self, update): raise RuntimeError("synthetic provider failure")
    try:
        TelegramPollingApplication(FailedModelAdapter(), Client()).run_forever(bot_username="synthetic", output=lambda _: None)
    except RuntimeError:
        results["telegram_provider_outage"] = {"provider_runtime_error_escapes_forever_loop": True}
    else: raise AssertionError("Expected runtime failure")

    recorder = TrialRecorder(InMemoryTrialRepository())
    trial = recorder.start("negative-trial", "continuity", NOW)
    recorder.record(trial.id, TrialEvidence("test", "automated_check", None, "All checks failed", NOW))
    for index, metric in enumerate(REQUIRED_REAL_USE_METRICS):
        trial = recorder.record(trial.id, TrialEvidence(str(index), "real_use", metric,
            "Rejected. No useful outcome; not worth keeping.", NOW))
    assert trial.accepted
    results["trial_acceptance"] = {"all_recorded_evidence_negative": True, "accepted": trial.accepted}

print(json.dumps(results, indent=2))
