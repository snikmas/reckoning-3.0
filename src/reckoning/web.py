from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from hmac import compare_digest
from html import escape
from http.cookies import SimpleCookie
from ipaddress import ip_address
from pathlib import Path
from secrets import token_urlsafe
from typing import Iterable
from urllib.parse import parse_qs, urlsplit
from wsgiref.simple_server import make_server
from wsgiref.types import StartResponse, WSGIEnvironment

from reckoning.application import (
    Message,
    ReckoningApplication,
    create_local_application,
)
from reckoning.config import (
    CREDENTIAL_PROVIDER_NAMES,
    DEFAULT_PROVIDER_CREDENTIALS,
    RuntimeProviderSettings,
)
from reckoning.continuity import (
    OperationRecord,
    Reckoning,
    ReckoningOperationConflict,
    ReckoningProviderError,
    ReckoningRevisionConflict,
    WhyView,
)
from reckoning.decision_conversation import (
    ClarifyDecision,
    ConfirmDecision,
    CorrectDecision,
    DecisionReply,
    DecisionTarget,
    NotDecisionRelated,
    interpret_decision_message,
)
from reckoning.interfaces import (
    ChannelSession,
    ControlView,
    ReckoningInterfaceApplication,
    create_local_interface_application,
)
from reckoning.operations import OperationError, load_installation_runtime
from reckoning.processing import PROCESSING_CATEGORIES
from reckoning.provider_adapters import AdapterConfig
from reckoning.web_decisions import (
    fresh_operation_id,
    render_decision_content,
    render_list,
    render_pending_input_card,
)


def validate_bind_host(host: str) -> str:
    """Keep the unauthenticated built-in server off public interfaces."""
    if host == "localhost":
        return host
    try:
        loopback = ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(
            "the built-in server may bind only to a loopback address; use an "
            "SSH tunnel for remote access"
        )
    return host


def validate_allowed_origin(origin: str) -> str:
    """Return one canonical browser origin with no path or credentials."""
    candidate = origin.strip().rstrip("/")
    parsed = urlsplit(candidate)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError(f"invalid allowed browser origin: {origin}") from error
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"invalid allowed browser origin: {origin}")
    scheme = parsed.scheme.casefold()
    hostname = parsed.hostname.casefold()
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port in ({"http": 80, "https": 443}[scheme], None):
        return f"{scheme}://{rendered_host}"
    return f"{scheme}://{rendered_host}:{port}"


def render_channel_text(text: str) -> str:
    """Render plain conversational text as safe HTML paragraphs and code blocks."""
    parts: list[str] = []
    lines = text.splitlines()
    in_fence = False
    fence_lines: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            paragraph = " ".join(paragraph_lines)
            parts.append(f"<p>{escape(paragraph)}</p>")
            paragraph_lines.clear()

    def flush_fence() -> None:
        if fence_lines:
            code = "\n".join(fence_lines)
            parts.append(f"<pre><code>{escape(code)}</code></pre>")
            fence_lines.clear()

    for line in lines:
        if line.startswith("```"):
            if in_fence:
                flush_fence()
                in_fence = False
            else:
                flush_paragraph()
                in_fence = True
        elif in_fence:
            fence_lines.append(line)
        elif line.strip() == "":
            flush_paragraph()
        else:
            paragraph_lines.append(line)

    if in_fence:
        flush_fence()
    flush_paragraph()
    return "".join(parts)


def local_browser_origins(port: int) -> tuple[str, ...]:
    """Supported loopback browser origins for the built-in server."""
    return (
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    )


# A rendered proposal review context stays valid for one hour. After that the
# user must re-open the current proposal before consenting.
_PRESENTATION_TTL_SECONDS = 3600.0
_PRESENTATION_RECEIPTS_PER_SESSION = 8


