"""Task graph data model for orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class LoopStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Condition Evaluation
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    """
    A condition that can be evaluated against task results.

    Supports:
      - contains: result contains substring
      - not_contains: result doesn't contain substring
      - matches: result matches regex
      - equals: result equals value (stripped)
      - expr: custom Python expression with `result` variable
      - eval_task: ask an LLM to evaluate (returns "yes"/"no"/"true"/"false")
    """
    type: str  # "contains", "not_contains", "matches", "equals", "expr", "eval_task"
    value: str | None = None
    task_id: str | None = None  # Which task's result to check (default: previous)

    def evaluate(self, result: str, all_results: dict[str, str] | None = None) -> bool:
        """Evaluate condition against a result string."""
        result = result.strip() if result else ""

        if self.type == "contains":
            return self.value.lower() in result.lower() if self.value else False

        elif self.type == "not_contains":
            return self.value.lower() not in result.lower() if self.value else True

        elif self.type == "matches":
            if not self.value:
                return False
            return bool(re.search(self.value, result, re.IGNORECASE))

        elif self.type == "equals":
            return result.lower() == (self.value or "").lower()

        elif self.type == "expr":
            # Safe subset of Python expressions
            # Available: result, len(), "in", and/or/not, comparisons
            if not self.value:
                return False
            try:
                # Provide limited context for safety
                ctx = {
                    "result": result,
                    "results": all_results or {},
                    "len": len,
                    "True": True,
                    "False": False,
                }
                return bool(eval(self.value, {"__builtins__": {}}, ctx))
            except Exception:
                return False

        elif self.type == "eval_task":
            # Special case: result should be yes/no/true/false from an evaluator task
            normalized = result.lower().strip()
            return normalized in ("yes", "true", "1", "correct", "pass", "approved")

        return False


# ---------------------------------------------------------------------------
# Task Types
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A single task in the orchestration graph."""

    id: str
    prompt: str
    engine: str | None = None  # None = use default engine
    depends_on: list[str] = field(default_factory=list)

    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    resume_token: str | None = None

    # Optional: routing hints
    project: str | None = None
    branch: str | None = None

    # Conditional execution
    condition: Condition | None = None  # Only run if condition is true

    # Template variables - {task_id} gets replaced with that task's result
    # e.g., prompt="Summarize this: {research}" uses output from 'research' task


@dataclass
class ConditionalBranch:
    """
    A conditional branch point in the workflow.

    Evaluates condition and enables one of two task groups.
    """
    id: str
    condition: Condition
    if_true: list[str] = field(default_factory=list)   # Task IDs to enable
    if_false: list[str] = field(default_factory=list)  # Task IDs to enable
    depends_on: list[str] = field(default_factory=list)

    # Runtime
    status: TaskStatus = TaskStatus.PENDING
    branch_taken: str | None = None  # "true" or "false"


@dataclass
class LoopConfig:
    """
    Configuration for a loop construct.

    Supports:
      - until: repeat until condition is true
      - while: repeat while condition is true
      - times: repeat N times
      - foreach: iterate over items (from a task result)
    """
    type: str  # "until", "while", "times", "foreach"

    # For until/while
    condition: Condition | None = None

    # For times
    count: int | None = None

    # For foreach
    items_from: str | None = None  # Task ID whose result contains items
    item_separator: str = "\n"     # How to split items

    # Safety limits
    max_iterations: int = 10


@dataclass
class Loop:
    """
    A loop construct that repeats a set of tasks.

    The tasks inside the loop can reference:
      - {loop.iteration}: current iteration number (0-indexed)
      - {loop.item}: current item (for foreach loops)
      - {loop.previous}: result from previous iteration's final task
    """
    id: str
    config: LoopConfig
    tasks: list[Task] = field(default_factory=list)  # Tasks to repeat
    depends_on: list[str] = field(default_factory=list)

    # Runtime state
    status: LoopStatus = LoopStatus.PENDING
    current_iteration: int = 0
    iterations: list[dict[str, Any]] = field(default_factory=list)  # Results per iteration
    items: list[str] = field(default_factory=list)  # For foreach loops
    final_result: str | None = None


@dataclass
class EarlyExit:
    """
    An early exit condition that halts the workflow.
    """
    id: str
    condition: Condition
    depends_on: list[str] = field(default_factory=list)
    message: str = "Workflow halted by early exit condition"

    # Runtime
    status: TaskStatus = TaskStatus.PENDING
    triggered: bool = False


