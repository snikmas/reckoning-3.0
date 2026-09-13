from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

from reckoning.application import create_local_application
from reckoning.config import DEFAULT_PROVIDER_CREDENTIALS, RuntimeProviderSettings
from reckoning.interfaces import create_local_interface_application
from reckoning.operations import setup_instance
from reckoning.provider_adapters import AdapterConfig
from reckoning.continuity import (
    Evidence,
    PersonalRecordProposal,
    ReckoningDraft,
    SourcedFact,
)
from reckoning.providers import ProviderFailure
from reckoning.web import ReckoningWebApplication, local_browser_origins


class ScriptedReckoningProvider:
    def reckon(self, unstructured_input: str) -> object:
        evidence = Evidence(
            id="message-evidence",
            source="current user message",
            content=unstructured_input,
        )
        return ReckoningDraft(
            conflict="Protect the degree baseline or maximize project momentum.",
            questions=(),
            matters_now=("Protect the nearest exam.",),
            maintained=("Keep one project block.",),
            parked=("Park broad job applications.",),
            uncertainties=("The exact exam date is missing.",),
            known=(
                SourcedFact(
                    text="Three commitments compete for time.",
                    evidence_ids=("message-evidence",),
                ),
            ),
            inferences=(),
            evidence=(evidence,),
            next_step="Confirm what protecting university means this week.",
            proposed_records=(
                PersonalRecordProposal(
                    record_type="current_state",
                    meaning="Mary has chosen university over the project.",
                    evidence_ids=("message-evidence",),
                ),
            ),
        )


class RecordParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.record_id = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "article" and values.get("data-record-id"):
            self.record_id = values["data-record-id"] or ""


class TokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("name") == "_csrf_token":
            self.token = values.get("value") or ""


@dataclass
class BrowserSession:
    web: ReckoningWebApplication
    origin: str = "http://127.0.0.1:8000"
    cookie: str = ""
    csrf_token: str = ""

    def get(self, path: str) -> tuple[str, dict[str, str], bytes]:
        status, header_pairs, body = self._raw_request("GET", path)
        headers = dict(header_pairs)
        if "Set-Cookie" in headers:
            self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        parser = TokenParser()
        parser.feed(body.decode("utf-8"))
        if parser.token:
            self.csrf_token = parser.token
        return status, headers, body

    def post(
        self,
        path: str,
        form: dict[str, str] | None = None,
        *,
        environ_overrides: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, str], bytes]:
        fields = {"_csrf_token": self.csrf_token, **(form or {})}
        overrides = {
            "HTTP_ORIGIN": self.origin,
            "HTTP_SEC_FETCH_SITE": "same-origin",
            **(environ_overrides or {}),
        }
        status, header_pairs, body = self._raw_request(
            "POST", path, form=fields, environ_overrides=overrides
        )
        return status, dict(header_pairs), body

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        form: dict[str, str] | None = None,
        environ_overrides: dict[str, object] | None = None,
    ) -> tuple[str, list[tuple[str, str]], bytes]:
        body = urlencode(form or {}).encode("utf-8")
        captured_status = ""
        captured_headers: list[tuple[str, str]] = []

        def start_response(
            status: str,
            headers: list[tuple[str, str]],
            exc_info: object | None = None,
        ) -> None:
            del exc_info
            nonlocal captured_status, captured_headers
            captured_status = status
            captured_headers = headers

        environ: dict[str, object] = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "wsgi.input": BytesIO(body),
            "wsgi.url_scheme": "http",
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8000",
            "HTTP_HOST": "127.0.0.1:8000",
            "REMOTE_ADDR": "127.0.0.1",
        }
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie
        if environ_overrides:
            environ.update(environ_overrides)
        response: Iterable[bytes] = self.web(environ, start_response)
        return captured_status, captured_headers, b"".join(response)


