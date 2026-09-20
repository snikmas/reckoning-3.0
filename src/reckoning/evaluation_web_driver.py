"""Web-driving helpers for conversation-quality evaluation.

Drives the real ReckoningWebApplication through rendered forms so the
scenario runner exercises the same browser-mutation path a user does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Callable, Iterable, cast
from urllib.request import Request
from urllib.parse import urlencode

from reckoning.application import ReckoningApplication
from reckoning.config import DEFAULT_PROVIDER_CREDENTIALS, RuntimeProviderSettings
from reckoning.continuity import (
    Evidence,
    Inference,
    MaterialQuestion,
    PersonalRecordProposal,
    PersonalRecordType,
    Reckoning,
    ReckoningDraft,
    SourcedFact,
)
from reckoning.interfaces import create_local_interface_application
from reckoning.operations import load_installation_runtime, setup_instance
from reckoning.provider_adapters import AdapterConfig
from reckoning.web import ReckoningWebApplication, local_browser_origins


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


class ScriptedModel:
    """Returns scripted assistant text for deterministic fake scenarios."""

    def __init__(self, outputs: tuple[str, ...]) -> None:
        self._outputs = outputs
        self._index = 0

    def respond(self, request: object) -> str:
        del request
        if self._index < len(self._outputs):
            output = self._outputs[self._index]
            self._index += 1
            return output
        return "Simulated response."


class ScriptedReckoningProvider:
    """Returns a scripted ReckoningDraft for deterministic fake scenarios."""

    def __init__(self, draft: dict[str, Any] | None) -> None:
        self._draft = draft

    def reckon(self, unstructured_input: str) -> ReckoningDraft:
        del unstructured_input
        if self._draft is None:
            return _default_reckoning_draft()
        return _draft_from_dict(self._draft)


def _default_reckoning_draft() -> ReckoningDraft:
    evidence = Evidence(id="msg", source="current user message", content="")
    return ReckoningDraft(
        conflict="The user described a conflict.",
        questions=(),
        matters_now=("Identify the nearest irreversible consequence.",),
        maintained=("Keep the smallest necessary maintenance.",),
        parked=("Park work with no current consequence.",),
        uncertainties=("Deadlines and available time are not yet known.",),
        known=(SourcedFact(text="The user described competing concerns.", evidence_ids=("msg",)),),
        inferences=(
            Inference(
                text="The concerns may compete for the same capacity.",
                evidence_ids=("msg",),
                uncertainty="This is an inference until confirmed.",
            ),
        ),
        evidence=(evidence,),
        next_step="Name the nearest irreversible consequence.",
    )


def _draft_from_dict(data: dict[str, Any]) -> ReckoningDraft:
    return ReckoningDraft(
        conflict=str(data["conflict"]),
        questions=tuple(
            MaterialQuestion(str(q["text"]), str(q["effect_on_recommendation"]))
            for q in data.get("questions", [])
        ),
        matters_now=tuple(str(x) for x in data.get("matters_now", [])),
        maintained=tuple(str(x) for x in data.get("maintained", [])),
        parked=tuple(str(x) for x in data.get("parked", [])),
        uncertainties=tuple(str(x) for x in data.get("uncertainties", [])),
        known=tuple(
            SourcedFact(str(f["text"]), tuple(str(x) for x in f.get("evidence_ids", [])))
            for f in data.get("known", [])
        ),
        inferences=tuple(
            Inference(
                str(i["text"]),
                tuple(str(x) for x in i.get("evidence_ids", [])),
                str(i["uncertainty"]),
            )
            for i in data.get("inferences", [])
        ),
        evidence=tuple(
            Evidence(str(e["id"]), str(e["source"]), str(e["content"]))
            for e in data.get("evidence", [])
        ),
        next_step=str(data["next_step"]),
        proposed_records=tuple(
            PersonalRecordProposal(
                cast(PersonalRecordType, str(r["record_type"])),
                str(r["meaning"]),
                tuple(str(x) for x in r.get("evidence_ids", [])),
            )
            for r in data.get("proposed_records", [])
        ),
    )


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[ParsedForm] = []
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
class _BrowserSession:
    web: ReckoningWebApplication
    origin: str = "http://127.0.0.1:8000"
    cookie: str = ""
    csrf_token: str = ""

    def get(self, path: str) -> tuple[str, dict[str, str], bytes]:
        status, header_pairs, body = self._raw_request("GET", path)
        headers = dict(header_pairs)
        if "Set-Cookie" in headers:
            self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        parser = _PageParser()
        parser.feed(body.decode("utf-8"))
        if parser.csrf_token:
            self.csrf_token = parser.csrf_token
        return status, headers, body

    def parse(self, body: bytes) -> _PageParser:
        parser = _PageParser()
        parser.feed(body.decode("utf-8"))
        return parser

    def post(
        self, path: str, form: dict[str, str] | None = None
    ) -> tuple[str, dict[str, str], bytes]:
        fields = {"_csrf_token": self.csrf_token, **(form or {})}
        status, header_pairs, body = self._raw_request(
            "POST",
            path,
            form=fields,
            environ_overrides={
                "HTTP_ORIGIN": self.origin,
                "HTTP_SEC_FETCH_SITE": "same-origin",
            },
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
        ) -> Callable[[bytes], object]:
            del exc_info
            nonlocal captured_status, captured_headers
            captured_status = status
            captured_headers = headers
            return lambda _chunk: None

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


def build_evaluation_web_app(
    data_dir: Path,
    *,
    activation: dict[str, Any] | None = None,
    provider_name: str | None = None,
    scripted_model_outputs: tuple[str, ...] | None = None,
    scripted_reckoning: dict[str, Any] | None = None,
    transport: Callable[[Request, float], bytes] | None = None,
    credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
) -> ReckoningWebApplication:
    """Set up a temporary installation and return a wired web application."""
    from reckoning.application import create_local_application
    from reckoning.provider_adapters import urlopen_transport

    fake_activation = {
        "status": "activated",
        "provider": "fake",
        "model": "deterministic-fake",
        "demo": True,
    }
    selected_activation = activation if activation is not None else fake_activation
    selected_provider_name = str(provider_name or selected_activation.get("provider", "fake"))

    setup_instance(
        data_dir,
        "local",
        first_conversation=("Hello, Simon.", "Hello. What is on your mind?"),
        activation=selected_activation,
    )
    runtime = load_installation_runtime(data_dir)
    provider = RuntimeProviderSettings.load(
        data_dir,
        credentials_path=credentials_path,
        provider_name=selected_provider_name,
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
        provider_transport=transport or urlopen_transport,
        persona=runtime.persona,
        placement=runtime.application_placement,
        model_override=(
            ScriptedModel(scripted_model_outputs or ())
            if scripted_model_outputs is not None or scripted_reckoning is not None
            else None
        ),
        reckoning_provider_override=(
            ScriptedReckoningProvider(scripted_reckoning)
            if scripted_model_outputs is not None or scripted_reckoning is not None
            else None
        ),
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


class WebEvaluationDriver:
    """Drive one scenario through the real web application."""

    def __init__(
        self,
        data_dir: Path,
        *,
        activation: dict[str, Any] | None = None,
        provider_name: str | None = None,
        scripted_model_outputs: tuple[str, ...] | None = None,
        scripted_reckoning: dict[str, Any] | None = None,
        transport: Callable[[Request, float], bytes] | None = None,
        credentials_path: Path = DEFAULT_PROVIDER_CREDENTIALS,
    ) -> None:
        self._data_dir = data_dir
        self._web = build_evaluation_web_app(
            data_dir,
            activation=activation,
            provider_name=provider_name,
            scripted_model_outputs=scripted_model_outputs,
            scripted_reckoning=scripted_reckoning,
            transport=transport,
            credentials_path=credentials_path,
        )
        self._browser = _BrowserSession(self._web)
        self._last_reckoning: Reckoning | None = None
        self._last_record_id: str | None = None
        self._observed: list[str] = []
        self._assistant_text: list[str] = []
        self._missing: list[str] = []
        self._composers_by_revision: dict[int, ParsedForm] = {}

    @property
    def application(self) -> ReckoningApplication:
        return self._web.application

    def send_message(self, text: str) -> None:
        self._browser.get("/simon")
        composer = self._current_composer()
        status, headers, _ = self._browser.post(
            "/messages", composer.submission(message=text)
        )
        self._observed.append(f"message status={status} redirect={headers.get('Location', '')}")
        if not status.startswith("303"):
            self._assistant_text.append("")
            return
        location = headers.get("Location", "")
        if location.startswith("/decisions/"):
            decision_status, _, _ = self._browser.get(location)
            self._observed.append(f"decision-follow status={decision_status}")
        simon_status, _, body = self._browser.get("/simon")
        self._observed.append(f"simon status={simon_status}")
        assistant = self._extract_assistant_text(body)
        self._assistant_text.append(assistant)
        if assistant:
            self._observed.append(assistant)

    def start_reckoning(self, text: str) -> None:
        self._browser.get("/simon")
        composer = self._current_composer()
        status, headers, _ = self._browser.post(
            "/decisions", composer.submission(message=text)
        )
        self._observed.append(f"reckon status={status} redirect={headers.get('Location', '')}")
        if status.startswith("303") and headers.get("Location", "").startswith("/decisions/"):
            decision_id = headers["Location"].rsplit("/", 1)[-1]
            self._last_reckoning = self.application.inspect_reckoning(decision_id)
            if self._last_reckoning.current_records:
                self._last_record_id = self._last_reckoning.current_records[0].record_id
            self._observed.append(
                f"Reckoning {self._last_reckoning.id} "
                f"status={self._last_reckoning.status} "
                f"version={self._last_reckoning.version}"
            )
            self._capture_proposal_composer()
        else:
            self._last_reckoning = None
            self._last_record_id = None

    def correct_record(self, text: str) -> None:
        """Correct through the rendered /messages composer, not a direct form."""
        if self._last_reckoning is None or self._last_record_id is None:
            raise RuntimeError("correct step requires a preceding reckon step.")
        decision_id = self._last_reckoning.id
        composer = self._composer_for_current_revision()
        if composer is None:
            self._observed.append("No rendered proposal composer found")
            return
        status, headers, _ = self._browser.post(
            "/messages",
            composer.submission(message=f"No, correct it: {text}"),
        )
        self._observed.append(
            f"correct status={status} redirect={headers.get('Location', '')}"
        )
        if status.startswith("303") and headers.get("Location", "").startswith("/decisions/"):
            self._last_reckoning = self.application.inspect_reckoning(decision_id)
            self._observed.append(
                f"Corrected {self._last_record_id} to version "
                f"{self._last_reckoning.current_records[0].version}"
            )
            self._capture_proposal_composer()
        else:
            self._observed.append(
                "Correction not applied: the handler asked for review"
            )

    def confirm_reckoning(self, expected_revision: int | None = None) -> None:
        """Confirm through the rendered /messages composer.

        When expected_revision names a rendered revision, the form captured for
        that revision is submitted as-is. A stale form is sent to the real
        handler; the driver never rejects it before the application sees it.
        """
        if self._last_reckoning is None:
            raise RuntimeError("confirm step requires a preceding reckon step.")
        decision_id = self._last_reckoning.id
        if expected_revision is not None:
            composer = self._composers_by_revision.get(expected_revision)
            if composer is None:
                self._observed.append(
                    f"No rendered composer for revision {expected_revision}"
                )
                return
        else:
            composer = self._composer_for_current_revision()
            if composer is None:
                self._observed.append("No rendered proposal composer found")
                return
        status, headers, _ = self._browser.post(
            "/messages", composer.submission(message="I confirm this version")
        )
        self._observed.append(
            f"confirm status={status} redirect={headers.get('Location', '')}"
        )
        self._last_reckoning = self.application.inspect_reckoning(decision_id)
        if self._last_reckoning.status == "confirmed":
            self._observed.append(f"Confirmed version {self._last_reckoning.version}")
        else:
            self._observed.append(
                f"Confirm rejected: status={status} "
                f"redirect={headers.get('Location', '')}"
            )

    def _composer_for_current_revision(self) -> ParsedForm | None:
        captured = self._capture_proposal_composer()
        if captured is None:
            return None
        _revision, composer = captured
        return composer

    def _capture_proposal_composer(self) -> tuple[int, ParsedForm] | None:
        status, _, body = self._browser.get("/simon")
        if not status.startswith("200"):
            return None
        text = body.decode("utf-8")
        match = re.search(r"\(revision (\d+)\)", text)
        parser = _PageParser()
        parser.feed(text)
        try:
            composer = parser.form_by_action("/messages")
        except StopIteration:
            return None
        if match is None:
            return None
        revision = int(match.group(1))
        self._composers_by_revision[revision] = composer
        return revision, composer

    def explain_reckoning(self) -> None:
        if self._last_reckoning is None:
            raise RuntimeError("explain step requires a preceding reckon step.")
        why = self.application.explain_reckoning(self._last_reckoning.id)
        self._observed.append(
            f"Why view: {len(why.evidence)} evidence, {len(why.record_versions)} records"
        )

    def record_check_in(self, outcome: str) -> None:
        self._missing.append("check_in")

    def resume_decision(self) -> None:
        self._missing.append("resume")

    def profile_reject(self) -> None:
        self._missing.append("profile fact rejection")

    def feedback(self) -> None:
        self._missing.append("reply feedback")

    def missing_journey(self, journey: str) -> None:
        """Record a journey owned by a later ticket as missing implementation."""
        self._missing.append(journey)

    def finish(self) -> tuple[str, str, dict[str, Any]]:
        if self._missing:
            raise MissingImplementationError(
                "Required operations not implemented: "
                + ", ".join(sorted(set(self._missing)))
            )
        raw_runs = self.application.inspect_model_runs()
        runs = [
            {
                "provider": run.provider,
                "model": run.model,
                "status": run.status,
                "latency_ms": run.latency_ms,
                "retries": run.retries,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "billable_units": run.billable_units,
            }
            for run in raw_runs
        ]
        reckonings = self.application.list_reckonings()
        confirmed = [r for r in reckonings if r.status == "confirmed"]
        proposed = [r for r in reckonings if r.status == "proposed"]
        last_reckoning = self._last_reckoning
        evidence: dict[str, Any] = {
            "model_run_count": len(runs),
            "model_run_records": list(runs),
            "last_run_status": runs[-1]["status"] if runs else None,
            "last_run_provider": runs[-1]["provider"] if runs else None,
            "confirmed_reckoning_count": len(confirmed),
            "proposed_reckoning_count": len(proposed),
            "last_reckoning_status": last_reckoning.status if last_reckoning else None,
            "last_reckoning_version": last_reckoning.version if last_reckoning else None,
            "last_reckoning_meanings": [
                record.meaning for record in (last_reckoning.current_records if last_reckoning else ())
            ],
        }
        return "\n".join(self._observed), "\n".join(self._assistant_text), evidence

    def _current_composer(self) -> ParsedForm:
        status, _, body = self._browser.get("/simon")
        if not status.startswith("200"):
            raise RuntimeError(f"Could not load /simon: {status}")
        parser = _PageParser()
        parser.feed(body.decode("utf-8"))
        return parser.form_by_action("/messages")

    @staticmethod
    def _extract_assistant_text(body: bytes) -> str:
        import re

        text = body.decode("utf-8")
        # Extract the most recent assistant message from the rendered page.
        # This is a coarse heuristic for fake-mode authority scoring only.
        marker = '<strong>Simon</strong>'
        index = text.rfind(marker)
        if index == -1:
            return ""
        article_start = text.rfind('<article', 0, index)
        article_end = text.find("</article>", index)
        if article_end == -1:
            article_end = len(text)
        block = text[article_start:article_end]
        cleaned = re.sub(r"<[^>]+>", "", block)
        if cleaned.startswith("Simon"):
            cleaned = cleaned[len("Simon") :].lstrip()
        return cleaned


class MissingImplementationError(RuntimeError):
    """A scenario step requires a product operation that is not yet available."""