@dataclass
class TaskGraph:
    """DAG of tasks with dependency tracking, conditionals, and loops."""

    tasks: dict[str, Task] = field(default_factory=dict)
    conditionals: dict[str, ConditionalBranch] = field(default_factory=dict)
    loops: dict[str, Loop] = field(default_factory=dict)
    early_exits: dict[str, EarlyExit] = field(default_factory=dict)

    # Runtime state
    halted: bool = False
    halt_reason: str | None = None

    def add_task(
        self,
        task_id: str,
        prompt: str,
        *,
        engine: str | None = None,
        depends_on: list[str] | None = None,
        project: str | None = None,
        branch: str | None = None,
        condition: Condition | None = None,
    ) -> Task:
        """Add a task to the graph."""
        if task_id in self.tasks:
            raise ValueError(f"Task {task_id!r} already exists")

        deps = depends_on or []
        for dep in deps:
            if dep not in self.tasks and dep not in self.conditionals and dep not in self.loops:
                raise ValueError(f"Dependency {dep!r} not found for task {task_id!r}")

        task = Task(
            id=task_id,
            prompt=prompt,
            engine=engine,
            depends_on=deps,
            project=project,
            branch=branch,
            condition=condition,
        )
        self.tasks[task_id] = task
        return task

    def add_conditional(
        self,
        cond_id: str,
        condition: Condition,
        *,
        if_true: list[str] | None = None,
        if_false: list[str] | None = None,
        depends_on: list[str] | None = None,
    ) -> ConditionalBranch:
        """Add a conditional branch point."""
        if cond_id in self.conditionals:
            raise ValueError(f"Conditional {cond_id!r} already exists")

        branch = ConditionalBranch(
            id=cond_id,
            condition=condition,
            if_true=if_true or [],
            if_false=if_false or [],
            depends_on=depends_on or [],
        )
        self.conditionals[cond_id] = branch
        return branch

    def add_loop(
        self,
        loop_id: str,
        config: LoopConfig,
        tasks: list[Task],
        *,
        depends_on: list[str] | None = None,
    ) -> Loop:
        """Add a loop construct."""
        if loop_id in self.loops:
            raise ValueError(f"Loop {loop_id!r} already exists")

        loop = Loop(
            id=loop_id,
            config=config,
            tasks=tasks,
            depends_on=depends_on or [],
        )
        self.loops[loop_id] = loop
        return loop

    def add_early_exit(
        self,
        exit_id: str,
        condition: Condition,
        *,
        depends_on: list[str] | None = None,
        message: str = "Workflow halted",
    ) -> EarlyExit:
        """Add an early exit condition."""
        early_exit = EarlyExit(
            id=exit_id,
            condition=condition,
            depends_on=depends_on or [],
            message=message,
        )
        self.early_exits[exit_id] = early_exit
        return early_exit

    def get_all_results(self) -> dict[str, str]:
        """Get all task results as a dict."""
        return {
            task_id: task.result or ""
            for task_id, task in self.tasks.items()
            if task.result is not None
        }

    def get_ready_tasks(self) -> list[Task]:
        """Get all tasks whose dependencies are satisfied."""
        if self.halted:
            return []

        ready = []
        all_results = self.get_all_results()

        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue

            # Check dependencies
            deps_satisfied = all(
                self._is_dependency_satisfied(dep)
                for dep in task.depends_on
            )
            if not deps_satisfied:
                continue

            # Check task-level condition
            if task.condition is not None:
                # Get the result to check (from condition.task_id or last dependency)
                check_task_id = task.condition.task_id or (task.depends_on[-1] if task.depends_on else None)
                if check_task_id:
                    check_result = self.tasks.get(check_task_id, Task(id="", prompt="")).result or ""
                    if not task.condition.evaluate(check_result, all_results):
                        task.status = TaskStatus.SKIPPED
                        continue

            ready.append(task)

        return ready

    def _is_dependency_satisfied(self, dep_id: str) -> bool:
        """Check if a dependency is satisfied."""
        if dep_id in self.tasks:
            return self.tasks[dep_id].status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
        if dep_id in self.conditionals:
            return self.conditionals[dep_id].status == TaskStatus.COMPLETED
        if dep_id in self.loops:
            return self.loops[dep_id].status in (LoopStatus.COMPLETED, LoopStatus.MAX_ITERATIONS)
        return False

    def get_ready_conditionals(self) -> list[ConditionalBranch]:
        """Get conditionals ready to evaluate."""
        if self.halted:
            return []

        ready = []
        for cond in self.conditionals.values():
            if cond.status != TaskStatus.PENDING:
                continue

            deps_satisfied = all(
                self._is_dependency_satisfied(dep)
                for dep in cond.depends_on
            )
            if deps_satisfied:
                ready.append(cond)

        return ready

    def get_ready_loops(self) -> list[Loop]:
        """Get loops ready to start or continue."""
        if self.halted:
            return []

        ready = []
        for loop in self.loops.values():
            if loop.status in (LoopStatus.COMPLETED, LoopStatus.MAX_ITERATIONS, LoopStatus.FAILED):
                continue

            if loop.status == LoopStatus.PENDING:
                # Check if deps satisfied to start
                deps_satisfied = all(
                    self._is_dependency_satisfied(dep)
                    for dep in loop.depends_on
                )
                if deps_satisfied:
                    ready.append(loop)
            elif loop.status == LoopStatus.RUNNING:
                # Loop is in progress
                ready.append(loop)

        return ready

    def get_ready_early_exits(self) -> list[EarlyExit]:
        """Get early exit conditions ready to evaluate."""
        if self.halted:
            return []

        ready = []
        for exit_cond in self.early_exits.values():
            if exit_cond.status != TaskStatus.PENDING:
                continue

            deps_satisfied = all(
                self._is_dependency_satisfied(dep)
                for dep in exit_cond.depends_on
            )
            if deps_satisfied:
                ready.append(exit_cond)

        return ready

    def evaluate_conditional(self, cond: ConditionalBranch) -> str:
        """Evaluate a conditional and enable appropriate tasks. Returns 'true' or 'false'."""
        all_results = self.get_all_results()

        # Get result to check
        check_task_id = cond.condition.task_id or (cond.depends_on[-1] if cond.depends_on else None)
        check_result = ""
        if check_task_id and check_task_id in self.tasks:
            check_result = self.tasks[check_task_id].result or ""

        branch_taken = "true" if cond.condition.evaluate(check_result, all_results) else "false"
        cond.branch_taken = branch_taken
        cond.status = TaskStatus.COMPLETED

        # Skip tasks on the branch not taken
        tasks_to_skip = cond.if_false if branch_taken == "true" else cond.if_true
        for task_id in tasks_to_skip:
            if task_id in self.tasks:
                self.tasks[task_id].status = TaskStatus.SKIPPED

        return branch_taken

    def evaluate_early_exit(self, exit_cond: EarlyExit) -> bool:
        """Evaluate early exit condition. Returns True if workflow should halt."""
        all_results = self.get_all_results()

        check_task_id = exit_cond.condition.task_id or (exit_cond.depends_on[-1] if exit_cond.depends_on else None)
        check_result = ""
        if check_task_id and check_task_id in self.tasks:
            check_result = self.tasks[check_task_id].result or ""

        if exit_cond.condition.evaluate(check_result, all_results):
            exit_cond.triggered = True
            exit_cond.status = TaskStatus.COMPLETED
            self.halted = True
            self.halt_reason = exit_cond.message
            return True

        exit_cond.status = TaskStatus.COMPLETED
        return False

    def get_failed_dependents(self, failed_task_id: str) -> list[Task]:
        """Get all tasks that transitively depend on a failed task."""
        dependents = []

        def collect_dependents(task_id: str) -> None:
            for task in self.tasks.values():
                if task_id in task.depends_on and task not in dependents:
                    dependents.append(task)
                    collect_dependents(task.id)

        collect_dependents(failed_task_id)
        return dependents

    def resolve_prompt(
        self,
        task: Task,
        *,
        loop_context: dict[str, Any] | None = None
    ) -> str:
        """Resolve template variables in prompt with dependency outputs."""
        prompt = task.prompt

        # Standard task result substitution
        for dep_id in task.depends_on:
            if dep_id in self.tasks:
                dep_task = self.tasks[dep_id]
                if dep_task.result is not None:
                    prompt = prompt.replace(f"{{{dep_id}}}", dep_task.result)

        # Also allow any completed task's result
        for task_id, t in self.tasks.items():
            if t.result is not None:
                prompt = prompt.replace(f"{{{task_id}}}", t.result)

        # Loop context substitution
        if loop_context:
            prompt = prompt.replace("{loop.iteration}", str(loop_context.get("iteration", 0)))
            prompt = prompt.replace("{loop.item}", str(loop_context.get("item", "")))
            prompt = prompt.replace("{loop.previous}", str(loop_context.get("previous", "")))
            prompt = prompt.replace("{loop.index}", str(loop_context.get("iteration", 0)))

        return prompt

    def is_complete(self) -> bool:
        """Check if all tasks are in a terminal state."""
        if self.halted:
            return True

        tasks_done = all(
            task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
            for task in self.tasks.values()
        )

        conditionals_done = all(
            cond.status == TaskStatus.COMPLETED
            for cond in self.conditionals.values()
        )

        loops_done = all(
            loop.status in (LoopStatus.COMPLETED, LoopStatus.MAX_ITERATIONS, LoopStatus.FAILED)
            for loop in self.loops.values()
        )

        exits_done = all(
            exit_cond.status == TaskStatus.COMPLETED
            for exit_cond in self.early_exits.values()
        )

        return tasks_done and conditionals_done and loops_done and exits_done

    def summary(self) -> dict[str, Any]:
        """Generate execution summary."""
        by_status: dict[str, list[str]] = {}
        for task in self.tasks.values():
            status_name = task.status.value
            if status_name not in by_status:
                by_status[status_name] = []
            by_status[status_name].append(task.id)

        return {
            "total": len(self.tasks),
            "by_status": by_status,
            "all_succeeded": all(
                t.status == TaskStatus.COMPLETED for t in self.tasks.values()
            ),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "loops": {
                loop_id: {
                    "iterations": loop.current_iteration,
                    "status": loop.status.value,
                }
                for loop_id, loop in self.loops.items()
            },
            "conditionals": {
                cond_id: cond.branch_taken
                for cond_id, cond in self.conditionals.items()
            },
        }


