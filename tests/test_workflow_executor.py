import pytest

from takopi.commands import RunRequest, RunResult
from takopi.plugins.workflow.checkpoint import checkpoint_from_graph
from takopi.plugins.workflow.executor import ExecutionConfig, resume_workflow, run_workflow
from takopi.plugins.workflow.graph import TaskStatus, parse_workflow
from takopi.transport import RenderedMessage


class FakeExecutor:
    def __init__(self, results_by_prompt: dict[str, str | None]):
        self.results_by_prompt = results_by_prompt
        self.batches: list[list[str]] = []

    async def send(self, message, *, reply_to=None, notify=True):  # pragma: no cover - unused
        return None

    async def run_one(self, request: RunRequest, *, mode: str = "emit") -> RunResult:
        return (await self.run_many([request], mode=mode))[0]

    async def run_many(
        self,
        requests: list[RunRequest],
        *,
        mode: str = "emit",
        parallel: bool = False,
    ) -> list[RunResult]:
        self.batches.append([req.prompt for req in requests])
        results: list[RunResult] = []
        for req in requests:
            payload = self.results_by_prompt.get(req.prompt, f"out:{req.prompt}")
            if payload is None:
                results.append(RunResult(engine=req.engine or "test", message=None))
            else:
                results.append(
                    RunResult(
                        engine=req.engine or "test",
                        message=RenderedMessage(text=payload),
                    )
                )
        return results


@pytest.mark.anyio
async def test_run_workflow_substitutes_dependency_results() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "alpha", "engine": "test"},
            {"id": "b", "prompt": "echo {a}", "depends_on": ["a"], "engine": "test"},
        ]
    }
    executor = FakeExecutor({"alpha": "hello"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    assert result.graph.tasks["a"].result == "hello"
    assert result.graph.tasks["b"].result == "out:echo hello"
    assert executor.batches == [["alpha"], ["echo hello"]]


@pytest.mark.anyio
async def test_run_workflow_skips_dependents_on_failure() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "fail", "engine": "test"},
            {"id": "b", "prompt": "use {a}", "depends_on": ["a"], "engine": "test"},
        ]
    }
    executor = FakeExecutor({"fail": None})
    config = ExecutionConfig(continue_on_failure=False)

    result = await run_workflow(executor, spec, config=config)

    assert result.graph.tasks["a"].status is TaskStatus.FAILED
    assert result.graph.tasks["b"].status is TaskStatus.SKIPPED
    assert "failed dependency: a" in (result.graph.tasks["b"].error or "")
    assert result.checkpoint.failed_tasks == 1
    assert result.checkpoint.skipped_tasks == 1


