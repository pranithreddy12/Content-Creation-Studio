"""SSRF guard tests."""
from __future__ import annotations

import os
import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from fastapi import HTTPException  # noqa: E402

from app.services.ingestion.url_guard import validate_external_url  # noqa: E402


GOOD_URLS = [
    "https://example.com",
    "http://example.com/path?q=1",
    "https://www.wikipedia.org/wiki/Foo",
    "https://github.com/anthropic/repo",
]


BAD_URLS_NETWORK = [
    "http://127.0.0.1",
    "http://127.0.0.1:8000/admin",
    "http://localhost",
    "http://localhost:8080",
    "http://10.0.0.5",
    "http://172.16.5.5/secret",
    "http://192.168.1.1",
    "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
    "http://[::1]/",
    "http://0.0.0.0",
]

BAD_URLS_SCHEME = [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com",
    "javascript:alert(1)",
    "data:text/plain;base64,YWJjZA==",
]

BAD_URLS_MALFORMED = [
    "https://",                  # no host
    "https:///nohost",           # no host
    "not a url at all",          # no scheme
]


@pytest.mark.parametrize("url", GOOD_URLS)
def test_accepts_public_urls(url: str) -> None:
    validate_external_url(url)  # must not raise


@pytest.mark.parametrize("url", BAD_URLS_NETWORK)
def test_blocks_internal_addresses(url: str) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_external_url(url)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("url", BAD_URLS_SCHEME)
def test_blocks_non_http_schemes(url: str) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_external_url(url)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("url", BAD_URLS_MALFORMED)
def test_blocks_malformed_urls(url: str) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_external_url(url)
    assert exc.value.status_code == 400


def test_skips_empty_and_none() -> None:
    # Sources with kind=topic or pdf/voice (file uploads) pass url=None.
    validate_external_url(None)
    validate_external_url("")


def test_blocks_overlong_url() -> None:
    long_url = "https://example.com/" + ("a" * 5000)
    with pytest.raises(HTTPException) as exc:
        validate_external_url(long_url)
    assert exc.value.status_code == 400
    assert "too long" in exc.value.detail
