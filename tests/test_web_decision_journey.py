"""Acceptance: the #124 web decision journey driven through rendered controls.

Every mutation submits values parsed from the rendered page (operation ids,
expected revisions, CSRF tokens); no intermediate domain mutation is seeded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    ReckoningProvider,
    SourcedFact,
)
from reckoning.providers import ProviderFailure
from reckoning.web import ReckoningWebApplication, local_browser_origins


SITUATION = "Exams, project, and job search all compete."
CORRECTED_MEANING = "Mary intends to protect exam preparation."


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


@dataclass
class ParsedForm:
    action: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    textareas: dict[str, str] = field(default_factory=dict)

    def submission(self, **overrides: str) -> dict[str, str]:
        fields = {
            name: value
            for name, value in self.fields.items()
            if name != "_csrf_token"
        }
        return {**fields, **self.textareas, **overrides}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[ParsedForm] = []
        self.decision_links: list[str] = []
        self.csrf_token = ""
        self._current_form: ParsedForm | None = None
        self._textarea_name = ""
        self._textarea_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._current_form = ParsedForm(action=values.get("action") or "")
        elif tag == "input":
            name = values.get("name")
            if name == "_csrf_token":
                self.csrf_token = values.get("value") or ""
            if self._current_form is not None and name:
                self._current_form.fields[name] = values.get("value") or ""
        elif tag == "textarea" and self._current_form is not None:
            self._textarea_name = values.get("name") or ""
            self._textarea_chunks = []
        elif tag == "a" and (values.get("href") or "").startswith("/decisions/"):
            self.decision_links.append(values["href"] or "")

    def handle_data(self, data: str) -> None:
        if self._current_form is not None and self._textarea_name:
            self._textarea_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self._current_form is not None:
            self._current_form.textareas[self._textarea_name] = "".join(
                self._textarea_chunks
            )
            self._textarea_name = ""
        elif tag == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None

    def form_by_action(self, action: str) -> ParsedForm:
        return next(form for form in self.forms if form.action == action)

    def forms_ending(self, suffix: str) -> list[ParsedForm]:
        return [form for form in self.forms if form.action.endswith(suffix)]


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
        parser = PageParser()
        parser.feed(body.decode("utf-8"))
        if parser.csrf_token:
            self.csrf_token = parser.csrf_token
        return status, headers, body

    def parse(self, body: bytes) -> PageParser:
        parser = PageParser()
        parser.feed(body.decode("utf-8"))
        return parser

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
    data_dir: Path, *, reckoning_provider: ReckoningProvider | None = None
) -> ReckoningWebApplication:
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
        reckoning_provider_override=reckoning_provider,
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


def install(data_dir: Path, *, first_conversation: tuple[str, str] | None = None) -> None:
    setup_instance(
        data_dir,
        "local",
        first_conversation=first_conversation,
        activation={
            "status": "activated",
            "provider": "fake",
            "model": "deterministic-fake",
            "demo": True,
        },
    )


def propose_via_composer(
    browser: BrowserSession, situation: str
) -> tuple[str, str]:
    """Submit the rendered Simon composer as Propose; return the decision URL."""
    status, _, page = browser.get("/simon")
    assert status == "200 OK"
    composer = browser.parse(page).form_by_action("/messages")
    status, headers, _ = browser.post(
        "/decisions",
        composer.submission(message=situation),
    )
    assert status == "303 See Other"
    return headers["Location"]


def test_full_web_decision_journey(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    install(
        data_dir,
        first_conversation=("Hello, Simon.", "Hello. What is on your mind?"),
    )
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    status, _, page = browser.get("/simon")
    assert status == "200 OK"
    assert b"Hello, Simon." in page

    # Casual conversation succeeds without creating a decision.
    composer = browser.parse(page).form_by_action("/messages")
    status, _, _ = browser.post(
        "/messages", composer.submission(message="Just thinking out loud.")
    )
    assert status == "303 See Other"
    status, _, page = browser.get("/")
    assert status == "200 OK"
    assert b'href="/decisions/' not in page

    # A scripted fictional conflict produces a visible proposal.
    decision_location = propose_via_composer(browser, SITUATION)
    decision_id = decision_location.rsplit("/", 1)[-1]

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert SITUATION.encode() in page
    assert b"proposed" in page
    # Reasons, uncertainty, and next action are visible.
    assert b"Protect the nearest exam." in page
    assert b"The exact exam date is missing." in page
    assert b"Confirm what protecting university means this week." in page

    proposal_page = browser.parse(page)
    # One compact confirmation control, bound to the rendered revision.
    confirm_forms = proposal_page.forms_ending("/confirm")
    assert len(confirm_forms) == 1
    correction_forms = proposal_page.forms_ending("/correct")
    assert len(correction_forms) == 1
    stale_correction = correction_forms[0].submission(meaning="Stale edit.")
    assert stale_correction["expected_revision"] == "1"

    # A second render of the same page carries its own operation identity.
    status, _, fresh_page = browser.get(decision_location)
    fresh_correction = browser.parse(fresh_page).forms_ending("/correct")[0]

    # Direct editing through the rendered control produces an unconfirmed revision.
    status, _, _ = browser.post(
        fresh_correction.action,
        fresh_correction.submission(meaning=CORRECTED_MEANING),
    )
    assert status == "303 See Other"

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert CORRECTED_MEANING.encode() in page
    assert b"Revision 2" in page
    assert b'class="status status-proposed"' in page

    # An old control cannot confirm or correct the new revision.
    status, _, page = browser.post(correction_forms[0].action, stale_correction)
    assert status == "409 Conflict"
    assert b"changed from revision 1 to 2" in page
    refreshed = browser.parse(page)
    assert refreshed.forms_ending("/confirm")[0].fields["expected_revision"] == "2"

    # The explicit control binds to the displayed record and version.
    status, _, _ = browser.post(
        f"/decisions/{decision_id}/confirm",
        refreshed.forms_ending("/confirm")[0].submission(),
    )
    assert status == "303 See Other"

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert b'class="status status-confirmed"' in page

    # After a restart, ordinary navigation reopens the saved decision.
    restarted = build_web_app(data_dir)
    browser.web = restarted
    status, _, page = browser.get("/")
    assert status == "200 OK"
    home = browser.parse(page)
    assert f"/decisions/{decision_id}" in home.decision_links
    assert b"Protect the degree baseline or maximize project momentum." in page

    status, _, page = browser.get(f"/decisions/{decision_id}")
    assert status == "200 OK"
    assert CORRECTED_MEANING.encode() in page
    assert b'class="status status-confirmed"' in page
    # The actual evidence is shown: the original message and the correction.
    assert SITUATION.encode() in page
    assert b"direct user correction" in page


def test_replay_after_a_saved_mutation_returns_the_same_decision(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    status, _, page = browser.get("/simon")
    composer = browser.parse(page).form_by_action("/messages")
    submission = composer.submission(message=SITUATION)

    status, headers, _ = browser.post("/decisions", submission)
    assert status == "303 See Other"
    decision_location = headers["Location"]

    # The browser retries the identical submission after a lost response.
    status, headers, _ = browser.post("/decisions", submission)
    assert status == "303 See Other"
    assert headers["Location"] == decision_location

    # The retry stays replay-safe after a full application restart.
    browser.web = build_web_app(data_dir)
    browser.get("/")  # A restarted server issues a fresh browser session.
    status, headers, _ = browser.post("/decisions", submission)
    assert status == "303 See Other"
    assert headers["Location"] == decision_location

    status, _, page = browser.get("/")
    assert len(browser.parse(page).decision_links) == 1


def test_conversational_correction_replay_creates_one_record_version(
    tmp_path: Path,
) -> None:
    """A completed conversational correction replay is safe; re-binding is not."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    decision_location = propose_via_composer(browser, SITUATION)
    composer = _simon_composer(browser)
    correction = composer.submission(message=f"No, correct it: {CORRECTED_MEANING}")

    status, _, _ = browser.post("/messages", correction)
    assert status == "303 See Other"
    status, _, page = browser.get(decision_location)
    assert b"Revision 2" in page

    # Identical retry after a lost response resolves the completed operation.
    status, _, _ = browser.post("/messages", correction)
    assert status == "303 See Other"
    status, _, page = browser.get(decision_location)
    assert b"Revision 2" in page
    assert b'class="status status-proposed"' in page

    # Reusing the operation id with a fresh binding (changed expected version)
    # conflicts instead of creating another mutation.
    fresh_composer = _simon_composer(browser)
    retry = {
        **fresh_composer.submission(message=f"No, correct it: {CORRECTED_MEANING}"),
        "operation_id": correction["operation_id"],
    }
    status, _, _ = browser.post("/messages", retry)
    assert status == "409 Conflict"
    status, _, page = browser.get(decision_location)
    assert b"Revision 2" in page
    assert b'class="status status-proposed"' in page

    # After restart the in-memory review context is gone, but the durable
    # operation identity still resolves the identical retry to its saved
    # result instead of failing on the process-local presentation receipt.
    browser.web = build_web_app(data_dir)
    browser.get("/simon")
    status, headers, _ = browser.post("/messages", correction)
    assert status == "303 See Other"
    assert headers["Location"] == decision_location
    status, _, page = browser.get(decision_location)
    assert b"Revision 2" in page
    assert b'class="status status-proposed"' in page

    # A fresh binding with the old operation id still conflicts.
    replay = {
        **_simon_composer(browser).submission(message=f"No, correct it: {CORRECTED_MEANING}"),
        "operation_id": correction["operation_id"],
    }
    status, _, _ = browser.post("/messages", replay)
    assert status == "409 Conflict"
    status, _, page = browser.get(decision_location)
    assert b"Revision 2" in page
    assert b'class="status status-proposed"' in page