@pytest.mark.anyio
async def test_run_workflow_conditional_branch_skips_false_path() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "source", "engine": "test"},
            {"id": "t1", "prompt": "true path", "depends_on": ["a"], "engine": "test"},
            {"id": "t2", "prompt": "false path", "depends_on": ["a"], "engine": "test"},
        ],
        "conditionals": [
            {
                "id": "cond1",
                "depends_on": ["a"],
                "condition": {"type": "contains", "task_id": "a", "value": "yes"},
                "if_true": ["t1"],
                "if_false": ["t2"],
            }
        ],
    }
    executor = FakeExecutor({"source": "yes"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    assert result.graph.conditionals["cond1"].branch_taken == "true"
    assert result.graph.tasks["t1"].status is TaskStatus.COMPLETED
    assert result.graph.tasks["t2"].status is TaskStatus.SKIPPED


@pytest.mark.anyio
async def test_run_workflow_loop_times_records_iterations() -> None:
    spec = {
        "loops": [
            {
                "id": "loop1",
                "type": "times",
                "count": 2,
                "tasks": [{"id": "step", "prompt": "iter {loop.iteration}", "engine": "test"}],
            }
        ]
    }
    executor = FakeExecutor({"iter 0": "done0", "iter 1": "done1"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    loop = result.graph.loops["loop1"]
    assert loop.status.value == "completed"
    assert len(loop.iterations) == 2
    assert loop.iterations[0]["context"]["iteration"] == 0
    assert loop.iterations[1]["context"]["iteration"] == 1
    assert loop.final_result == "done1"


@pytest.mark.anyio
async def test_run_workflow_early_exit_halts_execution() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "gate", "engine": "test"},
            {"id": "b", "prompt": "should not run", "depends_on": ["a"], "engine": "test"},
        ],
        "early_exits": [
            {
                "id": "exit1",
                "depends_on": ["a"],
                "condition": {"type": "contains", "task_id": "a", "value": "stop"},
                "message": "Stop now",
            }
        ],
    }
    executor = FakeExecutor({"gate": "stop"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    assert result.checkpoint.status == "halted"
    assert result.graph.halted is True
    assert result.graph.tasks["b"].status is TaskStatus.PENDING


@pytest.mark.anyio
async def test_run_workflow_loop_foreach_uses_items() -> None:
    spec = {
        "tasks": [{"id": "items", "prompt": "items", "engine": "test"}],
        "loops": [
            {
                "id": "loop1",
                "type": "foreach",
                "items_from": "items",
                "item_separator": ",",
                "depends_on": ["items"],
                "tasks": [{"id": "step", "prompt": "color {loop.item}", "engine": "test"}],
            }
        ],
    }
    executor = FakeExecutor({"items": "red,blue", "color red": "R", "color blue": "B"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    loop = result.graph.loops["loop1"]
    assert loop.items == ["red", "blue"]
    assert len(loop.iterations) == 2
    assert loop.final_result == "B"
    assert result.graph.tasks["loop1.0.step"].result == "R"
    assert result.graph.tasks["loop1.1.step"].result == "B"


@pytest.mark.anyio
async def test_resume_workflow_runs_pending_tasks() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "alpha", "engine": "test"},
            {"id": "b", "prompt": "beta {a}", "depends_on": ["a"], "engine": "test"},
        ]
    }
    graph = parse_workflow(spec)
    graph.tasks["a"].status = TaskStatus.COMPLETED
    graph.tasks["a"].result = "done"
    checkpoint = checkpoint_from_graph(graph, spec, workflow_id="wf1", status="running")

    executor = FakeExecutor({"beta done": "finished"})

    result = await resume_workflow(executor, checkpoint, config=ExecutionConfig())

    assert result.graph.tasks["a"].status is TaskStatus.COMPLETED
    assert result.graph.tasks["b"].status is TaskStatus.COMPLETED
    assert result.graph.tasks["b"].result == "finished"


@pytest.mark.anyio
async def test_run_workflow_loop_until_condition_met() -> None:
    spec = {
        "loops": [
            {
                "id": "loop1",
                "type": "until",
                "condition": {"type": "equals", "value": "stop"},
                "max_iterations": 3,
                "tasks": [{"id": "step", "prompt": "tick {loop.iteration}", "engine": "test"}],
            }
        ]
    }
    executor = FakeExecutor(
        {
            "tick 0": "go",
            "tick 1": "stop",
        }
    )

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    loop = result.graph.loops["loop1"]
    assert loop.status.value == "completed"
    assert len(loop.iterations) == 2
    assert loop.final_result == "stop"


@pytest.mark.anyio
async def test_run_workflow_loop_while_condition_controls_start() -> None:
    spec = {
        "tasks": [{"id": "gate", "prompt": "gate", "engine": "test"}],
        "loops": [
            {
                "id": "loop1",
                "type": "while",
                "condition": {"type": "contains", "task_id": "gate", "value": "go"},
                "depends_on": ["gate"],
                "tasks": [{"id": "step", "prompt": "loop {loop.iteration}", "engine": "test"}],
            }
        ],
    }
    executor = FakeExecutor({"gate": "stop"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    loop = result.graph.loops["loop1"]
    assert loop.status.value == "completed"
    assert len(loop.iterations) == 0
    assert loop.final_result is None


@pytest.mark.anyio
async def test_task_condition_expr_skips_task() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "alpha", "engine": "test"},
            {
                "id": "b",
                "prompt": "beta",
                "depends_on": ["a"],
                "condition": {"type": "expr", "value": "len(result) > 10"},
                "engine": "test",
            },
        ]
    }
    executor = FakeExecutor({"alpha": "short"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    assert result.graph.tasks["a"].status is TaskStatus.COMPLETED
    assert result.graph.tasks["b"].status is TaskStatus.SKIPPED


@pytest.mark.anyio
async def test_task_condition_eval_task_runs_on_yes() -> None:
    spec = {
        "tasks": [
            {"id": "a", "prompt": "judge", "engine": "test"},
            {
                "id": "b",
                "prompt": "approved",
                "depends_on": ["a"],
                "condition": {"type": "eval_task", "task_id": "a"},
                "engine": "test",
            },
        ]
    }
    executor = FakeExecutor({"judge": "yes"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    assert result.graph.tasks["b"].status is TaskStatus.COMPLETED


@pytest.mark.anyio
async def test_run_workflow_loop_stops_at_max_iterations() -> None:
    spec = {
        "loops": [
            {
                "id": "loop1",
                "type": "until",
                "condition": {"type": "equals", "value": "stop"},
                "max_iterations": 2,
                "tasks": [{"id": "step", "prompt": "tick {loop.iteration}", "engine": "test"}],
            }
        ]
    }
    executor = FakeExecutor({"tick 0": "go 0", "tick 1": "go 1"})

    result = await run_workflow(executor, spec, config=ExecutionConfig())

    loop = result.graph.loops["loop1"]
    assert loop.status.value == "max_iterations"
    assert len(loop.iterations) == 2
    assert loop.final_result == "go 1"
