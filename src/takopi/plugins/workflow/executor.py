"""Orchestration executor - runs task graphs using Takopi's infrastructure."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from .graph import (
    TaskGraph, Task, TaskStatus,
    Loop, LoopStatus, LoopConfig,
    ConditionalBranch, EarlyExit, Condition
)
from .checkpoint import (
    WorkflowCheckpoint,
    TaskCheckpoint,
    CheckpointStore,
    checkpoint_from_graph,
    generate_workflow_id,
)

if TYPE_CHECKING:
    from takopi.api import CommandExecutor, RunRequest, RunResult


@dataclass
class ExecutionConfig:
    """Configuration for orchestration execution."""

    # Max concurrent tasks (None = unlimited)
    max_parallel: int | None = None

    # Whether to continue executing unaffected tasks when one fails
    continue_on_failure: bool = True

    # Max retries per task
    max_retries: int = 0

    # Default engine if task doesn't specify one
    default_engine: str | None = None

    # Checkpointing
    checkpoint_store: CheckpointStore | None = None
    checkpoint_interval: int = 1  # Save after every N tasks complete

    # Progress callbacks
    on_task_start: Callable[[Task], Awaitable[None]] | None = None
    on_task_complete: Callable[[Task], Awaitable[None]] | None = None
    on_progress: Callable[[TaskGraph, WorkflowCheckpoint], Awaitable[None]] | None = None
    on_loop_iteration: Callable[[Loop, int], Awaitable[None]] | None = None
    on_conditional: Callable[[ConditionalBranch, str], Awaitable[None]] | None = None
    on_early_exit: Callable[[EarlyExit], Awaitable[None]] | None = None


@dataclass
class ExecutionResult:
    """Result of workflow execution."""
    graph: TaskGraph
    checkpoint: WorkflowCheckpoint
    workflow_id: str
    completed: bool
    cancelled: bool = False
    error: str | None = None


@dataclass
class OrchestrationExecutor:
    """
    Executes a TaskGraph using Takopi's CommandExecutor.

    Supports:
      - Parallel task execution
      - Conditional branching
      - Loops (until, while, times, foreach)
      - Early exit conditions
      - Checkpointing and resume
    """

    executor: "CommandExecutor"
    config: ExecutionConfig = field(default_factory=ExecutionConfig)

    # Runtime state
    _workflow_id: str | None = None
    _spec: dict[str, Any] | None = None
    _checkpoint: WorkflowCheckpoint | None = None
    _tasks_since_checkpoint: int = 0
    _cancelled: bool = False

    async def run(
        self,
        graph: TaskGraph,
        *,
        spec: dict[str, Any] | None = None,
        workflow_id: str | None = None,
        resume_checkpoint: WorkflowCheckpoint | None = None,
    ) -> ExecutionResult:
        """
        Execute all tasks in the graph respecting dependencies.

        Returns ExecutionResult with the graph, checkpoint, and completion status.
        """
        # Initialize workflow tracking
        self._workflow_id = workflow_id or generate_workflow_id()
        self._spec = spec or {}
        self._cancelled = False
        self._tasks_since_checkpoint = 0

        # Initialize or restore checkpoint
        if resume_checkpoint:
            self._checkpoint = resume_checkpoint
            self._checkpoint.status = "running"
        else:
            self._checkpoint = checkpoint_from_graph(
                graph,
                self._spec,
                self._workflow_id,
                status="running",
            )
            self._checkpoint.started_at = time.time()
            self._checkpoint.status = "running"

        self._save_checkpoint()

        error: str | None = None

        try:
            while not graph.is_complete() and not self._cancelled:
                # 1. Check early exit conditions
                for exit_cond in graph.get_ready_early_exits():
                    if graph.evaluate_early_exit(exit_cond):
                        if self.config.on_early_exit:
                            await self.config.on_early_exit(exit_cond)
                        break

                if graph.halted:
                    self._checkpoint.status = "halted"
                    self._checkpoint.halt_reason = graph.halt_reason
                    break

                # 2. Evaluate ready conditionals
                for cond in graph.get_ready_conditionals():
                    branch = graph.evaluate_conditional(cond)
                    self._checkpoint.conditionals[cond.id] = branch
                    if self.config.on_conditional:
                        await self.config.on_conditional(cond, branch)

                # 3. Process ready loops
                for loop in graph.get_ready_loops():
                    await self._process_loop(graph, loop)

                # 4. Execute ready tasks
                ready_tasks = graph.get_ready_tasks()

                if not ready_tasks and not graph.get_ready_loops():
                    # No tasks ready and no loops to process = done or deadlock
                    break

                if ready_tasks:
                    await self._execute_tasks(graph, ready_tasks)

                # Progress callback with checkpoint
                if self.config.on_progress:
                    await self.config.on_progress(graph, self._checkpoint)

        except Exception as e:
            error = str(e)
            self._checkpoint.status = "failed"
            self._save_checkpoint()
            raise

        # Finalize
        if self._cancelled:
            self._checkpoint.status = "cancelled"
        elif graph.halted:
            self._checkpoint.status = "halted"
        elif graph.is_complete():
            self._checkpoint.status = "completed"
            self._checkpoint.completed_at = time.time()

        self._save_checkpoint()

        return ExecutionResult(
            graph=graph,
            checkpoint=self._checkpoint,
            workflow_id=self._workflow_id,
            completed=graph.is_complete(),
            cancelled=self._cancelled,
            error=error,
        )

    def cancel(self) -> None:
        """Request cancellation of the running workflow."""
        self._cancelled = True

    def _save_checkpoint(self) -> None:
        """Save current checkpoint to store."""
        if self.config.checkpoint_store and self._checkpoint:
            self.config.checkpoint_store.save(self._checkpoint)

    def _update_checkpoint_from_graph(self, graph: TaskGraph) -> None:
        """Update checkpoint with current graph state."""
        if not self._checkpoint:
            return

        completed = 0
        failed = 0
        skipped = 0

        for task_id, task in graph.tasks.items():
            if task_id not in self._checkpoint.tasks:
                self._checkpoint.tasks[task_id] = TaskCheckpoint(
                    id=task_id,
                    status=task.status.value,
                    engine=task.engine,
                )

            cp = self._checkpoint.tasks[task_id]
            cp.status = task.status.value
            cp.result = task.result
            cp.error = task.error
            cp.resume_token = task.resume_token

            if task.status == TaskStatus.COMPLETED:
                completed += 1
            elif task.status == TaskStatus.FAILED:
                failed += 1
            elif task.status == TaskStatus.SKIPPED:
                skipped += 1

        self._checkpoint.completed_tasks = completed
        self._checkpoint.failed_tasks = failed
        self._checkpoint.skipped_tasks = skipped
        self._checkpoint.total_tasks = len(graph.tasks)

    async def _execute_tasks(
        self,
        graph: TaskGraph,
        tasks: list[Task],
        loop_context: dict[str, Any] | None = None,
    ) -> list[Task]:
        """Execute a batch of tasks in parallel."""
        from takopi.api import RunRequest

        # Apply parallelism limit
        if self.config.max_parallel is not None:
            tasks = tasks[:self.config.max_parallel]

        # Mark tasks as running
        for task in tasks:
            task.status = TaskStatus.RUNNING
            if task.id in self._checkpoint.tasks:
                self._checkpoint.tasks[task.id].status = "running"
                self._checkpoint.tasks[task.id].started_at = time.time()
            if self.config.on_task_start:
                await self.config.on_task_start(task)

        # Build run requests
        requests: list[RunRequest] = []
        task_map: dict[int, Task] = {}

        for i, task in enumerate(tasks):
            resolved_prompt = graph.resolve_prompt(task, loop_context=loop_context)
            engine = task.engine or self.config.default_engine

            # Build context if project/branch specified
            context = None
            if task.project or task.branch:
                from takopi.api import RunContext
                context = RunContext(project=task.project, branch=task.branch)

            request = RunRequest(
                prompt=resolved_prompt,
                engine=engine,
                context=context,
            )
            requests.append(request)
            task_map[i] = task

        # Execute in parallel
        results = await self.executor.run_many(
            requests,
            mode="capture",
            parallel=True,
        )

        # Process results
        for i, result in enumerate(results):
            task = task_map[i]
            await self._handle_result(task, result, graph)

            # Update checkpoint for this task
            if task.id in self._checkpoint.tasks:
                cp = self._checkpoint.tasks[task.id]
                cp.completed_at = time.time()
                if cp.started_at:
                    cp.duration_s = cp.completed_at - cp.started_at

            self._tasks_since_checkpoint += 1

            if self.config.on_task_complete:
                await self.config.on_task_complete(task)

        # Update and maybe save checkpoint
        self._update_checkpoint_from_graph(graph)

        if self._tasks_since_checkpoint >= self.config.checkpoint_interval:
            self._save_checkpoint()
            self._tasks_since_checkpoint = 0

        return tasks

    async def _handle_result(
        self,
        task: Task,
        result: "RunResult",
        graph: TaskGraph,
    ) -> None:
        """Process a task result and update graph state."""
        # RunResult only has: engine, message (RenderedMessage | None)
        # message present = success, message None = failure
        if result.message is not None:
            task.status = TaskStatus.COMPLETED
            task.result = result.message.text
        else:
            task.status = TaskStatus.FAILED
            task.error = "Task returned no output"

            if not self.config.continue_on_failure:
                for dependent in graph.get_failed_dependents(task.id):
                    dependent.status = TaskStatus.SKIPPED
                    dependent.error = f"Skipped due to failed dependency: {task.id}"

    async def _process_loop(self, graph: TaskGraph, loop: Loop) -> None:
        """Process one iteration of a loop, or initialize it."""

        if loop.status == LoopStatus.PENDING:
            # Initialize the loop
            loop.status = LoopStatus.RUNNING
            loop.current_iteration = 0

            # For foreach, extract items
            if loop.config.type == "foreach" and loop.config.items_from:
                source_result = graph.tasks.get(loop.config.items_from)
                if source_result and source_result.result:
                    items_text = source_result.result
                    loop.items = [
                        item.strip()
                        for item in items_text.split(loop.config.item_separator)
                        if item.strip()
                    ]

        # Check termination conditions
        should_continue = self._should_loop_continue(graph, loop)

        if not should_continue:
            if loop.current_iteration >= loop.config.max_iterations:
                loop.status = LoopStatus.MAX_ITERATIONS
            else:
                loop.status = LoopStatus.COMPLETED

            # Set final result from last iteration
            if loop.iterations:
                last_iter = loop.iterations[-1]
                if last_iter.get("results"):
                    # Get last task's result from last iteration
                    loop.final_result = list(last_iter["results"].values())[-1]
            return

        # Execute one iteration
        if self.config.on_loop_iteration:
            await self.config.on_loop_iteration(loop, loop.current_iteration)

        # Build loop context for template substitution
        loop_context = {
            "iteration": loop.current_iteration,
            "item": loop.items[loop.current_iteration] if loop.items else "",
            "previous": "",
        }

        # Get previous iteration's last result
        if loop.iterations:
            last_results = loop.iterations[-1].get("results", {})
            if last_results:
                loop_context["previous"] = list(last_results.values())[-1]

        # Create fresh copies of loop tasks for this iteration
        iter_tasks = []
        for task_template in loop.tasks:
            task_copy = Task(
                id=f"{loop.id}.{loop.current_iteration}.{task_template.id}",
                prompt=task_template.prompt,
                engine=task_template.engine,
                depends_on=[
                    f"{loop.id}.{loop.current_iteration}.{dep}"
                    for dep in task_template.depends_on
                ],
                condition=task_template.condition,
            )
            iter_tasks.append(task_copy)
            graph.tasks[task_copy.id] = task_copy

        # Execute loop tasks (they may have internal dependencies)
        pending_loop_tasks = list(iter_tasks)
        while pending_loop_tasks:
            ready = [
                t for t in pending_loop_tasks
                if t.status == TaskStatus.PENDING and all(
                    graph.tasks.get(dep, Task(id="", prompt="", status=TaskStatus.COMPLETED)).status == TaskStatus.COMPLETED
                    for dep in t.depends_on
                )
            ]

            if not ready:
                break

            await self._execute_tasks(graph, ready, loop_context=loop_context)
            pending_loop_tasks = [t for t in pending_loop_tasks if t.status == TaskStatus.PENDING]

        # Record iteration results
        iter_results = {
            task.id.split(".")[-1]: task.result
            for task in iter_tasks
            if task.result
        }
        loop.iterations.append({
            "iteration": loop.current_iteration,
            "results": iter_results,
            "context": loop_context,
        })

        loop.current_iteration += 1

    def _should_loop_continue(self, graph: TaskGraph, loop: Loop) -> bool:
        """Determine if a loop should continue."""
        config = loop.config

        # Check max iterations
        if loop.current_iteration >= config.max_iterations:
            return False

        if config.type == "times":
            return loop.current_iteration < (config.count or 0)

        elif config.type == "foreach":
            return loop.current_iteration < len(loop.items)

        elif config.type == "until":
            # Continue until condition is TRUE
            if loop.current_iteration == 0:
                return True  # Always run at least once

            if config.condition and loop.iterations:
                last_results = loop.iterations[-1].get("results", {})
                last_result = list(last_results.values())[-1] if last_results else ""
                return not config.condition.evaluate(last_result, graph.get_all_results())
            return True

        elif config.type == "while":
            # Continue while condition is TRUE
            if config.condition:
                if loop.current_iteration == 0:
                    # Check initial condition against dependencies
                    dep_results = {
                        dep: graph.tasks.get(dep, Task(id="", prompt="")).result or ""
                        for dep in loop.depends_on
                    }
                    check_result = list(dep_results.values())[-1] if dep_results else ""
                    return config.condition.evaluate(check_result, graph.get_all_results())
                else:
                    last_results = loop.iterations[-1].get("results", {})
                    last_result = list(last_results.values())[-1] if last_results else ""
                    return config.condition.evaluate(last_result, graph.get_all_results())
            return False

        return False


async def run_workflow(
    executor: "CommandExecutor",
    spec: dict[str, Any],
    *,
    config: ExecutionConfig | None = None,
    workflow_id: str | None = None,
) -> ExecutionResult:
    """
    Convenience function to parse and run a workflow spec.

    Returns ExecutionResult with graph, checkpoint, and status.
    """
    from .graph import parse_workflow

    graph = parse_workflow(spec)
    cfg = config or ExecutionConfig()
    orchestrator = OrchestrationExecutor(executor=executor, config=cfg)

    return await orchestrator.run(graph, spec=spec, workflow_id=workflow_id)


async def resume_workflow(
    executor: "CommandExecutor",
    checkpoint: WorkflowCheckpoint,
    *,
    config: ExecutionConfig | None = None,
) -> ExecutionResult:
    """
    Resume a workflow from a checkpoint.

    Returns ExecutionResult with updated graph and checkpoint.
    """
    from .checkpoint import restore_graph_from_checkpoint

    graph = restore_graph_from_checkpoint(checkpoint)
    cfg = config or ExecutionConfig()
    orchestrator = OrchestrationExecutor(executor=executor, config=cfg)

    return await orchestrator.run(
        graph,
        spec=checkpoint.spec,
        workflow_id=checkpoint.workflow_id,
        resume_checkpoint=checkpoint,
    )
