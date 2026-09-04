from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from reckoning.providers import (
    ProviderKeyVerificationError,
    verify_provider_api_key,
)

def test_verify_provider_api_key_accepts_a_key_the_provider_recognizes() -> None:
    requests: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        requests.append(request)
        return b'{"data": []}'

    verify_provider_api_key("deepseek", "sk-good", transport=transport)

    (request,) = requests
    assert request.full_url == "https://api.deepseek.com/models"
    assert request.headers["Authorization"] == "Bearer sk-good"
    assert request.get_method() == "GET"


def test_verify_provider_api_key_explains_a_rejected_key() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(
        ProviderKeyVerificationError,
        match="OrcaRouter rejected the API key",
    ):
        verify_provider_api_key("orcarouter", "bad-key", transport=transport)


def test_verify_provider_api_key_explains_other_failures() -> None:
    def failing_http(request: Request, timeout: float) -> bytes:
        raise HTTPError(request.full_url, 500, "Server Error", {}, None)

    with pytest.raises(
        ProviderKeyVerificationError,
        match="DeepSeek returned HTTP 500 during API-key verification",
    ):
        verify_provider_api_key("deepseek", "key", transport=failing_http)

    def unreachable(request: Request, timeout: float) -> bytes:
        raise URLError("offline")

    with pytest.raises(
        ProviderKeyVerificationError,
        match="DeepSeek could not be reached: offline",
    ):
        verify_provider_api_key("deepseek", "key", transport=unreachable)


def test_verify_provider_api_key_rejects_an_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported model provider: fake"):
        verify_provider_api_key("fake", "key", transport=lambda r, t: b"")