def build_web_app(
    data_dir: Path, *, reckoning_provider: object | None = None
) -> ReckoningWebApplication:
    from dataclasses import replace
    from reckoning.operations import load_installation_runtime

    runtime = load_installation_runtime(data_dir)
    provider = RuntimeProviderSettings.load(
        data_dir,
        credentials_path=DEFAULT_PROVIDER_CREDENTIALS,
        provider_name="fake",
    )
    application = create_local_application(
        runtime.state_path("confirmed-state", "continuity.json"),
        personal_context_path=runtime.state_path(
            "personal-context", "personal-context.json"
        ),
        provider_name=provider.provider_name,
        provider_config=AdapterConfig(
            api_key=provider.api_key,
            model=provider.model,
            base_url=provider.base_url,
            protocol=provider.protocol,
            context_window=provider.context_window,
            headers=provider.headers,
        ),
        persona=runtime.persona,
        placement=runtime.application_placement,
    )
    if reckoning_provider is not None:
        application._dependencies = replace(
            application._dependencies, reckoning_provider=reckoning_provider
        )
    interface = create_local_interface_application(
        application,
        runtime.state_path("confirmed-state", "interfaces.json"),
        placement=runtime.interface_placement,
        connector_data_dir=runtime.root_for("approved-remote-sources"),
    )
    return ReckoningWebApplication(
        application,
        interface_application=interface,
        allowed_origins=local_browser_origins(8000),
    )


def test_full_web_decision_journey(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    setup_instance(
        data_dir,
        "local",
        first_conversation=("Hello, Simon.", "Hello. What is on your mind?"),
        activation={
            "status": "activated",
            "provider": "fake",
            "model": "deterministic-fake",
            "demo": True,
        },
    )
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    status, _, page = browser.get("/simon")
    assert status == "200 OK"
    assert b"Hello, Simon." in page

    status, _, page = browser.post("/messages", {"message": "Just thinking out loud."})
    assert status == "303 See Other"

    status, headers, _ = browser.post(
        "/decisions",
        {"situation": "Exams, project, and job search all compete."},
    )
    assert status == "303 See Other"
    decision_location = headers["Location"]
    decision_id = decision_location.rsplit("/", 1)[-1]

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert b"Exams, project, and job search all compete." in page
    assert b"proposed" in page
    record_parser = RecordParser()
    record_parser.feed(page.decode("utf-8"))
    record_id = record_parser.record_id
    assert record_id

    status, _, page = browser.post(
        f"/decisions/{decision_id}/records/{record_id}/correct",
        {
            "expected_revision": "1",
            "meaning": "Mary intends to protect exam preparation.",
        },
    )
    assert status == "303 See Other"

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert b"Mary intends to protect exam preparation." in page

    status, _, page = browser.post(
        f"/decisions/{decision_id}/confirm",
        {"expected_revision": "2"},
    )
    assert status == "303 See Other"

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert b"confirmed" in page

    restarted = build_web_app(data_dir)
    browser.web = restarted
    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert b"Mary intends to protect exam preparation." in page
    assert b"confirmed" in page


def test_provider_failure_and_retry_does_not_duplicate_decision(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    setup_instance(
        data_dir,
        "local",
        activation={
            "status": "activated",
            "provider": "fake",
            "model": "deterministic-fake",
            "demo": True,
        },
    )
    web = build_web_app(data_dir)
    browser = BrowserSession(web)
    browser.get("/simon")

    failing_provider = FailingReckoningProvider()
    web._application._dependencies = (
        web._application._dependencies
    )  # keep reference reachable
    original = web._application._dependencies.reckoning_provider
    web._application._dependencies = web._application._dependencies.__class__(
        **{
            **web._application._dependencies.__dict__,
            "reckoning_provider": failing_provider,
        }
    )

    status, _, page = browser.post(
        "/decisions", {"situation": "Competing commitments."}
    )
    assert status == "400 Bad Request"
    assert b"Competing commitments." in page

    web._application._dependencies = web._application._dependencies.__class__(
        **{
            **web._application._dependencies.__dict__,
            "reckoning_provider": original,
        }
    )

    status, headers, _ = browser.post(
        "/decisions", {"situation": "Competing commitments."}
    )
    assert status == "303 See Other"
    decision_id = headers["Location"].rsplit("/", 1)[-1]

    status, _, page = browser.get(headers["Location"])
    assert status == "200 OK"
    assert decision_id.encode() in page


class FailingReckoningProvider:
    def reckon(self, unstructured_input: str) -> object:
        raise ProviderFailure(
            provider="fake",
            model="deterministic-fake",
            model_calls=1,
            latency_ms=0,
            retries=0,
            message="Simulated provider failure.",
        )
