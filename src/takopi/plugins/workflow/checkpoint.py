"""Checkpoint and persistence for workflow state."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Protocol
import hashlib
import uuid


def generate_workflow_id() -> str:
    """Generate a short, memorable workflow ID."""
    return uuid.uuid4().hex[:8]


def resolve_workflow_dir(config_path: Path | None) -> Path:
    """Resolve workflow checkpoint directory from a config path."""
    if config_path is None:
        return Path.home() / ".takopi" / "workflows"
    return config_path.parent / "takopi_workflows"


def hash_spec(spec: dict[str, Any]) -> str:
    """Generate a hash of the workflow spec for identity."""
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


@dataclass
class TaskCheckpoint:
    """Checkpoint state for a single task."""
    id: str
    status: str  # pending, running, completed, failed, skipped
    result: str | None = None
    error: str | None = None
    resume_token: str | None = None
    engine: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    duration_s: float | None = None


@dataclass
class LoopCheckpoint:
    """Checkpoint state for a loop."""
    id: str
    status: str  # pending, running, completed, max_iterations, failed
    current_iteration: int = 0
    iterations: list[dict[str, Any]] = field(default_factory=list)
    items: list[str] = field(default_factory=list)


@dataclass
class WorkflowCheckpoint:
    """Complete checkpoint of workflow execution state."""

    # Identity
    workflow_id: str
    spec_hash: str
    spec: dict[str, Any]

    # Timing
    created_at: float
    updated_at: float
    started_at: float | None = None
    completed_at: float | None = None

    # Optional metadata
    name: str | None = None

    # Status
    status: str = "pending"  # pending, running, completed, failed, halted, cancelled
    halt_reason: str | None = None

    # Task state
    tasks: dict[str, TaskCheckpoint] = field(default_factory=dict)
    loops: dict[str, LoopCheckpoint] = field(default_factory=dict)
    conditionals: dict[str, str | None] = field(default_factory=dict)  # id -> branch_taken

    # Metrics
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    skipped_tasks: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "workflow_id": self.workflow_id,
            "spec_hash": self.spec_hash,
            "spec": self.spec,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "halt_reason": self.halt_reason,
            "tasks": {k: asdict(v) for k, v in self.tasks.items()},
            "loops": {k: asdict(v) for k, v in self.loops.items()},
            "conditionals": self.conditionals,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "skipped_tasks": self.skipped_tasks,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowCheckpoint":
        """Reconstruct from dict."""
        tasks = {
            k: TaskCheckpoint(**v)
            for k, v in data.get("tasks", {}).items()
        }
        loops = {
            k: LoopCheckpoint(**v)
            for k, v in data.get("loops", {}).items()
        }
        return cls(
            workflow_id=data["workflow_id"],
            spec_hash=data["spec_hash"],
            spec=data["spec"],
            name=data.get("name"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            status=data.get("status", "pending"),
            halt_reason=data.get("halt_reason"),
            tasks=tasks,
            loops=loops,
            conditionals=data.get("conditionals", {}),
            total_tasks=data.get("total_tasks", 0),
            completed_tasks=data.get("completed_tasks", 0),
            failed_tasks=data.get("failed_tasks", 0),
            skipped_tasks=data.get("skipped_tasks", 0),
        )

    def progress_summary(self) -> str:
        """Human-readable progress summary."""
        if self.status == "pending":
            return "⏳ Pending"

        if self.status == "running":
            pct = (self.completed_tasks / self.total_tasks * 100) if self.total_tasks else 0
            return f"🔄 Running: {self.completed_tasks}/{self.total_tasks} ({pct:.0f}%)"

        if self.status == "completed":
            return f"✅ Completed: {self.completed_tasks} tasks"

        if self.status == "failed":
            return f"❌ Failed: {self.failed_tasks} failed, {self.completed_tasks} completed"

        if self.status == "halted":
            return f"⛔ Halted: {self.halt_reason}"

        if self.status == "cancelled":
            return f"🚫 Cancelled at {self.completed_tasks}/{self.total_tasks}"

        return f"❓ {self.status}"

    def task_list_summary(self) -> str:
        """Detailed task status list."""
        lines = []

        status_emoji = {
            "pending": "⏳",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
            "skipped": "⏭️",
        }

        for task_id, task in self.tasks.items():
            emoji = status_emoji.get(task.status, "❓")
            duration = f" ({task.duration_s:.1f}s)" if task.duration_s else ""
            engine = f" [{task.engine}]" if task.engine else ""
            lines.append(f"- {emoji} {task_id}{engine}{duration}")

            if task.error:
                lines.append(f"  Error: {task.error[:50]}...")

        return "\n".join(lines)


class CheckpointStore(Protocol):
    """Protocol for checkpoint storage backends."""

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        """Save checkpoint."""
        ...

    def load(self, workflow_id: str) -> WorkflowCheckpoint | None:
        """Load checkpoint by ID."""
        ...

    def list_recent(self, limit: int = 10) -> list[WorkflowCheckpoint]:
        """List recent workflows."""
        ...

    def list_all(self) -> list[WorkflowCheckpoint]:
        """List all workflows."""
        ...

    def delete(self, workflow_id: str) -> bool:
        """Delete a checkpoint."""
        ...


class FileCheckpointStore:
    """File-based checkpoint storage."""

    def __init__(self, base_dir: Path | str | None = None):
        if base_dir is None:
            base_dir = Path.home() / ".takopi" / "workflows"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, workflow_id: str) -> Path:
        return self.base_dir / f"{workflow_id}.json"

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        checkpoint.updated_at = time.time()
        path = self._path_for(checkpoint.workflow_id)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(checkpoint.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)

    def load(self, workflow_id: str) -> WorkflowCheckpoint | None:
        path = self._path_for(workflow_id)
        if not path.exists():
            return None
        with open(path) as f:
            data = json.load(f)
        return WorkflowCheckpoint.from_dict(data)

    def list_recent(self, limit: int = 10) -> list[WorkflowCheckpoint]:
        """List recent workflows, sorted by updated_at descending."""
        checkpoints = self.list_all()
        checkpoints.sort(key=lambda c: c.updated_at, reverse=True)
        return checkpoints[:limit]

    def list_all(self) -> list[WorkflowCheckpoint]:
        """List all workflows."""
        checkpoints = []
        for path in self.base_dir.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                checkpoints.append(WorkflowCheckpoint.from_dict(data))
            except Exception:
                continue
        return checkpoints

    def delete(self, workflow_id: str) -> bool:
        path = self._path_for(workflow_id)
        if path.exists():
            path.unlink()
            return True
        return False


def create_checkpoint(
    spec: dict[str, Any],
    workflow_id: str | None = None,
) -> WorkflowCheckpoint:
    """Create a new checkpoint from a workflow spec."""
    from .graph import parse_workflow

    wf_id = workflow_id or generate_workflow_id()
    now = time.time()
    graph = parse_workflow(spec)

    checkpoint = checkpoint_from_graph(graph, spec, wf_id, status="pending")
    checkpoint.created_at = now
    checkpoint.updated_at = now
    checkpoint.started_at = None
    checkpoint.completed_at = None
    checkpoint.status = "pending"
    checkpoint.halt_reason = None
    checkpoint.name = spec.get("name")
    return checkpoint


def checkpoint_from_graph(
    graph: "TaskGraph",
    spec: dict[str, Any],
    workflow_id: str,
    status: str = "running",
) -> WorkflowCheckpoint:
    """Create checkpoint from current graph state."""
    from .graph import TaskGraph, TaskStatus, LoopStatus

    now = time.time()

    tasks = {}
    completed = 0
    failed = 0
    skipped = 0

    for task_id, task in graph.tasks.items():
        tasks[task_id] = TaskCheckpoint(
            id=task_id,
            status=task.status.value,
            result=task.result,
            error=task.error,
            resume_token=task.resume_token,
            engine=task.engine,
        )

        if task.status == TaskStatus.COMPLETED:
            completed += 1
        elif task.status == TaskStatus.FAILED:
            failed += 1
        elif task.status == TaskStatus.SKIPPED:
            skipped += 1

    loops = {}
    for loop_id, loop in graph.loops.items():
        loops[loop_id] = LoopCheckpoint(
            id=loop_id,
            status=loop.status.value,
            current_iteration=loop.current_iteration,
            iterations=loop.iterations,
            items=loop.items,
        )

    conditionals = {
        cond_id: cond.branch_taken
        for cond_id, cond in graph.conditionals.items()
    }

    return WorkflowCheckpoint(
        workflow_id=workflow_id,
        spec_hash=hash_spec(spec),
        spec=spec,
        name=spec.get("name"),
        created_at=now,  # Will be overwritten if loading existing
        updated_at=now,
        status=status,
        halt_reason=graph.halt_reason,
        tasks=tasks,
        loops=loops,
        conditionals=conditionals,
        total_tasks=len(graph.tasks),
        completed_tasks=completed,
        failed_tasks=failed,
        skipped_tasks=skipped,
    )


def restore_graph_from_checkpoint(
    checkpoint: WorkflowCheckpoint,
) -> "TaskGraph":
    """Restore a TaskGraph from checkpoint state."""
    from .graph import parse_workflow, TaskStatus, LoopStatus

    # Parse the original spec
    graph = parse_workflow(checkpoint.spec)

    # Restore task states
    for task_id, task_cp in checkpoint.tasks.items():
        if task_id in graph.tasks:
            task = graph.tasks[task_id]
            task.status = TaskStatus(task_cp.status)
            task.result = task_cp.result
            task.error = task_cp.error
            task.resume_token = task_cp.resume_token

    # Restore loop states
    for loop_id, loop_cp in checkpoint.loops.items():
        if loop_id in graph.loops:
            loop = graph.loops[loop_id]
            loop.status = LoopStatus(loop_cp.status)
            loop.current_iteration = loop_cp.current_iteration
            loop.iterations = loop_cp.iterations
            loop.items = loop_cp.items

    # Restore conditional states
    for cond_id, branch_taken in checkpoint.conditionals.items():
        if cond_id in graph.conditionals:
            cond = graph.conditionals[cond_id]
            cond.branch_taken = branch_taken
            if branch_taken is not None:
                cond.status = TaskStatus.COMPLETED

    # Restore halt state
    if checkpoint.status == "halted":
        graph.halted = True
        graph.halt_reason = checkpoint.halt_reason

    return graph
