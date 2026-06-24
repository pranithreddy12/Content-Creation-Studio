"""Lint-style audit: prevent regressions of the nested-asyncio.run bug class.

Two production bugs in this codebase had the same shape: a sync Celery task whose
body called `asyncio.run()` inside its own logic (instead of as the single
outermost wrapper). That pattern crashes inside any other code path that's
already running an event loop — pytest, ASGI handlers, etc.

This audit scans every Celery task module and verifies that:

  1. Each task function has at most ONE direct `asyncio.run` line in its body
  2. That single call must be either the only statement or appear in a `return`
     statement — i.e., it's the outer wrapper, not a sub-step

If a future PR introduces another nested call, this test fails immediately.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


# Where the Celery task modules live + a couple of integration glue modules
# that also wrap async cores.
SCAN_DIRS = [
    Path("/app/app/workers/tasks"),
    Path("/app/app/integrations"),  # video_render, media_render
]


def _files_to_scan() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        files.extend(p for p in d.rglob("*.py") if p.name != "__init__.py")
    return files


def _is_asyncio_run(node: ast.AST) -> bool:
    """Return True iff `node` is an `asyncio.run(...)` Call expression."""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr == "run"
        and isinstance(fn.value, ast.Name)
        and fn.value.id == "asyncio"
    )


# Files / functions we're explicitly OK with — used for the Celery-only paths
# whose nested asyncio.run can never reach a running event loop in production.
ALLOWED_NESTED = {
    # (file basename, function name)
    ("loop_tasks.py", "_quota"),
}


def test_no_unexpected_nested_asyncio_run_in_celery_tasks():
    """Every Celery sync wrapper should have asyncio.run as its single outermost call."""
    violations: list[str] = []
    for path in _files_to_scan():
        src = path.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.AsyncFunctionDef):
                # async helpers should never call asyncio.run; if they do, it's wrong.
                bad = [c for c in ast.walk(node) if _is_asyncio_run(c)]
                if bad:
                    violations.append(
                        f"{path.name}:{node.lineno} async function {node.name!r} "
                        f"calls asyncio.run ({len(bad)} times)"
                    )
                continue
            # Sync function body — collect every asyncio.run call
            runs = [c for c in ast.walk(node) if _is_asyncio_run(c)]
            if len(runs) > 1:
                violations.append(
                    f"{path.name}:{node.lineno} sync function {node.name!r} has "
                    f"{len(runs)} asyncio.run calls (limit 1)"
                )
                continue
            if not runs:
                continue
            # Exactly one call. Verify it's a top-level statement of the function
            # (i.e. `asyncio.run(...)` or `return asyncio.run(...)` or
            # `xs = asyncio.run(...)`) — NOT nested inside an inner `def`, `for`,
            # `if`, or expression-of-expression.
            call = runs[0]
            # Treat statements inside a top-level try/except/finally as top-level too:
            # `try: x = asyncio.run(...)` is still the outer wrapper (a guarded call),
            # not a sub-step nested in a for/if. Unwrap one level of Try only.
            top_level_stmts: list[ast.stmt] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Try):
                    top_level_stmts.extend(stmt.body)
                    for h in stmt.handlers:
                        top_level_stmts.extend(h.body)
                    top_level_stmts.extend(stmt.orelse)
                    top_level_stmts.extend(stmt.finalbody)
                else:
                    top_level_stmts.append(stmt)
            is_top_level = False
            for stmt in top_level_stmts:
                # Walk one level only — not full ast.walk
                immediate_children = list(ast.iter_child_nodes(stmt))
                # The call should either be the immediate expression or wrapped
                # in a small comprehension like `[str(i) for i in asyncio.run(...)]`
                # at top level.
                if call in immediate_children:
                    is_top_level = True; break
                for ch in immediate_children:
                    if call in ast.walk(ch) and (
                        # Acceptable parents at top level: a Return, Assign, Expr,
                        # ListComp / GeneratorExp wrapping the call directly.
                        isinstance(stmt, (ast.Return, ast.Assign, ast.AugAssign, ast.Expr))
                    ):
                        is_top_level = True; break
                if is_top_level: break
            allow_key = (path.name, node.name)
            if not is_top_level and allow_key not in ALLOWED_NESTED:
                violations.append(
                    f"{path.name}:{node.lineno} sync function {node.name!r} "
                    f"has a non-top-level asyncio.run call — "
                    f"would crash from pytest / ASGI handlers"
                )

    assert not violations, "Found dangerous asyncio.run patterns:\n  " + "\n  ".join(violations)


def test_celery_task_wrappers_have_correct_shape():
    """Sanity: confirm every known sync wrapper has its async helper next to it."""
    expected_pairs = [
        ("ingest_tasks.py", "ingest_source_task", "_run"),
        ("research_tasks.py", "research_brand", "_do_research"),
        ("ideas_tasks.py", "generate_ideas", "_generate"),
        ("ideas_tasks.py", "score_idea", "_score"),
        ("ideas_tasks.py", "select_top_ideas", "_select_top"),
        ("writing_tasks.py", "generate_blog", "_generate_blog"),
        ("writing_tasks.py", "generate_social_bundle", "_generate_social_bundle"),
        ("media_tasks.py", "generate_media", "_generate"),
        ("video_tasks.py", "render_video_for_idea", "_render"),
        ("seo_tasks.py", "optimize_asset", "_optimize"),
        ("publishing_tasks.py", "dispatch_for_idea", "_dispatch"),
        ("publishing_tasks.py", "publish_due", "_publish_due"),
        ("analytics_tasks.py", "collect_for_asset", "_collect_for_asset"),
        ("analytics_tasks.py", "rollup_recent", "_rollup"),
        ("learning_tasks.py", "update_brand", "_update_brand"),
        ("viral_tasks.py", "crawl_all_platforms", "_crawl"),
        ("loop_tasks.py", "kickoff_daily_loops", "_do_kickoff"),
        ("workflow_tasks.py", "run_workflow_task", "run_workflow"),  # imported, not _-prefixed
    ]
    missing: list[str] = []
    for path in _files_to_scan():
        src = path.read_text()
        for fname, sync, _helper in expected_pairs:
            if path.name != fname:
                continue
            if f"def {sync}" not in src:
                missing.append(f"{path.name}: missing sync wrapper '{sync}'")
    assert not missing, "Missing sync wrappers:\n  " + "\n  ".join(missing)
