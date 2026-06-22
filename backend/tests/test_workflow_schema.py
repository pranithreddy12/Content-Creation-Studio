import pytest

from app.services.workflow.schema import validate_workflow


VALID = {
    "nodes": [
        {"id": "1", "kind": "trigger.schedule"},
        {"id": "2", "kind": "agent.research"},
        {"id": "3", "kind": "agent.writer"},
    ],
    "edges": [
        {"source": "1", "target": "2"},
        {"source": "2", "target": "3"},
    ],
}


def test_validate_accepts_valid():
    wf = validate_workflow(VALID)
    assert len(wf.nodes) == 3
    assert wf.entry_nodes()[0].id == "1"


def test_validate_rejects_cycle():
    with pytest.raises(ValueError, match="cycle"):
        validate_workflow({
            "nodes": [{"id": "a", "kind": "agent.writer"}, {"id": "b", "kind": "agent.seo"}],
            "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
        })


def test_validate_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown node kind"):
        validate_workflow({"nodes": [{"id": "x", "kind": "agent.bogus"}], "edges": []})


def test_validate_rejects_dangling_edge():
    with pytest.raises(ValueError, match="unknown node"):
        validate_workflow({
            "nodes": [{"id": "a", "kind": "agent.writer"}],
            "edges": [{"source": "a", "target": "missing"}],
        })


def test_kind_inferred_from_label():
    wf = validate_workflow({
        "nodes": [
            {"id": "1", "data": {"label": "Schedule trigger"}},
            {"id": "2", "data": {"label": "Research agent"}},
        ],
        "edges": [{"source": "1", "target": "2"}],
    })
    assert wf.node("1").kind == "trigger.schedule"
    assert wf.node("2").kind == "agent.research"
