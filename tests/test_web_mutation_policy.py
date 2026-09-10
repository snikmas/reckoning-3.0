from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from typing import Callable, Iterable
from urllib.parse import urlencode, urlsplit

import pytest

from reckoning.interfaces import (
    ChannelRequest,
    InMemoryInterfaceRepository,
    PlacementPolicy,
    ReckoningInterfaceApplication,
)
from reckoning.processing import ProcessingDestination, ProcessingScopeStatus
from reckoning.web import ReckoningWebApplication, validate_allowed_origin


class RecordingResponder:
    def __init__(self, response: str = "Persisted Simon reply") -> None:
        self.response = response
        self.requests: list[ChannelRequest] = []

    def respond(self, request: ChannelRequest) -> str:
        self.requests.append(request)
        if self.response == "raise":
            raise RuntimeError("Provider unavailable")
        return self.response


def test_allowed_origins_are_exact_and_canonical() -> None:
    assert validate_allowed_origin("HTTPS://Example.COM:443/") == (
        "https://example.com"
    )
    assert validate_allowed_origin("http://[::1]:8000") == "http://[::1]:8000"
    for invalid in (
        "https://user@example.com",
        "https://example.com/path",
        "https://example.com?query=yes",
        "not-an-origin",
    ):
        with pytest.raises(ValueError, match="invalid allowed browser origin"):
            validate_allowed_origin(invalid)


class RecordingCore:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.processing_version = 0
        self.processing_categories: tuple[str, ...] = ()

    def open_session(self) -> tuple[object, ...]:
        return ()

    def profile_proposals(self) -> tuple[object, ...]:
        return ()

    def correct_profile_proposal(
        self, record_id: str, meaning: str, *, language: str
    ) -> None:
        self.actions.append(("correct", f"{record_id}:{meaning}:{language}"))

    def confirm_profile_proposal(self, record_id: str) -> None:
        self.actions.append(("confirm", record_id))

    def reject_profile_proposal(self, record_id: str) -> None:
        self.actions.append(("reject", record_id))

    def processing_scope_status(self) -> ProcessingScopeStatus:
        return ProcessingScopeStatus(
            ProcessingDestination(
                "deepseek", "https://api.deepseek.com", "cloud"
            ),
            self.processing_version,
            self.processing_categories,
            ("current-request",),
            self.processing_version > 0,
            "local",
            "server",
        )

    def change_processing_scope(
        self, categories: tuple[str, ...], *, expected_revision: int
    ) -> ProcessingScopeStatus:
        if expected_revision != self.processing_version:
            raise RuntimeError("stale processing grant")
        self.processing_version += 1
        self.processing_categories = categories
        self.actions.append(("processing", ",".join(categories)))
        return self.processing_scope_status()


def build_web(
    *,
    origin: str = "http://127.0.0.1:8000",
    response: str = "Persisted Simon reply",
) -> tuple[ReckoningWebApplication, ReckoningInterfaceApplication, RecordingResponder, RecordingCore]:
    responder = RecordingResponder(response)
    interface = ReckoningInterfaceApplication(
        repository=InMemoryInterfaceRepository(),
        responder=responder,
        placement=PlacementPolicy(
            profile="local",
            categories=(),
            local_node_available=True,
            server_node_available=False,
        ),
    )
    core = RecordingCore()
    web = ReckoningWebApplication(
        core,  # type: ignore[arg-type]
        interface_application=interface,
        allowed_origins=(origin,),
    )
    return web, interface, responder, core


def raw_request(
    application: Callable,
    method: str,
    path: str,
    *,
    form: dict[str, str] | None = None,
    origin: str = "http://127.0.0.1:8000",
    cookie: str = "",
    environ_overrides: dict[str, object] | None = None,
) -> tuple[str, list[tuple[str, str]], bytes]:
    body = urlencode(form or {}).encode("utf-8")
    captured_status = ""
    captured_headers: list[tuple[str, str]] = []
    parsed_origin = urlsplit(origin)

    def start_response(
        status: str,
        headers: list[tuple[str, str]],
        exc_info: object | None = None,
    ) -> None:
        del exc_info
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": BytesIO(body),
        "wsgi.url_scheme": parsed_origin.scheme,
        "SERVER_NAME": parsed_origin.hostname or "127.0.0.1",
        "SERVER_PORT": str(parsed_origin.port or 80),
        "HTTP_HOST": parsed_origin.netloc,
        "REMOTE_ADDR": "127.0.0.1",
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    environ.update(environ_overrides or {})
    response: Iterable[bytes] = application(environ, start_response)
    return captured_status, captured_headers, b"".join(response)


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
        status, header_pairs, body = raw_request(
            self.web, "GET", path, origin=self.origin, cookie=self.cookie
        )
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
        include_origin: bool = True,
        include_fetch_metadata: bool = True,
    ) -> tuple[str, dict[str, str], bytes]:
        fields = {"_csrf_token": self.csrf_token, **(form or {})}
        overrides = {
            "HTTP_ORIGIN": self.origin,
            "HTTP_SEC_FETCH_SITE": "same-origin",
            **(environ_overrides or {}),
        }
        if not include_origin:
            overrides.pop("HTTP_ORIGIN", None)
        if not include_fetch_metadata:
            overrides.pop("HTTP_SEC_FETCH_SITE", None)
        status, header_pairs, body = raw_request(
            self.web,
            "POST",
            path,
            form=fields,
            origin=self.origin,
            cookie=self.cookie,
            environ_overrides=overrides,
        )
        return status, dict(header_pairs), body


