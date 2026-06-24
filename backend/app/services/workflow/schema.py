"""Workflow definition schema + validator.

A workflow is a directed graph of nodes connected by edges. Nodes come in four
families:

- trigger.*    — entry points (schedule | webhook | event)
- agent.*      — invokes one of the registered AI agents
- control.*    — flow control (condition | loop | approval)
- effect.*     — side effects (publish | enqueue | http)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KNOWN_NODE_KINDS: set[str] = {
    # triggers
    "trigger.schedule", "trigger.webhook", "trigger.event",
    # agents (map to app.agents.registry.AGENTS)
    "agent.research", "agent.strategist", "agent.writer", "agent.seo",
    "agent.designer", "agent.video", "agent.publisher", "agent.analytics",
    "agent.learning",
    # control
    "control.condition", "control.loop", "control.approval",
    # effects
    "effect.publish", "effect.http", "effect.enqueue",
}


@dataclass
class NodeDef:
    id: str
    kind: str
    config: dict = field(default_factory=dict)
    label: str | None = None


@dataclass
class EdgeDef:
    source: str
    target: str
    when: dict | None = None       # optional condition predicate evaluated against context


@dataclass
class WorkflowDef:
    nodes: list[NodeDef]
    edges: list[EdgeDef]

    def node(self, nid: str) -> NodeDef:
        for n in self.nodes:
            if n.id == nid:
                return n
        raise KeyError(nid)

    def out_edges(self, nid: str) -> list[EdgeDef]:
        return [e for e in self.edges if e.source == nid]

    def entry_nodes(self) -> list[NodeDef]:
        targets = {e.target for e in self.edges}
        return [n for n in self.nodes if n.id not in targets and n.kind.startswith("trigger.")]


def _coerce_node(raw: Any) -> NodeDef:
    if isinstance(raw, dict):
        data = raw.get("data", {})
        return NodeDef(
            id=str(raw["id"]),
            kind=str(raw.get("kind") or data.get("kind") or _kind_from_label(data.get("label", ""))),
            config=raw.get("config") or data.get("config") or {},
            label=data.get("label"),
        )
    raise TypeError("node must be a dict")


def _kind_from_label(label: str) -> str:
    """Loose fallback when the React Flow node carries a label but no explicit kind."""
    s = (label or "").lower()
    if "research" in s: return "agent.research"
    if "writer" in s: return "agent.writer"
    if "seo" in s: return "agent.seo"
    if "video" in s: return "agent.video"
    if "designer" in s: return "agent.designer"
    if "publisher" in s: return "agent.publisher"
    if "analytics" in s: return "agent.analytics"
    if "learning" in s: return "agent.learning"
    if "strategist" in s: return "agent.strategist"
    if "schedule" in s: return "trigger.schedule"
    if "webhook" in s: return "trigger.webhook"
    if "event" in s: return "trigger.event"
    if "condition" in s: return "control.condition"
    if "loop" in s: return "control.loop"
    if "approval" in s: return "control.approval"
    return "effect.http"


def _coerce_edge(raw: Any) -> EdgeDef:
    if isinstance(raw, dict):
        return EdgeDef(
            source=str(raw["source"]),
            target=str(raw["target"]),
            when=raw.get("when"),
        )
    raise TypeError("edge must be a dict")


def validate_workflow(raw: dict) -> WorkflowDef:
    nodes = [_coerce_node(n) for n in (raw.get("nodes") or [])]
    edges = [_coerce_edge(e) for e in (raw.get("edges") or [])]

    if not nodes:
        raise ValueError("workflow must have at least one node")
    ids = {n.id for n in nodes}
    if len(ids) != len(nodes):
        raise ValueError("duplicate node id")
    for e in edges:
        if e.source not in ids or e.target not in ids:
            raise ValueError(f"edge references unknown node: {e.source} -> {e.target}")
    for n in nodes:
        if n.kind not in KNOWN_NODE_KINDS:
            raise ValueError(f"unknown node kind: {n.kind}")

    # cycle check — Kahn topological sort
    indeg = {n.id: 0 for n in nodes}
    for e in edges:
        indeg[e.target] += 1
    ready = [nid for nid, d in indeg.items() if d == 0]
    seen = 0
    while ready:
        cur = ready.pop()
        seen += 1
        for e in [ed for ed in edges if ed.source == cur]:
            indeg[e.target] -= 1
            if indeg[e.target] == 0:
                ready.append(e.target)
    if seen != len(nodes):
        raise ValueError("workflow contains a cycle")
    return WorkflowDef(nodes=nodes, edges=edges)
