from takopi.plugins.workflow.backend import WorkflowCommand


def test_normalize_llm_spec_injects_missing_dependency_placeholders() -> None:
    data = {
        "tasks": [
            {"id": "b", "prompt": "Summarize the inputs.", "depends_on": ["a", "c"]}
        ]
    }

    WorkflowCommand()._normalize_llm_spec(data)

    prompt = data["tasks"][0]["prompt"]
    assert "Context from dependencies:" in prompt
    assert "- a: {a}" in prompt
    assert "- c: {c}" in prompt


def test_normalize_llm_spec_respects_existing_placeholders() -> None:
    data = {"tasks": [{"id": "b", "prompt": "Use {a}.", "depends_on": ["a"]}]}

    WorkflowCommand()._normalize_llm_spec(data)

    prompt = data["tasks"][0]["prompt"]
    assert prompt == "Use {a}."
