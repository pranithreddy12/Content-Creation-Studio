"""Tests for workflow effect-* and control-* node families.

These exercise `_exec_node` directly so they don't need the full DB-backed
WorkflowRun pipeline — just the per-node behavior:

  * effect.http  → calls httpx with the configured URL/method/body
  * effect.publish → returns the published-asset envelope
  * effect.enqueue → dispatches a Celery signature
  * control.condition → evaluates the configured predicate
  * control.loop → returns the configured iteration count
  * `$.path` resolver in edge `when` predicates
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest

os.environ.setdefault("SECRET_KEY", "testtesttesttesttesttesttesttest")

from app.services.workflow.runner import _eval_when, _exec_node, _resolve  # noqa: E402
from app.services.workflow.schema import NodeDef  # noqa: E402


@pytest.mark.asyncio
async def test_effect_http_calls_configured_url(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 201
        def json(self): return {"ok": True, "id": 42}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def request(self, method, url, **kw):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kw.get("json")
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    node = NodeDef(id="h1", kind="effect.http", config={
        "url": "https://api.example.com/notify",
        "method": "PUT",
        "body": {"event": "asset_ready", "id": "abc"},
    })
    result, _cost = await _exec_node(uuid4(), node, {"trigger": {}})
    assert result["status"] == 201
    assert result["body"] == {"ok": True, "id": 42}
    assert captured["method"] == "PUT"
    assert captured["url"] == "https://api.example.com/notify"
    assert captured["json"] == {"event": "asset_ready", "id": "abc"}


@pytest.mark.asyncio
async def test_effect_http_defaults_method_to_post_and_uses_node_input_as_body(monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self): raise ValueError("no json")
        text = "plaintext OK"

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def request(self, method, url, **kw):
            captured["method"] = method
            captured["body"] = kw.get("json")
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    node = NodeDef(id="h2", kind="effect.http", config={"url": "https://x/y"})
    result, _cost = await _exec_node(uuid4(), node, {"trigger": {"k": "v"}})
    assert result["status"] == 200
    # When response isn't JSON, _safe_json returns the truncated text.
    assert "OK" in result["body"]
    assert captured["method"] == "POST"
    # No body configured → node_input is sent as the body.
    assert captured["body"] == {"trigger": {"k": "v"}}


@pytest.mark.asyncio
async def test_effect_publish_returns_envelope():
    node = NodeDef(id="p1", kind="effect.publish")
    aid = str(uuid4())
    result, _cost = await _exec_node(uuid4(), node, {"asset_id": aid})
    assert result == {"published": True, "asset_id": aid}


@pytest.mark.asyncio
async def test_effect_enqueue_dispatches_celery_signature(monkeypatch):
    """The enqueue node should call celery_app.signature(...).apply_async()."""
    captured = {}

    class FakeAsyncResult:
        id = "fake-task-id"

    class FakeSignature:
        def __init__(self, task, args=None, kwargs=None):
            captured["task"] = task
            captured["args"] = args
            captured["kwargs"] = kwargs
        def apply_async(self): return FakeAsyncResult()

    fake_app = MagicMock()
    fake_app.signature.side_effect = lambda task, args=None, kwargs=None: FakeSignature(task, args, kwargs)

    import app.workers.celery_app as celery_app_mod
    monkeypatch.setattr(celery_app_mod, "celery_app", fake_app)

    node = NodeDef(id="q1", kind="effect.enqueue", config={
        "task": "app.workers.tasks.health_tasks.ping",
        "args": ["from-effect"],
        "kwargs": {"foo": 1},
    })
    result, _cost = await _exec_node(uuid4(), node, {})
    assert result == {"enqueued": "app.workers.tasks.health_tasks.ping"}
    assert captured["task"] == "app.workers.tasks.health_tasks.ping"
    assert captured["args"] == ["from-effect"]
    assert captured["kwargs"] == {"foo": 1}


@pytest.mark.asyncio
async def test_control_condition_evaluates_predicate():
    node = NodeDef(id="c1", kind="control.condition", config={
        "when": {"gt": ["$.score", 0.5]},
    })
    r, _ = await _exec_node(uuid4(), node, {"score": 0.8})
    assert r == {"matched": True}

    r2, _ = await _exec_node(uuid4(), node, {"score": 0.2})
    assert r2 == {"matched": False}


@pytest.mark.asyncio
async def test_control_loop_returns_iteration_count():
    node = NodeDef(id="l1", kind="control.loop", config={"times": 5})
    r, _ = await _exec_node(uuid4(), node, {"input_key": "x"})
    assert r["iterations"] == 5
    assert r["input"] == {"input_key": "x"}


# --- pure predicate helpers ---

def test_eval_when_eq_neq_in():
    assert _eval_when({"eq": ["a", "a"]}, None) is True
    assert _eval_when({"neq": [1, 2]}, None) is True
    assert _eval_when({"in": ["x", ["a", "x", "y"]]}, None) is True
    assert _eval_when({"in": ["z", ["a", "x", "y"]]}, None) is False


def test_eval_when_resolves_path_against_root():
    # `$.foo.bar` should look up the value in the dict.
    result = _eval_when({"eq": ["$.foo.bar", 42]}, {"foo": {"bar": 42}})
    assert result is True

    result = _eval_when({"eq": ["$.foo.bar", 42]}, {"foo": {"bar": 41}})
    assert result is False


def test_resolve_returns_literal_when_not_a_path():
    assert _resolve("hello", {"foo": 1}) == "hello"
    assert _resolve(42, {}) == 42
    assert _resolve(None, {}) is None


def test_resolve_returns_none_when_path_missing():
    assert _resolve("$.missing.deep", {}) is None
    assert _resolve("$.foo.bar", {"foo": "not a dict"}) is None


def test_eval_when_returns_false_for_unknown_op():
    assert _eval_when({"bogus_op": [1, 2]}, None) is False