@pytest.mark.parametrize(
    ("overrides", "token"),
    (
        ({"HTTP_HOST": "foreign.invalid:8000"}, "valid"),
        ({"HTTP_ORIGIN": "https://foreign.invalid"}, "valid"),
        ({"HTTP_SEC_FETCH_SITE": "cross-site"}, "valid"),
        ({"HTTP_X_FORWARDED_HOST": "127.0.0.1:8000", "HTTP_HOST": "foreign.invalid"}, "valid"),
        ({}, "missing"),
        ({}, "invalid"),
    ),
    ids=("foreign-host", "foreign-origin", "cross-site", "forwarded-host", "missing-token", "invalid-token"),
)
def test_message_mutation_rejects_untrusted_browser_requests_before_dispatch(
    overrides: dict[str, object], token: str
) -> None:
    web, interface, responder, core = build_web()
    browser = BrowserSession(web)
    browser.get("/simon")
    if token == "missing":
        browser.csrf_token = ""
    elif token == "invalid":
        browser.csrf_token = "wrong-token"

    status, _, page = browser.post(
        "/messages", {"message": "Do not process this"}, environ_overrides=overrides
    )

    assert status == "403 Forbidden"
    assert b"request was rejected" in page
    assert interface.channel_session("web") == ()
    assert responder.requests == []
    assert core.actions == []


@pytest.mark.parametrize(
    ("origin", "include_origin", "include_fetch_metadata"),
    (
        ("http://localhost:8123", True, True),
        ("http://127.0.0.1:8123", False, False),
        ("http://[::1]:8123", True, True),
        ("https://reckoning.private.example", True, True),
    ),
)
def test_configured_local_and_private_origins_can_submit_with_a_session_token(
    origin: str, include_origin: bool, include_fetch_metadata: bool
) -> None:
    web, interface, responder, _ = build_web(origin=origin)
    browser = BrowserSession(web, origin=origin)
    browser.get("/simon")

    status, headers, _ = browser.post(
        "/messages",
        {"message": "Process this"},
        include_origin=include_origin,
        include_fetch_metadata=include_fetch_metadata,
    )

    assert status == "303 See Other"
    assert headers["Location"] == "/simon"
    assert [item.content for item in interface.channel_session("web")] == [
        "Process this",
        "Persisted Simon reply",
    ]
    assert len(responder.requests) == 1


@pytest.mark.parametrize(
    ("path", "form", "expected_action"),
    (
        ("/profile/profile-1/correct", {"meaning": "Corrected"}, "correct"),
        ("/profile/profile-1/confirm", {}, "confirm"),
        ("/profile/profile-1/reject", {}, "reject"),
    ),
)
def test_every_profile_mutation_uses_the_same_session_policy(
    path: str, form: dict[str, str], expected_action: str
) -> None:
    web, _, responder, core = build_web()
    browser = BrowserSession(web)
    browser.get("/simon")

    browser.csrf_token = "wrong-token"
    denied, _, _ = browser.post(path, form)
    assert denied == "403 Forbidden"
    assert core.actions == []
    assert responder.requests == []

    browser.get("/simon")
    accepted, headers, _ = browser.post(path, form)
    assert accepted == "303 See Other"
    assert headers["Location"] == "/simon"
    assert core.actions[0][0] == expected_action


def test_control_exposes_and_protects_the_processing_scope_choice() -> None:
    web, _, responder, core = build_web()
    browser = BrowserSession(web)
    status, _, page = browser.get("/control")

    assert status == "200 OK"
    assert b"Model processing scope" in page
    assert b"deepseek@https://api.deepseek.com" in page
    assert b"Storage: local" in page
    assert b"Executing node:" in page

    browser.csrf_token = "wrong-token"
    denied, _, _ = browser.post(
        "/processing-scope",
        {"expected_revision": "0", "category": "current-request"},
    )
    assert denied == "403 Forbidden"
    assert core.actions == []
    assert responder.requests == []

    browser.get("/control")
    accepted, headers, _ = browser.post(
        "/processing-scope",
        {"expected_revision": "0", "category": "current-request"},
    )
    assert accepted == "303 See Other"
    assert headers["Location"] == "/control"
    assert core.actions == [("processing", "current-request")]


def test_session_from_a_restarted_server_expires_cleanly() -> None:
    first_web, _, _, _ = build_web()
    browser = BrowserSession(first_web)
    browser.get("/simon")
    second_web, second_interface, second_responder, _ = build_web()
    browser.web = second_web

    status, _, _ = browser.post("/messages", {"message": "Stale session"})

    assert status == "403 Forbidden"
    assert second_interface.channel_session("web") == ()
    assert second_responder.requests == []


def test_successful_submission_redirects_to_the_visible_saved_simon_reply() -> None:
    web, _, _, _ = build_web()
    browser = BrowserSession(web)
    first_status, _, first_page = browser.get("/")
    assert first_status == "200 OK"
    assert b'data-area="simon"' in first_page

    status, headers, _ = browser.post(
        "/messages", {"message": "Show me the answer"}
    )
    assert status == "303 See Other"
    assert headers["Location"] == "/simon"

    final_status, _, final_page = browser.get(headers["Location"])
    assert final_status == "200 OK"
    assert b"Show me the answer" in final_page
    assert b"Persisted Simon reply" in final_page


def test_submission_failure_stays_in_simon_and_never_shows_a_success_reply() -> None:
    web, interface, _, _ = build_web(response="raise")
    browser = BrowserSession(web)
    browser.get("/simon")

    status, _, page = browser.post("/messages", {"message": "Try this"})

    assert status == "400 Bad Request"
    assert b'data-area="simon"' in page
    assert b"Provider unavailable" in page
    assert b"Persisted Simon reply" not in page
    assert interface.channel_session("web") == ()