class ReckoningWebApplication:
    """A thin WSGI adapter for the Reckoning application boundary."""

    def __init__(
        self,
        application: ReckoningApplication,
        *,
        interface_application: ReckoningInterfaceApplication | None = None,
        allowed_origins: tuple[str, ...] | None = None,
    ) -> None:
        self._application = application
        self._interfaces = interface_application
        configured = allowed_origins or local_browser_origins(8000)
        self._allowed_origins = frozenset(
            validate_allowed_origin(origin) for origin in configured
        )
        if not self._allowed_origins:
            raise ValueError("at least one allowed browser origin is required")
        self._allowed_hosts = frozenset(
            urlsplit(origin).netloc.casefold() for origin in self._allowed_origins
        )
        self._browser_sessions: dict[str, str] = {}
        # Server-side evidence that a browser session was shown a specific
        # proposal revision: csrf token -> presentation token ->
        # (reckoning_id, version, issued_at). Client-submitted target fields
        # alone never prove presentation.
        self._proposal_presentations: dict[
            str, dict[str, tuple[str, int, float]]
        ] = {}

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        csrf_token = ""
        response_headers: tuple[tuple[str, str], ...] = ()
        if method == "GET":
            csrf_token, cookie = self._browser_session(environ, create=True)
            if cookie is not None:
                response_headers = (("Set-Cookie", cookie),)
        elif method not in {"HEAD", "OPTIONS"}:
            if self._mutation_rejection(environ) is not None:
                body = b"This request was rejected by the browser mutation policy."
                start_response(
                    "403 Forbidden",
                    [
                        ("Content-Type", "text/plain; charset=utf-8"),
                        ("Content-Length", str(len(body))),
                    ],
                )
                return [body]
            csrf_token, _ = self._browser_session(environ, create=False)

        if self._interfaces is not None:
            interface_response = self._handle_interface_request(
                method,
                path,
                environ,
                start_response,
                csrf_token=csrf_token,
                response_headers=response_headers,
            )
            if interface_response is not None:
                return interface_response

        if method == "GET" and path == "/":
            return self._html_response(
                start_response,
                "200 OK",
                self._render_session(
                    self._application.open_session(), csrf_token=csrf_token
                ),
                extra_headers=response_headers,
            )

        if method == "POST" and path == "/messages":
            try:
                self._application.send_message(self._read_message(environ))
            except (ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_session(
                        self._application.open_session(),
                        error=str(error),
                        csrf_token=csrf_token,
                    ),
                )
            start_response("303 See Other", [("Location", "/"), ("Content-Length", "0")])
            return [b""]

        body = b"Not found"
        start_response(
            "404 Not Found",
            [
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    def _handle_interface_request(
        self,
        method: str,
        path: str,
        environ: WSGIEnvironment,
        start_response: StartResponse,
        *,
        csrf_token: str,
        response_headers: tuple[tuple[str, str], ...],
    ) -> list[bytes] | None:
        assert self._interfaces is not None
        if (
            (
                (
                    method == "GET"
                    and (path == "/control" or path.startswith("/control/"))
                )
                or (method == "POST" and path == "/processing-scope")
            )
            and not self._interfaces.administration_allowed(
                str(environ.get("REMOTE_ADDR", "")),
                private_network_authenticated=environ.get(
                    "reckoning.private_network_authenticated"
                )
                is True,
                via_ssh_tunnel=environ.get("reckoning.ssh_tunnel") is True,
            )
        ):
            body = b"Administration requires a private access path."
            start_response(
                "403 Forbidden",
                [
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]
        if method == "GET" and path == "/":
            area = self._interfaces.landing_area()
            return self._html_response(
                start_response,
                "200 OK",
                self._render_interface_area(area, csrf_token=csrf_token),
                extra_headers=response_headers,
            )
        if method == "GET" and path in {
            "/home",
            "/simon",
            "/plan",
            "/review",
            "/control",
        }:
            return self._html_response(
                start_response,
                "200 OK",
                self._render_interface_area(
                    path.removeprefix("/"), csrf_token=csrf_token
                ),
                extra_headers=response_headers,
            )
        if method == "POST" and path == "/messages":
            fields = self._read_form(environ)
            message = fields.get("message", [""])[0]
            session_id = fields.get("session_id", [""])[0]
            expected_message_revision = fields.get("expected_revision", [""])[0]
            operation_id = fields.get("operation_id", [""])[0] or None
            decision_reply = self._interpret_decision_reply(
                message, fields, csrf_token
            )
            if decision_reply is not None:
                return self._apply_decision_reply(
                    decision_reply,
                    message,
                    start_response,
                    csrf_token=csrf_token,
                    response_headers=response_headers,
                    operation_id=operation_id,
                )
            try:
                if not session_id:
                    selected = self._interfaces.selected_channel_session("web")
                    if selected is None:
                        raise ValueError("A session is required to send a message.")
                    session_id = selected.session_id
                revision = (
                    int(expected_message_revision)
                    if expected_message_revision
                    else self._interfaces.session_revision("web", session_id)
                )
                self._interfaces.send_channel_message(
                    "web",
                    message,
                    session_id=session_id,
                    expected_session_revision=revision,
                )
            except (ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_interface_area(
                        "simon",
                        error=str(error),
                        csrf_token=csrf_token,
                        pending_text=message,
                    ),
                )
            start_response(
                "303 See Other",
                [("Location", "/simon"), ("Content-Length", "0")],
            )
            return [b""]
        if method == "POST" and path == "/sessions/new":
            fields = self._read_form(environ)
            name = fields.get("display_name", [""])[0].strip() or None
            try:
                session = self._interfaces.create_channel_session(
                    "web", display_name=name
                )
                selection = self._interfaces._repository.get_selected_session("web")
                expected = selection.revision if selection is not None else 0
                self._interfaces.select_channel_session(
                    "web",
                    session.session_id,
                    expected_selection_revision=expected,
                )
            except (ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_interface_area(
                        "simon", error=str(error), csrf_token=csrf_token
                    ),
                )
            start_response(
                "303 See Other",
                [("Location", "/simon"), ("Content-Length", "0")],
            )
            return [b""]
        if method == "POST" and path == "/sessions/select":
            fields = self._read_form(environ)
            session_id = fields.get("session_id", [""])[0]
            expected_selection_revision = int(
                fields.get("selection_revision", ["0"])[0]
            )
            try:
                self._interfaces.select_channel_session(
                    "web",
                    session_id,
                    expected_selection_revision=expected_selection_revision,
                )
            except (ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_interface_area(
                        "simon", error=str(error), csrf_token=csrf_token
                    ),
                )
            start_response(
                "303 See Other",
                [("Location", "/simon"), ("Content-Length", "0")],
            )
            return [b""]
        if method == "POST" and path == "/processing-scope":
            try:
                fields = self._read_form(environ)
                expected_revision = int(
                    fields.get("expected_revision", [""])[0]
                )
                categories = tuple(fields.get("category", []))
                self._application.change_processing_scope(
                    categories, expected_revision=expected_revision
                )
            except (RuntimeError, TypeError, ValueError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_interface_area(
                        "control", error=str(error), csrf_token=csrf_token
                    ),
                )
            start_response(
                "303 See Other",
                [("Location", "/control"), ("Content-Length", "0")],
            )
            return [b""]
        profile_action = self._profile_action(method, path)
        if profile_action is not None:
            record_id, action = profile_action
            try:
                if action == "correct":
                    self._application.correct_profile_proposal(
                        record_id,
                        self._read_form_field(environ, "meaning"),
                        language="und",
                    )
                elif action == "confirm":
                    self._application.confirm_profile_proposal(record_id)
                else:
                    self._application.reject_profile_proposal(record_id)
            except (KeyError, ValueError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "400 Bad Request",
                    self._render_interface_area(
                        "simon", error=str(error), csrf_token=csrf_token
                    ),
                )
            start_response(
                "303 See Other",
                [("Location", "/simon"), ("Content-Length", "0")],
            )
            return [b""]
        decision_action = self._decision_action(method, path)
        if decision_action is not None:
            return self._handle_decision_request(
                decision_action,
                environ,
                start_response,
                csrf_token=csrf_token,
                response_headers=response_headers,
            )
        return None

    def _issue_presentation(self, csrf_token: str, reckoning: Reckoning) -> str:
        """Record that this session was shown reckoning id/version; return its token."""
        receipts = self._proposal_presentations.setdefault(csrf_token, {})
        now = time.monotonic()
        expired = [
            token
            for token, (_, _, issued_at) in receipts.items()
            if now - issued_at > _PRESENTATION_TTL_SECONDS
        ]
        for token in expired:
            del receipts[token]
        while len(receipts) >= _PRESENTATION_RECEIPTS_PER_SESSION:
            oldest = min(receipts, key=lambda token: receipts[token][2])
            del receipts[oldest]
        token = token_urlsafe(16)
        receipts[token] = (reckoning.id, reckoning.version, now)
        return token

    def _presentation_receipt(
        self, csrf_token: str, token: str
    ) -> tuple[str, int] | None:
        """Return the server-recorded (reckoning_id, version) for a token."""
        receipt = self._proposal_presentations.get(csrf_token, {}).get(token)
        if receipt is None:
            return None
        reckoning_id, version, issued_at = receipt
        if time.monotonic() - issued_at > _PRESENTATION_TTL_SECONDS:
            return None
        return reckoning_id, version

    def _interpret_decision_reply(
        self, message: str, fields: dict[str, list[str]], csrf_token: str
    ) -> DecisionReply | None:
        """Resolve a conversation reply against the one active proposal.

        Consent requires a server-issued presentation token proving this
        session was rendered the proposal summary. Client-supplied fields are
        never evidence of presentation; a stale, expired, or missing token
        never confers consent.
        """
        list_reckonings = getattr(self._application, "list_reckonings", None)
        if not callable(list_reckonings):
            return None
        proposed = tuple(
            reckoning
            for reckoning in list_reckonings()
            if reckoning.status == "proposed"
        )
        if not proposed:
            return None
        if len(proposed) > 1:
            return ClarifyDecision(
                "Several proposals are open. Open the decision you mean from "
                "Home, then confirm or correct it there."
            )
        reckoning = proposed[0]

        token = fields.get("decision_presentation_token", [""])[0]
        bound = False
        bound_version = reckoning.version
        if token:
            receipt = self._presentation_receipt(csrf_token, token)
            if receipt is None:
                return ClarifyDecision(
                    "Your review context is missing or expired. Open the "
                    "decision again and review the current proposal before "
                    "confirming or correcting it."
                )
            target_id, target_version = receipt
            if target_id != reckoning.id or target_version != reckoning.version:
                # A completed operation resolves to its saved result before a
                # retry is reinterpreted against current proposal state. The
                # application still verifies the payload digest, so a changed
                # payload under the same operation id conflicts there.
                operation_id = fields.get("operation_id", [""])[0]
                if not self._completed_operation_for(operation_id, reckoning.id):
                    return ClarifyDecision(
                        f"The proposal changed from revision {target_version} to "
                        f"revision {reckoning.version}. Please review the current "
                        "version before confirming or correcting it."
                    )
                bound_version = target_version
            bound = True

        target = DecisionTarget(
            reckoning_id=reckoning.id,
            version=bound_version,
            conflict=reckoning.draft.conflict,
            record_ids=tuple(
                record.record_id for record in reckoning.current_records
            ),
        )
        reply = interpret_decision_message(message, target)
        if isinstance(reply, NotDecisionRelated):
            return None
        if isinstance(reply, (ConfirmDecision, CorrectDecision)) and not bound:
            return ClarifyDecision(
                "Open the decision and review the proposal before confirming or "
                "correcting it."
            )
        return reply

    def _apply_decision_reply(
        self,
        reply: DecisionReply,
        message: str,
        start_response: StartResponse,
        *,
        csrf_token: str,
        response_headers: tuple[tuple[str, str], ...],
        operation_id: str | None = None,
    ) -> list[bytes]:
        assert self._interfaces is not None
        web_session_id = self._selected_web_session_id()
        if isinstance(reply, ClarifyDecision):
            self._interfaces.record_channel_exchange(
                "web", message, reply.prompt, session_id=web_session_id
            )
            start_response(
                "303 See Other",
                [("Location", "/simon"), ("Content-Length", "0")],
            )
            return [b""]
        assert isinstance(reply, (ConfirmDecision, CorrectDecision))
        if operation_id is None:
            return self._html_response(
                start_response,
                "400 Bad Request",
                self._render_interface_area(
                    "decision",
                    error="Operation identity is required for confirmation or correction.",
                    csrf_token=csrf_token,
                    decision=self._safe_decision(reply.target.reckoning_id),
                ),
                extra_headers=response_headers,
            )
        target = reply.target
        try:
            if isinstance(reply, ConfirmDecision):
                outcome = self._application.confirm_reckoning(
                    target.reckoning_id,
                    expected_revision=target.version,
                    operation_id=operation_id,
                )
                note = (
                    f'Confirmed "{target.conflict}" exactly as shown '
                    f"(revision {target.version})."
                )
            else:
                outcome = self._application.correct_personal_record(
                    target.reckoning_id,
                    reply.record_id,
                    reply.meaning,
                    expected_revision=target.version,
                    operation_id=operation_id,
                )
                note = (
                    "Corrected the proposal. Revision "
                    f"{outcome.version} is ready for review."
                )
        except (ReckoningRevisionConflict, ReckoningOperationConflict) as error:
            return self._html_response(
                start_response,
                "409 Conflict",
                self._render_interface_area(
                    "decision",
                    error=str(error),
                    csrf_token=csrf_token,
                    decision=self._safe_decision(target.reckoning_id),
                ),
                extra_headers=response_headers,
            )
        except (KeyError, ValueError, RuntimeError) as error:
            return self._html_response(
                start_response,
                "400 Bad Request",
                self._render_interface_area(
                    "decision",
                    error=str(error),
                    csrf_token=csrf_token,
                    decision=self._safe_decision(target.reckoning_id),
                ),
                extra_headers=response_headers,
            )
        self._interfaces.record_channel_exchange(
            "web", message, note, session_id=web_session_id
        )
        start_response(
            "303 See Other",
            [
                ("Location", f"/decisions/{escape(outcome.id, quote=True)}"),
                ("Content-Length", "0"),
            ],
        )
        return [b""]

    def _handle_decision_request(
        self,
        action: tuple[str, str, str],
        environ: WSGIEnvironment,
        start_response: StartResponse,
        *,
        csrf_token: str,
        response_headers: tuple[tuple[str, str], ...],
    ) -> list[bytes]:
        action_name, reckoning_id, record_id = action
        if action_name == "view":
            try:
                reckoning = self._application.inspect_reckoning(reckoning_id)
                why = self._application.explain_reckoning(reckoning_id)
            except (KeyError, RuntimeError) as error:
                return self._html_response(
                    start_response,
                    "404 Not Found",
                    self._render_interface_area(
                        "simon", error=str(error), csrf_token=csrf_token
                    ),
                    extra_headers=response_headers,
                )
            return self._html_response(
                start_response,
                "200 OK",
                self._render_interface_area(
                    "decision",
                    csrf_token=csrf_token,
                    decision=reckoning,
                    why=why,
                ),
                extra_headers=response_headers,
            )
        fields = self._read_form(environ)
        operation_id = fields.get("operation_id", [""])[0]
        if action_name != "view" and not operation_id:
            return self._html_response(
                start_response,
                "400 Bad Request",
                self._render_interface_area(
                    "simon" if action_name == "create" else "decision",
                    error="Operation identity is required for this decision action.",
                    csrf_token=csrf_token,
                    pending_text=fields.get("situation", fields.get("message", [""]))[0]
                    if action_name == "create"
                    else "",
                    pending_record_id=record_id,
                    decision=self._safe_decision(reckoning_id),
                ),
                extra_headers=response_headers,
            )
        pending_text = ""
        try:
            if action_name == "create":
                pending_text = fields.get("situation", fields.get("message", [""]))[0]
                reckoning = self._application.start_reckoning(
                    pending_text, operation_id=operation_id or None
                )
                start_response(
                    "303 See Other",
                    [
                        (
                            "Location",
                            f"/decisions/{escape(reckoning.id, quote=True)}",
                        ),
                        ("Content-Length", "0"),
                    ],
                )
                return [b""]
            expected_revision = int(fields.get("expected_revision", [""])[0])
            if action_name == "correct_record":
                pending_text = fields.get("meaning", [""])[0]
                corrected = self._application.correct_personal_record(
                    reckoning_id,
                    record_id,
                    pending_text,
                    expected_revision=expected_revision,
                    operation_id=operation_id or None,
                )
                start_response(
                    "303 See Other",
                    [
                        (
                            "Location",
                            f"/decisions/{escape(corrected.id, quote=True)}",
                        ),
                        ("Content-Length", "0"),
                    ],
                )
                return [b""]
            if action_name == "confirm":
                confirmed = self._application.confirm_reckoning(
                    reckoning_id,
                    expected_revision=expected_revision,
                    operation_id=operation_id or None,
                )
                start_response(
                    "303 See Other",
                    [
                        (
                            "Location",
                            f"/decisions/{escape(confirmed.id, quote=True)}",
                        ),
                        ("Content-Length", "0"),
                    ],
                )
                return [b""]
        except ReckoningRevisionConflict as error:
            return self._html_response(
                start_response,
                "409 Conflict",
                self._render_interface_area(
                    "decision",
                    error=str(error),
                    csrf_token=csrf_token,
                    pending_text=pending_text,
                    pending_record_id=record_id,
                    decision=self._safe_decision(reckoning_id),
                ),
                extra_headers=response_headers,
            )
        except (
            ReckoningOperationConflict,
            ReckoningProviderError,
            KeyError,
            ValueError,
            RuntimeError,
        ) as error:
            error_area = "simon" if action_name == "create" else "decision"
            return self._html_response(
                start_response,
                "400 Bad Request",
                self._render_interface_area(
                    error_area,
                    error=str(error),
                    csrf_token=csrf_token,
                    pending_text=pending_text,
                    pending_record_id=record_id,
                    decision=self._safe_decision(reckoning_id),
                ),
                extra_headers=response_headers,
            )
        return self._html_response(
            start_response,
            "404 Not Found",
            self._render_interface_area(
                "simon", error="Unknown decision action.", csrf_token=csrf_token
            ),
            extra_headers=response_headers,
        )

    def _completed_operation_for(self, operation_id: str, reckoning_id: str) -> bool:
        """True when operation_id completed a mutation on this reckoning."""
        if not operation_id:
            return False
        inspect_operation = getattr(self._application, "inspect_operation", None)
        if not callable(inspect_operation):
            return False
        record = inspect_operation(operation_id)
        return (
            record is not None
            and record.status == "completed"
            and record.result_id == reckoning_id
        )

    def _safe_decision(self, reckoning_id: str) -> Reckoning | None:
        if not reckoning_id:
            return None
        try:
            return self._application.inspect_reckoning(reckoning_id)
        except (KeyError, RuntimeError):
            return None

    def _browser_session(
        self, environ: WSGIEnvironment, *, create: bool
    ) -> tuple[str, str | None]:
        session_id = ""
        raw_cookie = str(environ.get("HTTP_COOKIE", ""))
        if raw_cookie:
            cookies = SimpleCookie()
            try:
                cookies.load(raw_cookie)
            except Exception:  # An invalid cookie is an absent browser session.
                cookies = SimpleCookie()
            morsel = cookies.get("reckoning_session")
            session_id = morsel.value if morsel is not None else ""
        csrf_token = self._browser_sessions.get(session_id, "")
        if csrf_token or not create:
            return csrf_token, None
        session_id = token_urlsafe(24)
        csrf_token = token_urlsafe(32)
        self._browser_sessions[session_id] = csrf_token
        cookie = (
            f"reckoning_session={session_id}; Path=/; HttpOnly; "
            "SameSite=Strict"
        )
        return csrf_token, cookie

    def _mutation_rejection(self, environ: WSGIEnvironment) -> str | None:
        host = str(environ.get("HTTP_HOST", "")).strip().casefold()
        if not host:
            server_name = str(environ.get("SERVER_NAME", "")).strip()
            server_port = str(environ.get("SERVER_PORT", "")).strip()
            host = f"{server_name}:{server_port}".casefold()
        if host not in self._allowed_hosts:
            return "untrusted host"

        origin = str(environ.get("HTTP_ORIGIN", "")).strip()
        if origin:
            try:
                normalized_origin = validate_allowed_origin(origin)
            except ValueError:
                return "invalid origin"
            if normalized_origin not in self._allowed_origins:
                return "untrusted origin"

        fetch_site = str(environ.get("HTTP_SEC_FETCH_SITE", "")).strip().casefold()
        if fetch_site == "cross-site":
            return "cross-site request"

        session_token, _ = self._browser_session(environ, create=False)
        if not session_token:
            return "missing or expired browser session"
        try:
            submitted_token = self._read_form_field(environ, "_csrf_token")
        except (UnicodeDecodeError, ValueError):
            return "invalid form"
        if not submitted_token or not compare_digest(session_token, submitted_token):
            return "invalid session token"
        return None

    @staticmethod
    def _profile_action(method: str, path: str) -> tuple[str, str] | None:
        if method != "POST":
            return None
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "profile":
            return None
        record_id, action = parts[1], parts[2]
        if not record_id or action not in {"correct", "confirm", "reject"}:
            return None
        return record_id, action

    @staticmethod
    def _decision_action(method: str, path: str) -> tuple[str, str, str] | None:
        parts = path.strip("/").split("/")
        if len(parts) == 1 and parts[0] == "decisions" and method == "POST":
            return ("create", "", "")
        if len(parts) == 2 and parts[0] == "decisions" and method == "GET":
            return ("view", parts[1], "")
        if len(parts) == 3 and parts[0] == "decisions" and method == "POST":
            reckoning_id, action = parts[1], parts[2]
            if action == "confirm":
                return ("confirm", reckoning_id, "")
        if (
            len(parts) == 5
            and parts[0] == "decisions"
            and parts[2] == "records"
            and method == "POST"
        ):
            return ("correct_record", parts[1], parts[3])
        return None

    @staticmethod
    def _read_message(environ: WSGIEnvironment) -> str:
        fields = ReckoningWebApplication._read_form(environ)
        return fields.get("message", [""])[0]

    @staticmethod
    def _read_form_field(environ: WSGIEnvironment, name: str) -> str:
        fields = ReckoningWebApplication._read_form(environ)
        return fields.get(name, [""])[0]

    @staticmethod
    def _read_form(environ: WSGIEnvironment) -> dict[str, list[str]]:
        cached = environ.get("reckoning.form_fields")
        if isinstance(cached, dict):
            return cached
        content_length = int(str(environ.get("CONTENT_LENGTH") or "0"))
        if content_length > 65_536:
            raise ValueError("The form is too long.")
        input_stream = environ.get("wsgi.input")
        if input_stream is None or not hasattr(input_stream, "read"):
            raise ValueError("The request body is missing.")
        body = input_stream.read(content_length).decode("utf-8")
        fields = parse_qs(body, keep_blank_values=True)
        environ["reckoning.form_fields"] = fields
        return fields

    @staticmethod
    def _html_response(
        start_response: StartResponse,
        status: str,
        page: str,
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> list[bytes]:
        body = page.encode("utf-8")
        start_response(
            status,
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                *extra_headers,
            ],
        )
        return [body]

    @staticmethod
    def _render_session(
        messages: tuple[Message, ...],
        error: str | None = None,
        *,
        csrf_token: str = "",
    ) -> str:
        conversation = "".join(
            (
                '<article class="message">'
                f'<strong>{"You" if message.role == "user" else "Simon"}</strong>'
                f"<p>{escape(message.content)}</p>"
                "</article>"
            )
            for message in messages
        )
        if not conversation:
            conversation = '<p class="empty">Send Simon the first message.</p>'

        error_markup = f'<p class="error">{escape(error)}</p>' if error else ""
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Simon | Reckoning</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-monospace, monospace; }}
    body {{ background: #11131a; color: #e9edf5; margin: 0; }}
    main {{ margin: 10vh auto; max-width: 42rem; padding: 0 1.25rem; }}
    .panel {{ background: #191d28; border: 1px solid #353c4e; padding: 1.5rem; }}
    h1 {{ margin-top: 0; }}
    .message {{ border-top: 1px solid #353c4e; padding-top: 1rem; }}
    .message p {{ line-height: 1.6; white-space: pre-wrap; }}
    .empty {{ color: #aeb7c8; }}
    .error {{ color: #ff9d9d; }}
    label, textarea {{ display: block; width: 100%; }}
    textarea {{ background: #10121a; border: 1px solid #4c566f; box-sizing: border-box;
      color: inherit; font: inherit; margin: .5rem 0 1rem; min-height: 7rem; padding: .75rem; }}
    button {{ background: #d6ff5f; border: 0; color: #12150b; cursor: pointer;
      font: inherit; font-weight: 700; padding: .7rem 1rem; }}
  </style>
</head>
<body>
  <main>
    <section class="panel" aria-labelledby="simon-heading">
      <h1 id="simon-heading">Simon</h1>
      <div aria-live="polite">{conversation}</div>
      {error_markup}
      <form action="/messages" method="post">
        <input type="hidden" name="_csrf_token"
          value="{escape(csrf_token, quote=True)}">
        <label for="message">Your message</label>
        <textarea id="message" name="message" required autofocus></textarea>
        <button type="submit">Send</button>
      </form>
    </section>
  </main>
</body>
</html>"""

    def _render_interface_area(
        self,
        area: str,
        error: str | None = None,
        *,
        csrf_token: str = "",
        pending_text: str = "",
        pending_record_id: str = "",
        session_id: str | None = None,
        decision: Reckoning | None = None,
        why: WhyView | None = None,
    ) -> str:
        assert self._interfaces is not None
        visual_state = self._interfaces.visual_state()
        status_label, status_modifier = self._status_pair(visual_state)
        if area == "home":
            home = self._interfaces.home()
            content = self._render_home(home)
            inspector = self._render_inspector(
                status_label, status_modifier, visual_state=visual_state
            )
        elif area == "control":
            content = self._render_control_area(
                self._interfaces.control(), csrf_token, error=error
            )
            inspector = self._render_inspector(
                status_label, status_modifier, visual_state=visual_state
            )
        elif area == "simon":
            content = self._render_simon(
                csrf_token,
                error=error,
                pending_text=pending_text,
                session_id=session_id,
            )
            inspector = self._render_inspector(
                status_label, status_modifier, visual_state=visual_state
            )
        elif area == "decision":
            content = render_decision_content(
                decision,
                csrf_token,
                error=error,
                pending_meanings=(
                    {pending_record_id: pending_text} if pending_record_id else None
                ),
            )
            inspector = self._render_inspector(
                status_label,
                status_modifier,
                visual_state=visual_state,
                decision=decision,
                why=why,
            )
        elif area == "plan":
            content = (
                '<header><p class="eyebrow">Choose the work</p><h1>Plan</h1>'
                "</header><p>Shape direction, goals, and feasible commitments.</p>"
            )
            inspector = self._render_inspector(
                status_label, status_modifier, visual_state=visual_state
            )
        else:
            content = (
                '<header><p class="eyebrow">Learn from outcomes</p><h1>Review</h1>'
                "</header><p>Compare intentions with what happened and decide what "
                "changes.</p>"
            )
            inspector = self._render_inspector(
                status_label, status_modifier, visual_state=visual_state
            )

        task_areas = (
            ("home", "Home", "See your current situation"),
            ("simon", "Simon", "Think with Simon"),
            ("plan", "Plan", "Choose the work"),
            ("review", "Review", "Learn from outcomes"),
            ("control", "Control", "Inspect operations"),
        )
        navigation = "".join(
            f'<a href="/{"" if slug == "home" else slug}"'
            f'{" aria-current=\"page\"" if slug == area else ""}'
            f' data-task-area="{slug}"><strong>{label}</strong>'
            f'<span>{description}</span></a>'
            for slug, label, description in task_areas
        )
        return self._render_shell(
            area=area,
            navigation=navigation,
            content=content,
            inspector=inspector,
            visual_state=visual_state,
            status_label=status_label,
        )

    def _render_shell(
        self,
        *,
        area: str,
        navigation: str,
        content: str,
        inspector: str,
        visual_state: str,
        status_label: str,
    ) -> str:
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(area.title())} | Reckoning</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #050605;
      --panel: #0a110a;
      --panel-2: #111811;
      --border: #1f2e1f;
      --text: #e9f0e9;
      --text-2: #9fb39f;
      --green: #a6ff4d;
      --green-dim: #6bbd1e;
      --amber: #ffcc4d;
      --gray: #8a9a8a;
      --red: #ff7a7a;
      --focus: #a6ff4d;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      background: var(--bg);
      color: var(--text);
      margin: 0;
      min-height: 100vh;
    }}
    body {{
      font-size: 1rem;
      line-height: 1.55;
    }}
    .shell {{
      display: grid;
      grid-template-columns: 14rem minmax(0, 1fr) 18rem;
      min-height: 100vh;
    }}
    nav {{
      background: var(--panel);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: .35rem;
      padding: 1.25rem .875rem;
    }}
    nav a {{
      border-radius: .35rem;
      color: var(--text-2);
      display: flex;
      flex-direction: column;
      justify-content: center;
      min-height: 44px;
      padding: .55rem .75rem;
      text-decoration: none;
    }}
    nav a span {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .65rem;
      letter-spacing: .08em;
      line-height: 1.25;
      text-transform: uppercase;
    }}
    nav a[aria-current] {{
      background: var(--panel-2);
      border-left: 3px solid var(--green);
      color: var(--text);
    }}
    nav a:focus-visible {{
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }}
    main {{
      display: flex;
      flex-direction: column;
      max-width: 64rem;
      padding: 2.5rem clamp(1rem, 4vw, 4rem) 6rem;
      width: 100%;
    }}
    aside {{
      background: var(--panel);
      border-left: 1px solid var(--border);
      padding: 1.25rem;
    }}
    .eyebrow {{
      color: var(--text-2);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .7rem;
      letter-spacing: .12em;
      margin: 0 0 .4rem;
      text-transform: uppercase;
    }}
    h1 {{
      font-size: clamp(1.8rem, 4.5vw, 3.2rem);
      font-weight: 700;
      line-height: 1.1;
      margin: 0 0 1.5rem;
    }}
    h2 {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .75rem;
      font-weight: 700;
      letter-spacing: .1em;
      margin: 0 0 .75rem;
      text-transform: uppercase;
    }}
    .card, .panel, details, .control-section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: .4rem;
      margin-bottom: .75rem;
      padding: 1rem;
    }}
    .card:focus-within, .panel:focus-within {{
      border-color: var(--green-dim);
    }}
    summary {{
      cursor: pointer;
      font-weight: 600;
    }}
    .composer {{
      background: var(--panel);
      border-top: 1px solid var(--border);
      bottom: 0;
      left: 14rem;
      padding: .75rem clamp(1rem, 4vw, 4rem);
      position: fixed;
      right: 18rem;
    }}
    .composer form {{
      display: flex;
      gap: .5rem;
    }}
    .composer label {{
      position: absolute;
      left: -9999px;
    }}
    .composer textarea {{
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: .35rem;
      color: inherit;
      flex: 1 1 auto;
      font: inherit;
      min-height: 3.2rem;
      padding: .65rem .85rem;
      resize: vertical;
    }}
    .composer .actions {{
      display: flex;
      flex-direction: column;
      gap: .4rem;
    }}
    button, .button {{
      background: var(--green);
      border: 0;
      border-radius: .35rem;
      color: #081008;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
      min-height: 44px;
      padding: .55rem 1rem;
    }}
    button.secondary {{
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-2);
    }}
    button:focus-visible, .button:focus-visible, textarea:focus-visible {{
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }}
    .error {{
      background: rgba(255, 122, 122, .1);
      border: 1px solid var(--red);
      border-radius: .35rem;
      color: var(--red);
      padding: .75rem 1rem;
    }}
    .empty {{ color: var(--text-2); }}
    .message {{
      border-top: 1px solid var(--border);
      padding: .9rem 0;
    }}
    .message strong {{
      color: var(--green);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .75rem;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .message p {{
      margin: .35rem 0 0;
      white-space: pre-wrap;
      word-wrap: break-word;
    }}
    .status {{
      align-items: center;
      display: flex;
      gap: .5rem;
    }}
    .status::before {{
      border-radius: 50%;
      content: "";
      display: inline-block;
      height: .55rem;
      width: .55rem;
    }}
    .status-ready::before {{ background: var(--green); }}
    .status-reasoning::before {{ background: var(--amber); }}
    .status-limited::before {{ background: var(--amber); }}
    .status-failed::before {{ background: var(--red); }}
    .status-proposed::before {{ background: var(--amber); }}
    .status-corrected::before {{ background: var(--amber); }}
    .status-confirmed::before {{ background: var(--green); }}
    .status-unknown::before {{ background: var(--gray); }}
    .inspector-toggle {{ display: none; }}
    .inspector-panel details {{ margin-bottom: .5rem; }}
    .inspector-panel ul {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .inspector-panel li {{
      border-top: 1px solid var(--border);
      padding: .5rem 0;
    }}
    .inspector-panel li:first-child {{ border-top: 0; }}
    .simon-core {{
      align-items: center;
      display: grid;
      gap: .75rem;
      justify-items: center;
    }}
    .core-glyph {{
      background: var(--panel-2);
      border: 4px double var(--text-2);
      border-radius: 38%;
      box-shadow: inset 0 0 0 6px var(--bg);
      display: grid;
      height: 6rem;
      place-items: center;
      position: relative;
      width: 6rem;
    }}
    .core-glyph::before {{
      border: 2px solid var(--green);
      border-radius: 50%;
      content: "";
      height: 1.8rem;
      width: 1.8rem;
    }}
    [data-simon-state="warning"] .core-glyph,
    [data-simon-state="degraded"] .core-glyph {{ border-color: var(--red); }}
    [data-simon-state="approval"] .core-glyph {{ border-color: var(--amber); }}
    [data-simon-state="execution"] .core-glyph {{ border-color: #70d6ff; }}
    [data-simon-state="listening"] .core-glyph {{ border-color: #7bdcb5; }}
    [data-simon-state="reasoning"] .core-glyph {{ border-color: #c4a7ff; }}
    .proposal-card .record {{
      border-top: 1px solid var(--border);
      padding: 1rem 0;
    }}
    .proposal-card form {{
      margin-top: .75rem;
    }}
    .next-action {{
      color: var(--text-2);
      font-size: .9rem;
      margin-top: .5rem;
    }}
    @media (max-width: 720px) {{
      .shell {{ grid-template-columns: 1fr; }}
      nav {{
        border-bottom: 1px solid var(--border);
        border-right: 0;
        flex-direction: row;
        overflow-x: auto;
        padding: .65rem;
      }}
      nav a {{ flex: 0 0 auto; min-height: 44px; white-space: nowrap; }}
      nav a span {{ display: none; }}
      main {{ padding: 1.25rem 1rem 7rem; }}
      aside {{
        border-left: 0;
        border-top: 1px solid var(--border);
        padding: 1rem;
      }}
      .composer {{
        left: 0;
        right: 0;
      }}
      .simon-core {{
        grid-template-columns: auto 1fr;
        justify-items: start;
      }}
      .core-glyph {{ height: 3.5rem; width: 3.5rem; }}
      .inspector-panel {{ display: none; }}
      .inspector-toggle {{ display: block; margin-bottom: .75rem; }}
      .inspector-toggle[aria-expanded="true"] + .inspector-panel {{ display: block; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      * {{ animation: none !important; transition: none !important; }}
    }}
  </style>
</head>
<body data-area="{escape(area)}" data-simon-state="{escape(visual_state)}">
  <div class="shell">
    <nav aria-label="Task areas">{navigation}</nav>
    <main>{content}</main>
    <aside>
      <figure class="simon-core" role="img" aria-label="Simon, original mechanical core">
        <span class="core-glyph" aria-hidden="true"></span>
        <figcaption>Simon · {escape(status_label)}</figcaption>
      </figure>
      {inspector}
    </aside>
  </div>
</body>
</html>"""

    @staticmethod
    def _status_pair(visual_state: str) -> tuple[str, str]:
        mapping = {
            "idle": ("ready", "status-ready"),
            "listening": ("reasoning", "status-reasoning"),
            "reasoning": ("reasoning", "status-reasoning"),
            "approval": ("proposed", "status-proposed"),
            "execution": ("reasoning", "status-reasoning"),
            "warning": ("failed", "status-failed"),
            "degraded": ("limited", "status-limited"),
        }
        return mapping.get(visual_state, ("ready", "status-ready"))

    def _render_home(self, home) -> str:
        return f"""
          <header><p class="eyebrow">Command center</p><h1>Home</h1></header>
          <section class="card" aria-labelledby="matters-now">
            <h2 id="matters-now">What matters now</h2>{render_list(home.matters_now)}
          </section>
          <section class="card" aria-labelledby="what-changed">
            <h2 id="what-changed">What changed</h2>{render_list(home.changes)}
          </section>
          <section class="card" aria-labelledby="needs-decision">
            <h2 id="needs-decision">Needs your decision</h2>{render_list(home.decisions)}
          </section>
          <section class="card" aria-labelledby="saved-decisions">
            <h2 id="saved-decisions">Decisions</h2>{self._decision_links()}
          </section>
          <section class="card" aria-labelledby="system-health">
            <h2 id="system-health">System health</h2>
            <p>{escape(home.health)}</p>
            <p>{escape(home.placement.notice)}</p>
          </section>
        """

    def _decision_links(self) -> str:
        list_reckonings = getattr(self._application, "list_reckonings", None)
        if not callable(list_reckonings):
            return '<p class="empty">None recorded.</p>'
        reckonings = list_reckonings()
        if not reckonings:
            return '<p class="empty">None recorded.</p>'
        return "<ul>" + "".join(
            '<li><a href="/decisions/'
            f'{escape(reckoning.id, quote=True)}">'
            f"{escape(reckoning.draft.conflict)}</a> · "
            f'<span class="status status-{escape(reckoning.status)}">'
            f"{escape(reckoning.status)}</span></li>"
            for reckoning in reckonings
        ) + "</ul>"

    def _render_control_area(
        self, control: ControlView, csrf_token: str, *, error: str | None
    ) -> str:
        error_markup = f'<p class="error">{escape(error)}</p>' if error else ""
        return (
            self._render_control(control)
            + error_markup
            + self._render_processing_scope(csrf_token)
        )

    def _displayed_web_session(
        self, preferred_id: str | None = None
    ) -> ChannelSession:
        assert self._interfaces is not None
        if preferred_id:
            sessions = self._interfaces.list_channel_sessions("web")
            match = next(
                (session for session in sessions if session.session_id == preferred_id),
                None,
            )
            if match is not None:
                return match
        return self._ensure_selected_web_session()

    def _ensure_selected_web_session(self) -> ChannelSession:
        assert self._interfaces is not None
        selected = self._interfaces.selected_channel_session("web")
        if selected is not None:
            return selected
        session = self._interfaces.create_channel_session(
            "web",
            display_name="First conversation",
            origin="first-use",
        )
        self._interfaces.select_channel_session(
            "web",
            session.session_id,
            expected_selection_revision=0,
        )
        return session

    def _selected_web_session_id(self) -> str:
        return self._ensure_selected_web_session().session_id

    def _session_preview(self, session: ChannelSession) -> str:
        messages = session.messages
        if not messages:
            return '<p class="empty">No messages yet.</p>'
        lines: list[str] = []
        for message in messages[-4:]:
            label = "You" if message.role == "user" else "Simon"
            lines.append(f"{label}: {message.content}")
        return escape("\n".join(lines))

    def _render_simon(
        self,
        csrf_token: str,
        *,
        error: str | None,
        pending_text: str,
        session_id: str | None = None,
    ) -> str:
        assert self._interfaces is not None
        displayed = self._displayed_web_session(session_id)
        selection = self._interfaces._repository.get_selected_session("web")
        selection_revision = selection.revision if selection is not None else 0
        revision = self._interfaces.session_revision("web", displayed.session_id)
        messages = self._interfaces.channel_session(
            "web", session_id=displayed.session_id
        )
        notices = self._interfaces.channel_notices(
            "web", session_id=displayed.session_id
        )
        conversation = "".join(
            '<article class="message">'
            f'<strong>{"You" if message.role == "user" else "Simon"}</strong>'
            f"{render_channel_text(message.content)}</article>"
            for message in messages
        ) or '<p class="empty">Send Simon the first message.</p>'
        notice_markup = ""
        if notices:
            notice_items = "".join(
                f'<li>{escape(notice)}</li>' for notice in notices
            )
            notice_markup = (
                '<section class="status-notices" aria-label="Status notices">'
                f'<ul>{notice_items}</ul></section>'
            )
        error_markup = f'<p class="error">{escape(error)}</p>' if error else ""
        profile_review = (
            self._render_profile_review(csrf_token)
            if any(message.role == "assistant" for message in messages)
            else ""
        )
        pending_cards = "".join(
            render_pending_input_card(operation, csrf_token)
            for operation in self._pending_decision_inputs()
        )
        active_proposal, target_fields = self._active_proposal_state(csrf_token)

        last_activity = (
            displayed.last_activity_at.isoformat()
            if displayed.last_activity_at is not None
            else "unknown"
        )
        session_header = (
            f'<p class="session-meta">{escape(displayed.display_name or "Conversation")} · '
            f'last activity {escape(last_activity)}</p>'
        )

        sessions = self._interfaces.list_channel_sessions("web")
        session_list_items = "".join(
            f"""<li>
            <form action="/sessions/select" method="post" class="inline">
              <input type="hidden" name="_csrf_token"
                value="{escape(csrf_token, quote=True)}">
              <input type="hidden" name="session_id"
                value="{escape(session.session_id, quote=True)}">
              <input type="hidden" name="selection_revision"
                value="{selection_revision}">
              <button type="submit" class="secondary">
                Resume {escape(session.display_name or "Conversation")}
              </button>
            </form>
            <span class="session-preview">{self._session_preview(session)}</span>
          </li>"""
            for session in sessions
            if session.session_id != displayed.session_id
        )
        session_list = (
            f'<section aria-labelledby="sessions-heading">'
            f'<h2 id="sessions-heading">Other conversations</h2>'
            f'<ul>{session_list_items}</ul></section>'
            if session_list_items
            else ""
        )
        new_session_form = f"""
          <section aria-labelledby="new-session-heading">
            <h2 id="new-session-heading">New conversation</h2>
            <form action="/sessions/new" method="post">
              <input type="hidden" name="_csrf_token"
                value="{escape(csrf_token, quote=True)}">
              <label for="display_name">Name (optional)</label>
              <input type="text" id="display_name" name="display_name">
              <button type="submit">Start</button>
            </form>
          </section>
        """
        return f"""
          <header><p class="eyebrow">Conversation</p><h1>Simon</h1></header>
          {session_header}
          {notice_markup}
          <div aria-live="polite">{conversation}</div>{error_markup}
          {active_proposal}
          {pending_cards}
          {profile_review}
          <div class="composer">
            <form action="/messages" method="post">
              <input type="hidden" name="_csrf_token"
                value="{escape(csrf_token, quote=True)}">
              <input type="hidden" name="operation_id"
                value="{escape(fresh_operation_id(), quote=True)}">
              <input type="hidden" name="session_id"
                value="{escape(displayed.session_id, quote=True)}">
              <input type="hidden" name="expected_revision"
                value="{revision}">
              {target_fields}
              <label for="message">Your message</label>
              <textarea id="message" name="message" required
                >{escape(pending_text)}</textarea>
              <div class="actions">
                <button type="submit">Send</button>
                <button type="submit" formaction="/decisions" formmethod="post"
                  class="secondary">Propose</button>
              </div>
            </form>
          </div>
          {session_list}
          {new_session_form}
        """

    def _pending_decision_inputs(self) -> tuple[OperationRecord, ...]:
        pending = getattr(self._application, "pending_decision_inputs", None)
        if not callable(pending):
            return ()
        return pending()

    def _active_proposal_state(self, csrf_token: str) -> tuple[str, str]:
        """Return (summary HTML, composer binding field) for one proposal.

        Rendering the summary issues a server-side presentation receipt; the
        composer carries only the opaque token.
        """
        list_reckonings = getattr(self._application, "list_reckonings", None)
        if not callable(list_reckonings):
            return "", ""
        proposed = tuple(
            reckoning
            for reckoning in list_reckonings()
            if reckoning.status == "proposed"
        )
        if len(proposed) != 1:
            return "", ""
        reckoning = proposed[0]
        summary = (
            '<section class="card" aria-labelledby="active-proposal">'
            '<h2 id="active-proposal">Waiting for your review</h2>'
            f'<p>Reply <code>confirm</code> to confirm <strong>'
            f'{escape(reckoning.draft.conflict)}</strong> (revision '
            f'{reckoning.version}) exactly as shown, or tell me the correction. '
            f'You can also <a href="/decisions/{escape(reckoning.id, quote=True)}">'
            "open it</a> and edit directly.</p></section>"
        )
        presentation_token = self._issue_presentation(csrf_token, reckoning)
        target_fields = (
            f'<input type="hidden" name="decision_presentation_token" '
            f'value="{escape(presentation_token, quote=True)}">'
        )
        return summary, target_fields


    def _render_inspector(
        self,
        status_label: str,
        status_modifier: str,
        *,
        visual_state: str,
        decision: Reckoning | None = None,
        why: WhyView | None = None,
    ) -> str:
        state_block = (
            f'<p class="status {escape(status_modifier)}">'
            f'{escape(status_label)}</p>'
        )
        decision_block = ""
        if decision is not None and why is not None:
            evidence_items = "".join(
                f"<li><strong>{escape(item.source)}</strong> · {escape(item.content)}</li>"
                for item in why.evidence
            ) or '<li class="empty">No evidence recorded.</li>'
            version_items = "".join(
                f"<li>{escape(record.record_type)} v{record.version} · "
                f"<span class=\"status status-{record.status}\">{escape(record.status)}</span> · "
                f"{escape(record.meaning)}</li>"
                for record in decision.record_versions
            ) or '<li class="empty">No versions recorded.</li>'
            decision_block = f"""
              <details open>
                <summary>Evidence</summary>
                <ul>{evidence_items}</ul>
              </details>
              <details open>
                <summary>Versions</summary>
                <ul>{version_items}</ul>
              </details>
              <details open>
                <summary>Status</summary>
                <p class="status status-{escape(decision.status)}">{escape(decision.status)}</p>
                <p>Revision {decision.version}</p>
              </details>
            """
        return f"""
          <button type="button" class="inspector-toggle secondary" aria-expanded="false"
            aria-controls="inspector-panel" onclick="var p=document.getElementById('inspector-panel');var t=this;var shown=window.getComputedStyle(p).display!=='none';p.style.display=shown?'none':'block';t.setAttribute('aria-expanded',String(!shown));t.focus();">
            Inspector
          </button>
          <div class="inspector-panel" id="inspector-panel">
            <h2 class="eyebrow">Inspector</h2>
            {state_block}
            {decision_block}
          </div>
        """

    def _render_profile_review(self, csrf_token: str) -> str:
        profile_proposals = getattr(self._application, "profile_proposals", None)
        if not callable(profile_proposals):
            return ""
        proposals = profile_proposals()
        if not proposals:
            return ""
        cards = "".join(
            f"""
            <article class="control-section"
              data-profile-proposal="{escape(item.record_id)}">
              <p><strong>Unconfirmed profile statement</strong></p>
              <p>Source: {escape(item.source)}</p>
              <form action="/profile/{escape(item.record_id)}/correct" method="post">
                <input type="hidden" name="_csrf_token"
                  value="{escape(csrf_token, quote=True)}">
                <label for="meaning-{escape(item.record_id)}">Meaning</label>
                <textarea id="meaning-{escape(item.record_id)}" name="meaning"
                  required>{escape(item.original_text)}</textarea>
                <button type="submit">Save correction</button>
              </form>
              <form action="/profile/{escape(item.record_id)}/confirm" method="post">
                <input type="hidden" name="_csrf_token"
                  value="{escape(csrf_token, quote=True)}">
                <button type="submit">Confirm</button>
              </form>
              <form action="/profile/{escape(item.record_id)}/reject" method="post">
                <input type="hidden" name="_csrf_token"
                  value="{escape(csrf_token, quote=True)}">
                <button type="submit">Reject</button>
              </form>
              <a href="/">Skip for now</a>
            </article>
            """
            for item in proposals
        )
        return (
            '<section aria-labelledby="profile-review-heading">'
            '<h2 id="profile-review-heading">Review your profile</h2>'
            "<p>Correct, confirm, reject, or leave a statement for later. "
            "Nothing becomes confirmed personal context without your choice.</p>"
            f"{cards}</section>"
        )

    @classmethod
    def _render_control(cls, control: ControlView) -> str:
        runs = tuple(
            f"{item.id}: {item.summary} ({item.status})"
            for item in reversed(control.receipts)
        )
        sections = (
            ("Runs", runs),
            ("Evidence", control.evidence),
            ("Memory", control.memory),
            ("Permissions", control.permissions),
            ("Approvals", control.approvals),
            ("Routine health", control.routine_health),
            ("Connector health", control.connector_health),
            ("Suppressed findings", control.suppressed_findings),
            ("Tools", control.tools),
            ("Actions", control.actions),
        )
        details = "".join(
            f'<section class="control-section"><h2>{escape(title)}</h2>{render_list(items)}</section>'
            for title, items in sections
        )
        failures = render_list(tuple(item.summary for item in control.failures))
        return f"""
          <header><p class="eyebrow">Operational truth</p><h1>Control</h1></header>
          <section class="control-section"><h2>Costs</h2>
            <p>{control.cost_units} billable units</p></section>
          {details}
          <section id="failure-log" class="control-section"><h2>Failure log</h2>{failures}</section>
        """

    def _render_processing_scope(self, csrf_token: str) -> str:
        inspect_scope = getattr(self._application, "processing_scope_status", None)
        if not callable(inspect_scope):
            return ""
        status = inspect_scope()
        choices = "".join(
            f'<label><input type="checkbox" name="category" value="{escape(category)}"'
            f'{" checked" if category in status.allowed_categories else ""}>'
            f" {escape(category)}</label>"
            for category in PROCESSING_CATEGORIES
        )
        explicit = "explicit grant" if status.explicit else "default local policy"
        return f"""
          <section id="processing-scope" class="control-section">
            <h2>Model processing scope</h2>
            <p>Storage: {escape(status.storage_location)}. Executing node:
              {escape(status.executing_node)}. Provider destination:
              {escape(status.destination.id)} ({escape(status.destination.kind)}).</p>
            <p>Grant revision {status.grant_version}; {explicit}.</p>
            <form action="/processing-scope" method="post">
              <input type="hidden" name="_csrf_token"
                value="{escape(csrf_token, quote=True)}">
              <input type="hidden" name="expected_revision"
                value="{status.grant_version}">
              <fieldset><legend>Context allowed at this exact destination</legend>
                {choices}</fieldset>
              <button type="submit">Save processing scope</button>
            </form>
          </section>
        """



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Reckoning's local web interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--provider", choices=CREDENTIAL_PROVIDER_NAMES)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help=(
            "Browser origin allowed to submit mutations. Repeat for explicit "
            "private access; forwarded headers are not trusted automatically."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "reckoning",
    )
    parser.add_argument(
        "--server-data-dir",
        type=Path,
        help="Configured personal-server storage root for server or hybrid placement.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    arguments = parser.parse_args(argv)

    try:
        host = validate_bind_host(arguments.host)
        runtime = load_installation_runtime(
            arguments.data_dir,
            server_data_dir=arguments.server_data_dir,
        )
        provider = RuntimeProviderSettings.load(
            arguments.data_dir,
            credentials_path=DEFAULT_PROVIDER_CREDENTIALS,
            provider_name=arguments.provider,
            model=arguments.model,
            base_url=arguments.base_url,
        )
        configured_origins = tuple(arguments.allowed_origin) or local_browser_origins(
            arguments.port
        )
        allowed_origins = tuple(
            validate_allowed_origin(origin) for origin in configured_origins
        )
    except (OperationError, ValueError) as error:
        parser.error(str(error))

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
    web = ReckoningWebApplication(
        application,
        interface_application=create_local_interface_application(
            application,
            runtime.state_path("confirmed-state", "interfaces.json"),
            placement=runtime.interface_placement,
            connector_data_dir=runtime.root_for("approved-remote-sources"),
        ),
        allowed_origins=allowed_origins,
    )
    with make_server(host, arguments.port, web) as server:
        print(f"Simon is available at http://{host}:{arguments.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nReckoning stopped.")


if __name__ == "__main__":
    main()
