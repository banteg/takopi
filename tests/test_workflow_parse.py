import pytest

from takopi.plugins.workflow.backend import WorkflowCommand
from takopi.plugins.workflow.graph import parse_workflow


def test_parse_workflow_allows_task_depends_on_conditional_and_loop() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "alpha"},
            {"id": "b", "prompt": "beta", "depends_on": ["cond1", "loop1"]},
        ],
        "conditionals": [
            {
                "id": "cond1",
                "condition": {"type": "equals", "task_id": "a", "value": "ok"},
                "depends_on": ["a"],
                "if_true": [],
                "if_false": [],
            }
        ],
        "loops": [
            {
                "id": "loop1",
                "type": "times",
                "count": 1,
                "tasks": [{"id": "step", "prompt": "step"}],
            }
        ],
    }

    graph = parse_workflow(spec)

    assert "b" in graph.tasks
    assert "cond1" in graph.conditionals
    assert "loop1" in graph.loops


def test_normalize_llm_spec_converts_when_to_expr() -> None:
    data = {
        "tasks": [
            {
                "id": "a",
                "prompt": "alpha",
                "condition": {"when": "{{ len(result) > 3 }}"},
            }
        ],
        "conditionals": [
            {
                "id": "cond1",
                "condition": {"when": "result == 'ok'"},
                "depends_on": [],
                "if_true": [],
                "if_false": [],
            }
        ],
    }

    WorkflowCommand()._normalize_llm_spec(data)

    task_condition = data["tasks"][0]["condition"]
    assert task_condition["type"] == "expr"
    assert task_condition["value"] == "len(result) > 3"

    cond_condition = data["conditionals"][0]["condition"]
    assert cond_condition["type"] == "expr"
    assert cond_condition["value"] == "result == 'ok'"


def test_parse_workflow_detects_cycles() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "alpha", "depends_on": ["b"]},
            {"id": "b", "prompt": "beta", "depends_on": ["a"]},
        ]
    }

    with pytest.raises(ValueError, match="Circular or unresolved dependencies"):
        parse_workflow(spec)
