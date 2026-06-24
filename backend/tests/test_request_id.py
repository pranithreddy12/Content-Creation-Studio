"""Request-ID propagation + structured log correlation.

  * A request with no x-request-id header gets one assigned (32-hex-char UUID)
  * A request that supplies x-request-id has it echoed back unmodified
  * The request_id flows into structlog's contextvars and appears on the
    structured log line emitted by LoggingMiddleware
  * The structured log line includes path, method, status, and duration_ms
"""
from __future__ import annotations

import json
import os
import re
import uuid
from io import StringIO

import pytest
import structlog
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.main import app  # noqa: E402

UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.mark.asyncio
async def test_request_id_assigned_when_absent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as cx:
        r = await cx.get("/health")
    assert r.status_code == 200
    rid = r.headers.get("x-request-id")
    assert rid is not None
    assert UUID_HEX_RE.match(rid), f"not a UUID hex: {rid}"


@pytest.mark.asyncio
async def test_request_id_echoes_provided_value():
    incoming = f"trace-{uuid.uuid4().hex}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as cx:
        r = await cx.get("/health", headers={"x-request-id": incoming})
    assert r.headers.get("x-request-id") == incoming


@pytest.mark.asyncio
async def test_request_id_each_request_unique():
    """Two back-to-back requests without an incoming id should get distinct ones."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as cx:
        r1 = await cx.get("/health")
        r2 = await cx.get("/health")
    a = r1.headers["x-request-id"]
    b = r2.headers["x-request-id"]
    assert a != b


@pytest.mark.asyncio
async def test_request_id_appears_in_structured_log_line():
    """LoggingMiddleware emits a JSON-encoded structured log. The request_id we sent
    must appear in that line, alongside path/method/status/duration_ms."""
    incoming = f"corr-{uuid.uuid4().hex[:12]}"

    # Capture structlog output by redirecting its renderer to a buffer.
    buf = StringIO()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(20),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buf),
        cache_logger_on_first_use=False,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as cx:
        await cx.get("/health", headers={"x-request-id": incoming})

    output = buf.getvalue()
    matched = [line for line in output.splitlines() if incoming in line]
    assert matched, f"no log line contained request_id {incoming!r}; output={output!r}"

    entry = json.loads(matched[-1])
    assert entry.get("request_id") == incoming
    assert entry.get("event") == "request"
    assert entry.get("status") == 200
    assert entry.get("path") == "/health"
    assert entry.get("method") == "GET"
    assert isinstance(entry.get("duration_ms"), (int, float))
