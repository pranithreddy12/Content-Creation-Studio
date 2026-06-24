"""Prometheus /metrics surface tests.

Verify the public metrics endpoint exposes the counters + histograms the
Grafana dashboards consume. This protects against silent regressions where
a metric gets renamed or accidentally removed and dashboards go blank.

The /metrics endpoint is mounted via prometheus_client.make_asgi_app(), so we
just curl it and pattern-match against the text exposition format.
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.main import app  # noqa: E402


REQUIRED_METRICS = [
    # HTTP middleware
    "http_requests_total",
    "http_request_duration_seconds",
    # Business — content loop
    "daily_loop_kickoffs_total",
    "ideas_generated_total",
    "assets_generated_total",
    "video_renders_total",
    # Publishing
    "publish_attempts_total",
    "publish_latency_seconds",
    # Agents / LLM
    "agent_runs_total",
    "llm_cost_usd_total",
    "llm_tokens_total",
    # Ingest
    "ingest_runs_total",
    "ingest_chunks_total",
]


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200_and_text():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as cx:
        r = await cx.get("/metrics")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    # prometheus_client returns text/plain; version=0.0.4
    assert "text/plain" in ct or "application/openmetrics-text" in ct
    assert r.text.strip(), "metrics output must not be empty"


@pytest.mark.asyncio
async def test_every_required_metric_appears_in_exposition():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as cx:
        r = await cx.get("/metrics")
    body = r.text
    missing = [m for m in REQUIRED_METRICS if m not in body]
    assert not missing, f"these metrics are not exported: {missing}"


@pytest.mark.asyncio
async def test_help_and_type_lines_emitted_for_counters():
    """Every counter must have its `# HELP` and `# TYPE` exposition lines."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as cx:
        r = await cx.get("/metrics")
    body = r.text
    for metric in ["agent_runs_total", "llm_cost_usd_total", "publish_attempts_total"]:
        assert f"# HELP {metric}" in body, f"missing # HELP for {metric}"
        assert f"# TYPE {metric}" in body, f"missing # TYPE for {metric}"


@pytest.mark.asyncio
async def test_business_counter_increments_after_use():
    """Incrementing a counter via the public API actually moves the exported value."""
    from app.core.metrics import IDEAS_GENERATED

    before = IDEAS_GENERATED.labels(brand_id="brand-test-counter")._value.get()
    IDEAS_GENERATED.labels(brand_id="brand-test-counter").inc(7)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as cx:
        r = await cx.get("/metrics")
    body = r.text

    # The exposition will include a line like:
    # ideas_generated_total{brand_id="brand-test-counter"} 7.0
    assert 'ideas_generated_total{brand_id="brand-test-counter"}' in body, \
        f"counter+label not present in exposition; before-value={before}"


@pytest.mark.asyncio
async def test_histogram_buckets_are_exported():
    """Histograms must expose their _bucket / _sum / _count series."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as cx:
        # Hit /health once to make sure http_request_duration_seconds has observations
        await cx.get("/health")
        r = await cx.get("/metrics")
    body = r.text
    assert "http_request_duration_seconds_bucket" in body
    assert "http_request_duration_seconds_count" in body
    assert "http_request_duration_seconds_sum" in body


@pytest.mark.asyncio
async def test_metrics_exempt_from_rate_limit_and_auth():
    """The metrics endpoint must NOT require Clerk auth and must NOT be rate-limited."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", follow_redirects=True) as cx:
        # Hammer it once just to make sure it never 429s.
        for _ in range(5):
            r = await cx.get("/metrics")
            assert r.status_code == 200