def parse_condition(spec: dict[str, Any]) -> Condition:
    """Parse a condition from spec."""
    cond_type = spec.get("type", "contains")
    return Condition(
        type=cond_type,
        value=spec.get("value"),
        task_id=spec.get("task_id"),
    )


def parse_workflow(spec: dict[str, Any]) -> TaskGraph:
    """
    Parse a workflow specification into a TaskGraph.
    """
    graph = TaskGraph()

    if spec is None:
        raise ValueError("Workflow spec is None")

    # First pass: add conditionals
    conditionals_specs = spec.get("conditionals") or []
    for cond_spec in conditionals_specs:
        condition = parse_condition(cond_spec["condition"])
        graph.add_conditional(
            cond_id=cond_spec["id"],
            condition=condition,
            if_true=cond_spec.get("if_true") or [],
            if_false=cond_spec.get("if_false") or [],
            depends_on=cond_spec.get("depends_on") or [],
        )

    # Second pass: add loops
    loops_specs = spec.get("loops") or []
    for loop_spec in loops_specs:
        loop_type = loop_spec.get("type", "until")

        condition = None
        if "condition" in loop_spec:
            condition = parse_condition(loop_spec["condition"])

        config = LoopConfig(
            type=loop_type,
            condition=condition,
            count=loop_spec.get("count"),
            items_from=loop_spec.get("items_from"),
            item_separator=loop_spec.get("item_separator") or "\n",
            max_iterations=loop_spec.get("max_iterations") or 10,
        )

        # Parse tasks inside the loop
        loop_tasks = []
        for task_spec in (loop_spec.get("tasks") or []):
            condition = None
            if "condition" in task_spec:
                condition = parse_condition(task_spec["condition"])

            loop_tasks.append(Task(
                id=task_spec["id"],
                prompt=task_spec["prompt"],
                engine=task_spec.get("engine"),
                depends_on=task_spec.get("depends_on") or [],
                condition=condition,
            ))

        graph.add_loop(
            loop_id=loop_spec["id"],
            config=config,
            tasks=loop_tasks,
            depends_on=loop_spec.get("depends_on") or [],
        )

    # Third pass: add all tasks (sorted by task dependencies only)
    task_specs = spec.get("tasks") or []
    if not isinstance(task_specs, list):
        raise ValueError(f"'tasks' must be a list, got {type(task_specs).__name__}")

    task_ids = {task_spec["id"] for task_spec in task_specs if isinstance(task_spec, dict)}
    added: set[str] = set()
    pending = list(task_specs)

    max_iterations = len(pending) * 2 + 10
    iterations = 0

    while pending and iterations < max_iterations:
        iterations += 1
        for task_spec in list(pending):
            task_id = task_spec["id"]
            deps = task_spec.get("depends_on") or []
            task_deps = [dep for dep in deps if dep in task_ids]

            if all(dep in added for dep in task_deps) or (not task_deps):
                condition = None
                if "condition" in task_spec:
                    condition = parse_condition(task_spec["condition"])

                graph.add_task(
                    task_id=task_id,
                    prompt=task_spec["prompt"],
                    engine=task_spec.get("engine"),
                    depends_on=deps,
                    project=task_spec.get("project"),
                    branch=task_spec.get("branch"),
                    condition=condition,
                )
                added.add(task_id)
                pending.remove(task_spec)

    if pending:
        unresolved = [t["id"] for t in pending]
        raise ValueError(f"Circular or unresolved dependencies: {unresolved}")

    # Fourth pass: add early exits
    early_exits_specs = spec.get("early_exits") or []
    for exit_spec in early_exits_specs:
        condition = parse_condition(exit_spec["condition"])
        graph.add_early_exit(
            exit_id=exit_spec["id"],
            condition=condition,
            depends_on=exit_spec.get("depends_on") or [],
            message=exit_spec.get("message") or "Workflow halted",
        )

    return graph