def test_conversational_confirmation_replay_cannot_hit_a_later_proposal(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    first_location = propose_via_composer(browser, SITUATION)
    first_composer = _simon_composer(browser)
    first_confirmation = first_composer.submission(message="I confirm this version")

    status, _, _ = browser.post("/messages", first_confirmation)
    assert status == "303 See Other"
    status, _, page = browser.get(first_location)
    assert b'class="status status-confirmed"' in page

    # A later proposal reused for the same operation id is rejected.
    second_location = propose_via_composer(browser, "A different conflict now.")
    second_composer = _simon_composer(browser)
    reused = {
        **second_composer.submission(message="I confirm this version"),
        "operation_id": first_confirmation["operation_id"],
    }
    status, _, page = browser.post("/messages", reused)
    assert status == "409 Conflict"
    assert b"already completed with a different payload" in page

    status, _, page = browser.get(second_location)
    assert b'class="status status-proposed"' in page


def test_completed_confirmation_retry_replays_without_a_model_call(
    tmp_path: Path,
) -> None:
    """A retry after the proposal is gone resolves its saved operation."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    decision_location = propose_via_composer(browser, SITUATION)
    confirmation = _simon_composer(browser).submission(
        message="I confirm this version"
    )
    status, _, _ = browser.post("/messages", confirmation)
    assert status == "303 See Other"
    status, _, page = browser.get(decision_location)
    assert b'class="status status-confirmed"' in page

    runs_before = len(web._application.inspect_model_runs())
    # No proposal is open now. The identical retry must resolve the saved
    # operation instead of becoming an ordinary model request.
    status, headers, _ = browser.post("/messages", confirmation)
    assert status == "303 See Other"
    assert headers["Location"] == decision_location
    assert len(web._application.inspect_model_runs()) == runs_before

    status, _, page = browser.get(decision_location)
    assert b'class="status status-confirmed"' in page


def test_completed_confirmation_retry_with_changed_text_conflicts(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    decision_location = propose_via_composer(browser, SITUATION)
    confirmation = _simon_composer(browser).submission(
        message="I confirm this version"
    )
    status, _, _ = browser.post("/messages", confirmation)
    assert status == "303 See Other"

    status, _, page = browser.post(
        "/messages", {**confirmation, "message": "Confirm this version."}
    )
    assert status == "409 Conflict"
    assert b"already completed with a different payload" in page

    status, _, page = browser.get(decision_location)
    assert b'class="status status-confirmed"' in page


def test_failed_proposal_input_survives_restart_and_retries_once(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=FailingReckoningProvider())
    browser = BrowserSession(web)

    status, _, page = browser.get("/simon")
    composer = browser.parse(page).form_by_action("/messages")
    operation_id = composer.fields["operation_id"]

    status, _, page = browser.post(
        "/decisions", composer.submission(message=SITUATION)
    )
    assert status == "400 Bad Request"
    assert SITUATION.encode() in page

    # The received input and the failed operation survive a restart.
    browser.web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    status, _, page = browser.get("/simon")
    assert status == "200 OK"
    assert SITUATION.encode() in page
    recovery = browser.parse(page).form_by_action("/decisions")
    assert recovery.fields["operation_id"] == operation_id

    # Retrying the rendered recovery control completes the same operation once.
    status, headers, _ = browser.post("/decisions", recovery.submission())
    assert status == "303 See Other"
    decision_location = headers["Location"]

    status, _, page = browser.get("/")
    assert len(browser.parse(page).decision_links) == 1

    status, _, page = browser.get(decision_location)
    assert status == "200 OK"
    assert b'class="status status-proposed"' in page

    browser.web = build_web_app(data_dir)
    status, _, page = browser.get("/simon")
    assert b"data-pending-operation" not in page


def _simon_composer(browser: BrowserSession) -> ParsedForm:
    status, _, page = browser.get("/simon")
    assert status == "200 OK"
    return browser.parse(page).form_by_action("/messages")


def test_conversational_correction_and_confirmation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    # Ambiguous assent asks for clarification instead of confirming.
    first_location = propose_via_composer(browser, SITUATION)
    composer = _simon_composer(browser)
    status, headers, _ = browser.post("/messages", composer.submission(message="yes"))
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = browser.get("/simon")
    assert b"Do you want to confirm" in page
    status, _, page = browser.get(first_location)
    assert b'class="status status-proposed"' in page

    # Conversational correction produces an unconfirmed, visible revision.
    composer = _simon_composer(browser)
    status, headers, _ = browser.post(
        "/messages",
        composer.submission(message=f"No, correct it: {CORRECTED_MEANING}"),
    )
    assert status == "303 See Other"
    assert headers["Location"] == first_location
    status, _, page = browser.get(first_location)
    assert CORRECTED_MEANING.encode() in page
    assert b"Revision 2" in page
    assert b'class="status status-proposed"' in page

    # An unambiguous conversational confirmation binds to the shown version.
    composer = _simon_composer(browser)
    status, headers, _ = browser.post(
        "/messages", composer.submission(message="I confirm this version")
    )
    assert status == "303 See Other"
    assert headers["Location"] == first_location
    status, _, page = browser.get(first_location)
    assert b'class="status status-confirmed"' in page

    status, _, page = browser.get("/simon")
    assert b"exactly as shown (revision 2)" in page

    # Casual conversation still works and never creates a decision.
    composer = _simon_composer(browser)
    status, headers, _ = browser.post(
        "/messages", composer.submission(message="What should I focus on this week?")
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = browser.get("/")
    assert len(browser.parse(page).decision_links) == 1


def test_stale_composer_binding_asks_for_review_instead_of_confirming(
    tmp_path: Path,
) -> None:
    """A composer rendered for revision N must not confirm revision N+1."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    decision_location = propose_via_composer(browser, SITUATION)
    stale_composer = _simon_composer(browser)
    assert stale_composer.fields["decision_presentation_token"]

    # Another tab corrects the proposal to revision 2.
    decision_page = browser.parse(browser.get(decision_location)[2])
    correction = decision_page.forms_ending("/correct")[0]
    status, _, _ = browser.post(
        correction.action, correction.submission(meaning=CORRECTED_MEANING)
    )
    assert status == "303 See Other"

    # The stale composer asks for a fresh review; it does not confirm.
    status, headers, _ = browser.post(
        "/messages", stale_composer.submission(message="I confirm this version")
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = browser.get("/simon")
    assert b"changed from revision 1 to revision 2" in page

    status, _, page = browser.get(decision_location)
    assert b'class="status status-proposed"' in page


def test_conversational_mutation_requires_a_rendered_proposal_binding(
    tmp_path: Path,
) -> None:
    """A 'confirm' post with operation id but no target fields never mutates."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    propose_via_composer(browser, SITUATION)
    browser.get("/simon")

    status, headers, _ = browser.post(
        "/messages",
        {
            "message": "I confirm this version",
            "_csrf_token": browser.csrf_token,
            "operation_id": "op-missing-binding",
        },
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = browser.get("/simon")
    assert b"Open the decision and review the proposal" in page


def test_conversational_mutation_requires_operation_identity(
    tmp_path: Path,
) -> None:
    """A 'confirm' post with a valid binding but no operation id never mutates."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    propose_via_composer(browser, SITUATION)
    composer = _simon_composer(browser)

    status, _, page = browser.post(
        "/messages",
        {
            "message": "I confirm this version",
            "_csrf_token": browser.csrf_token,
            "decision_presentation_token": composer.fields[
                "decision_presentation_token"
            ],
        },
    )
    assert status == "400 Bad Request"
    assert b"Operation identity is required" in page


def test_client_supplied_target_fields_are_not_presentation_evidence(
    tmp_path: Path,
) -> None:
    """Echoing the current server proposal id/version never confers consent."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    decision_location = propose_via_composer(browser, SITUATION)
    reckoning = web._application.inspect_reckoning(
        decision_location.rsplit("/", 1)[-1]
    )
    browser.get("/simon")

    # A client that read the current state and echoes it back without a
    # server-issued presentation token gets a clarification, not a mutation.
    status, headers, _ = browser.post(
        "/messages",
        {
            "message": "I confirm this version",
            "_csrf_token": browser.csrf_token,
            "operation_id": "op-fabricated-binding",
            "decision_target_id": reckoning.id,
            "decision_target_version": str(reckoning.version),
        },
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = browser.get("/simon")
    assert b"Open the decision and review the proposal" in page
    assert (
        web._application.inspect_reckoning(reckoning.id).status == "proposed"
    )


def test_presentation_token_from_another_session_does_not_bind(
    tmp_path: Path,
) -> None:
    """A valid presentation token is bound to the session that received it."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    first = BrowserSession(web)
    second = BrowserSession(web)

    propose_via_composer(first, SITUATION)
    first_composer = _simon_composer(first)
    second.get("/simon")

    status, headers, _ = second.post(
        "/messages",
        {
            "message": "I confirm this version",
            "_csrf_token": second.csrf_token,
            "operation_id": "op-borrowed-token",
            "decision_presentation_token": first_composer.fields[
                "decision_presentation_token"
            ],
        },
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = second.get("/simon")
    assert b"review context is missing or expired" in page


def test_unknown_presentation_token_asks_for_a_fresh_review(
    tmp_path: Path,
) -> None:
    """An unknown or expired review context cannot silently select a proposal."""
    data_dir = tmp_path / "data"
    install(data_dir)
    web = build_web_app(data_dir, reckoning_provider=ScriptedReckoningProvider())
    browser = BrowserSession(web)

    decision_location = propose_via_composer(browser, SITUATION)
    browser.get("/simon")

    status, headers, _ = browser.post(
        "/messages",
        {
            "message": "I confirm this version",
            "_csrf_token": browser.csrf_token,
            "operation_id": "op-unknown-token",
            "decision_presentation_token": "not-a-issued-token",
        },
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    status, _, page = browser.get("/simon")
    assert b"review context is missing or expired" in page
    reckoning_id = decision_location.rsplit("/", 1)[-1]
    assert web._application.inspect_reckoning(reckoning_id).status == "proposed"
