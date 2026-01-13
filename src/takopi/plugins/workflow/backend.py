"""Takopi command backend for workflow orchestration."""

from __future__ import annotations

import json
import re
import yaml
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from takopi.api import CommandContext, CommandResult

from .graph import parse_workflow, TaskGraph, TaskStatus
from .executor import ExecutionConfig, run_workflow, resume_workflow
from .checkpoint import (
    FileCheckpointStore,
    WorkflowCheckpoint,
    resolve_workflow_dir,
    restore_graph_from_checkpoint,
)


def get_checkpoint_store(config_path: Path | None) -> FileCheckpointStore:
    """Get the default checkpoint store."""
    return FileCheckpointStore(base_dir=resolve_workflow_dir(config_path))


class WorkflowCommand:
    """
    Command plugin that enables multi-task workflow orchestration.

    Commands:
        /workflow <spec>           - Run a new workflow
        /workflow status [id]      - Show status of a workflow
        /workflow list             - List recent workflows
        /workflow resume <id>      - Resume a paused/failed workflow
        /workflow cancel <id>      - Cancel a running workflow
        /workflow result <id>      - Show full results of a workflow
        /workflow prune            - Delete successful workflows
        /workflow help             - Show help

    Example workflow (YAML):
        tasks:
          - id: research
            prompt: Research quantum computing basics
            engine: claude
          - id: eli5
            prompt: "Explain this like I'm 5: {research}"
            depends_on: [research]
            engine: codex
    """

    id = "workflow"
    description = "Execute multi-task workflows with dependencies"

    async def handle(self, ctx: "CommandContext") -> "CommandResult | None":
        args = ctx.args_text.strip()

        # Parse subcommand
        if args.startswith("status"):
            return await self._handle_status(ctx, args[6:].strip())
        elif args.startswith("list"):
            return await self._handle_list(ctx)
        elif args.startswith("resume"):
            return await self._handle_resume(ctx, args[6:].strip())
        elif args.startswith("cancel"):
            return await self._handle_cancel(ctx, args[6:].strip())
        elif args.startswith("result"):
            return await self._handle_result(ctx, args[6:].strip())
        elif args.startswith("prune"):
            return await self._handle_prune(ctx)
        elif args.startswith("help"):
            await self._send_text(ctx, self._usage_text())
            return None

        # Default: run a new workflow
        return await self._handle_run(ctx, args)

    async def _handle_run(self, ctx: "CommandContext", spec_text: str) -> "CommandResult":
        """Run a new workflow from spec."""

        # Also check if replying to a message with the spec
        if not spec_text and ctx.reply_text:
            spec_text = ctx.reply_text.strip()

        if not spec_text:
            await self._send_text(ctx, self._usage_text())
            return None

        # Parse spec
        try:
            spec = self._parse_spec(spec_text)
        except Exception as e:
            try:
                spec = await self._parse_spec_llm(ctx, spec_text)
                rendered = yaml.safe_dump(spec, sort_keys=False).strip()
                await self._send_text(
                    ctx,
                    "🧪 **Experimental parse result (LLM):**\n"
                    f"```yaml\n{rendered}\n```",
                )
            except Exception as llm_error:
                await self._send_text(
                    ctx,
                    "❌ Failed to parse workflow spec:\n"
                    f"{e}\n\nLLM parse failed:\n{llm_error}",
                )
                return None

        # Validate and build graph
        try:
            graph = parse_workflow(spec)
        except Exception as e:
            await self._send_text(ctx, f"❌ Invalid workflow:\n{e}")
            return None

        if not graph.tasks:
            await self._send_text(ctx, "❌ Workflow has no tasks. Make sure your spec has a tasks: list.")
            return None

        # Get config from plugin settings
        plugin_config = ctx.plugin_config or {}
        store = get_checkpoint_store(ctx.config_path)

        config = ExecutionConfig(
            max_parallel=plugin_config.get("max_parallel"),
            continue_on_failure=plugin_config.get("continue_on_failure", True),
            default_engine=plugin_config.get("default_engine"),
            checkpoint_store=store,
            checkpoint_interval=1,
            on_task_start=lambda t: self._notify_task_start(ctx, t),
            on_task_complete=lambda t: self._notify_task_complete(ctx, t),
        )

        # Execute
        try:
            result = await run_workflow(ctx.executor, spec, config=config)
        except Exception as e:
            await self._send_text(ctx, f"❌ Workflow execution failed:\n{e}")
            return None

        # Build final report
        await self._send_text(ctx, self._build_report(result.graph, result.checkpoint))
        return None

    async def _handle_status(self, ctx: "CommandContext", workflow_id: str) -> "CommandResult":
        """Show status of a specific workflow or the most recent one."""
        store = get_checkpoint_store(ctx.config_path)

        if workflow_id:
            checkpoint = store.load(workflow_id)
            if not checkpoint:
                await self._send_text(ctx, f"❌ Workflow {workflow_id} not found")
                return None
        else:
            recent = store.list_recent(1)
            if not recent:
                await self._send_text(ctx, "No workflows found. Run one with `/workflow <spec>`")
                return None
            checkpoint = recent[0]

        await self._send_text(ctx, self._format_status(checkpoint))
        return None

    async def _handle_list(self, ctx: "CommandContext") -> "CommandResult":
        """List recent workflows."""
        store = get_checkpoint_store(ctx.config_path)
        recent = store.list_recent(10)

        if not recent:
            await self._send_text(ctx, "No workflows found. Run one with `/workflow <spec>`")
            return None

        lines = ["## Recent Workflows\n"]
        for cp in recent:
            from datetime import datetime
            dt = datetime.fromtimestamp(cp.updated_at).strftime("%Y-%m-%d %H:%M")
            name_tag = f" — {cp.name}" if cp.name else ""
            lines.append(
                f"- **{cp.workflow_id}**{name_tag} — {cp.progress_summary()} "
                f"(_{dt}_ • {cp.total_tasks} tasks)"
            )

        lines.append("\nUse `/workflow status <id>` for details or `/workflow resume <id>` to continue")

        await self._send_text(ctx, "\n".join(lines))
        return None

    async def _handle_prune(self, ctx: "CommandContext") -> "CommandResult":
        """Delete completed workflows with no failures."""
        store = get_checkpoint_store(ctx.config_path)
        checkpoints = store.list_all()

        to_delete = [
            cp for cp in checkpoints
            if cp.status == "completed" and cp.failed_tasks == 0
        ]

        deleted = 0
        for cp in to_delete:
            if store.delete(cp.workflow_id):
                deleted += 1

        if deleted == 0:
            await self._send_text(ctx, "No successful workflows to prune.")
            return None

        await self._send_text(ctx, f"Pruned {deleted} successful workflow(s).")
        return None

    async def _handle_resume(self, ctx: "CommandContext", workflow_id: str) -> "CommandResult":
        """Resume a paused or failed workflow."""
        if not workflow_id:
            await self._send_text(ctx, "Usage: `/workflow resume <workflow_id>`\n\nUse `/workflow list` to see available workflows.")
            return None

        store = get_checkpoint_store(ctx.config_path)
        checkpoint = store.load(workflow_id)

        if not checkpoint:
            await self._send_text(ctx, f"❌ Workflow {workflow_id} not found")
            return None

        if checkpoint.status == "completed":
            await self._send_text(ctx, f"✅ Workflow {workflow_id} already completed. Nothing to resume.")
            return None

        if checkpoint.status == "running":
            await self._send_text(ctx, f"🔄 Workflow {workflow_id} is already running.")
            return None

        # Get config from plugin settings
        plugin_config = ctx.plugin_config or {}

        config = ExecutionConfig(
            max_parallel=plugin_config.get("max_parallel"),
            continue_on_failure=plugin_config.get("continue_on_failure", True),
            default_engine=plugin_config.get("default_engine"),
            checkpoint_store=store,
            checkpoint_interval=1,
        )

        # Resume execution
        try:
            result = await resume_workflow(ctx.executor, checkpoint, config=config)
        except Exception as e:
            await self._send_text(ctx, f"❌ Resume failed:\n{e}")
            return None

        await self._send_text(ctx, self._build_report(result.graph, result.checkpoint, resumed=True))
        return None

    async def _handle_cancel(self, ctx: "CommandContext", workflow_id: str) -> "CommandResult":
        """Cancel a workflow (mark as cancelled in checkpoint)."""
        if not workflow_id:
            await self._send_text(ctx, "Usage: `/workflow cancel <workflow_id>`")
            return None

        store = get_checkpoint_store(ctx.config_path)
        checkpoint = store.load(workflow_id)

        if not checkpoint:
            await self._send_text(ctx, f"❌ Workflow {workflow_id} not found")
            return None

        if checkpoint.status in ("completed", "cancelled", "halted"):
            await self._send_text(ctx, f"Workflow {workflow_id} is already {checkpoint.status}.")
            return None

        # Mark as cancelled
        checkpoint.status = "cancelled"
        store.save(checkpoint)

        await self._send_text(ctx, f"🚫 Workflow {workflow_id} marked as cancelled.\n\n{checkpoint.task_list_summary()}")
        return None

    async def _handle_result(self, ctx: "CommandContext", workflow_id: str) -> "CommandResult":
        """Show full results of a completed workflow."""
        if not workflow_id:
            await self._send_text(ctx, "Usage: `/workflow result <workflow_id>`")
            return None

        store = get_checkpoint_store(ctx.config_path)
        checkpoint = store.load(workflow_id)

        if not checkpoint:
            await self._send_text(ctx, f"❌ Workflow {workflow_id} not found")
            return None

        # Restore graph to get full results
        graph = restore_graph_from_checkpoint(checkpoint)

        await self._send_text(ctx, self._build_report(graph, checkpoint, show_full_results=True))
        return None

    def _parse_spec(self, text: str) -> dict:
        """Parse YAML or JSON spec."""
        text = text.strip()

        if not text:
            raise ValueError("Empty workflow spec")

        # Fix Telegram's non-breaking spaces (common copy-paste issue)
        text = text.replace('\xa0', ' ')  # non-breaking space → regular space
        text = text.replace('\u00a0', ' ')  # same thing, different notation
        text = text.replace('\u2003', ' ')  # em space
        text = text.replace('\u2002', ' ')  # en space
        text = text.replace('\u2007', ' ')  # figure space

        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        result = None
        yaml_error = None
        json_error = None

        try:
            result = yaml.safe_load(text)
        except yaml.YAMLError as e:
            yaml_error = e

        if result is None:
            try:
                result = json.loads(text)
            except json.JSONDecodeError as e:
                json_error = e

        if result is None:
            if yaml_error:
                raise ValueError(f"Invalid YAML: {yaml_error}")
            elif json_error:
                raise ValueError(f"Invalid JSON: {json_error}")
            else:
                raise ValueError("Spec parsed to empty/null value")

        if not isinstance(result, dict):
            raise ValueError(f"Spec must be a dict/object, got {type(result).__name__}")

        return result

    async def _parse_spec_llm(self, ctx: "CommandContext", text: str) -> dict:
        """Use an LLM to parse free-form text into a workflow spec."""
        from takopi.api import RunRequest

        prompt = (
            "Convert the user's workflow description into JSON.\n"
            "Return ONLY JSON (no code fences, no commentary).\n"
            "Schema:\n"
            "- name: optional string\n"
            "- tasks: list of tasks with id, prompt, depends_on?, engine?, project?, branch?, output?, early_exit?\n"
            "- loops: optional list with id, type (times|foreach|until|while),\n"
            "  count?, items_from?, item_separator?, depends_on?, condition?, tasks\n"
            "- condition or early_exit use: {when: \"{{ expr }}\", message?: \"...\"}\n\n"
            "User text:\n"
            f"{text.strip()}\n"
        )
        request = RunRequest(prompt=prompt, engine=None)
        result = await ctx.executor.run_one(request, mode="capture")
        if result.message is None or not result.message.text:
            raise ValueError("LLM returned no output")

        raw = result.message.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        data = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    data = None
        if data is None:
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid LLM output: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("LLM output is not an object")
        if "tasks" not in data:
            raise ValueError("LLM output missing tasks")
        self._normalize_llm_spec(data)
        return data

    def _normalize_llm_spec(self, data: dict) -> None:
        """Normalize LLM output to match Takopi prompt templating."""
        def normalize_condition(condition: dict) -> None:
            if not isinstance(condition, dict):
                return
            if "when" not in condition or "type" in condition:
                return
            raw = condition.get("when")
            if not isinstance(raw, str):
                return
            match = re.match(r"\s*\{\{\s*(.*?)\s*\}\}\s*", raw)
            expr = match.group(1) if match else raw.strip()
            condition["type"] = "expr"
            condition["value"] = expr
            condition.pop("when", None)

        def normalize_prompt(prompt: str, depends_on: list[str] | None) -> str:
            prompt = re.sub(
                r"\{\{\s*([A-Za-z0-9_.-]+)\.(?:result|output)\s*\}\}",
                r"{\1}",
                prompt,
            )
            prompt = re.sub(
                r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}",
                r"{\1}",
                prompt,
            )
            prompt = re.sub(
                r"\b([A-Za-z0-9_.-]+)\.(?:result|output)\b",
                r"{\1}",
                prompt,
            )
            if isinstance(depends_on, list) and depends_on:
                deps = [dep for dep in depends_on if isinstance(dep, str) and dep]
                missing = [
                    dep
                    for dep in deps
                    if not re.search(rf"\{{{re.escape(dep)}\}}", prompt)
                ]
                if missing:
                    context_lines = ["Context from dependencies:"]
                    context_lines.extend(f"- {dep}: {{{dep}}}" for dep in missing)
                    prompt = f"{prompt.rstrip()}\n\n" + "\n".join(context_lines)
            return prompt

        tasks = data.get("tasks")
        if not isinstance(tasks, list):
            return
        for task in tasks:
            if not isinstance(task, dict):
                continue
            prompt = task.get("prompt")
            if not isinstance(prompt, str):
                continue
            normalize_condition(task.get("condition"))
            task["prompt"] = normalize_prompt(prompt, task.get("depends_on"))

        for cond_spec in data.get("conditionals", []) or []:
            if isinstance(cond_spec, dict):
                normalize_condition(cond_spec.get("condition"))

        for loop_spec in data.get("loops", []) or []:
            if not isinstance(loop_spec, dict):
                continue
            normalize_condition(loop_spec.get("condition"))
            for loop_task in loop_spec.get("tasks") or []:
                if not isinstance(loop_task, dict):
                    continue
                loop_prompt = loop_task.get("prompt")
                if not isinstance(loop_prompt, str):
                    continue
                normalize_condition(loop_task.get("condition"))
                loop_task["prompt"] = normalize_prompt(loop_prompt, loop_task.get("depends_on"))

        for exit_spec in data.get("early_exits", []) or []:
            if isinstance(exit_spec, dict):
                normalize_condition(exit_spec.get("condition"))

    async def _notify_task_start(self, ctx: "CommandContext", task) -> None:
        """Optional: send notification when task starts."""
        pass

    async def _notify_task_complete(self, ctx: "CommandContext", task) -> None:
        """Optional: send notification when task completes."""
        pass

    def _format_status(self, checkpoint: WorkflowCheckpoint) -> str:
        """Format checkpoint as status message."""
        from datetime import datetime

        title = checkpoint.workflow_id
        if checkpoint.name:
            title = f"{checkpoint.workflow_id} — {checkpoint.name}"
        lines = [f"## Workflow {title}\n"]
        lines.append(f"**Status:** {checkpoint.progress_summary()}\n")

        created = datetime.fromtimestamp(checkpoint.created_at).strftime("%Y-%m-%d %H:%M:%S")
        updated = datetime.fromtimestamp(checkpoint.updated_at).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"**Created:** {created}")
        lines.append(f"**Updated:** {updated}\n")

        if checkpoint.halt_reason:
            lines.append(f"**Halt reason:** {checkpoint.halt_reason}\n")

        lines.append("### Tasks\n")
        lines.append(checkpoint.task_list_summary())

        # Show resume hint if applicable
        if checkpoint.status in ("failed", "cancelled"):
            lines.append(f"\n\nResume:\n`/workflow resume {checkpoint.workflow_id}`")

        return "\n".join(lines)

    def _build_report(
        self,
        graph: TaskGraph,
        checkpoint: WorkflowCheckpoint,
        resumed: bool = False,
        show_full_results: bool = False,
    ) -> str:
        """Build final execution report."""
        prefix = "Resumed " if resumed else ""
        title = checkpoint.workflow_id
        if checkpoint.name:
            title = f"{checkpoint.workflow_id} — {checkpoint.name}"
        lines = [f"# {prefix}Workflow {title}\n"]

        summary = graph.summary()

        # Overall status
        if checkpoint.status == "completed" and summary["all_succeeded"]:
            lines.append("✅ **All tasks completed successfully**\n")
        elif checkpoint.status == "completed":
            completed = len(summary["by_status"].get("completed", []))
            failed = len(summary["by_status"].get("failed", []))
            skipped = len(summary["by_status"].get("skipped", []))
            lines.append(f"⚠️ **{completed}/{summary['total']} tasks completed** ")
            lines.append(f"({failed} failed, {skipped} skipped)\n")
        elif checkpoint.status == "halted":
            lines.append(f"⛔ **Halted:** {checkpoint.halt_reason}\n")
        elif checkpoint.status == "cancelled":
            lines.append(f"🚫 **Cancelled** at {checkpoint.completed_tasks}/{checkpoint.total_tasks} tasks\n")
        elif checkpoint.status == "failed":
            lines.append(f"❌ **Failed:** {checkpoint.failed_tasks} tasks failed\n")
        else:
            lines.append(f"🔄 **{checkpoint.status.title()}**\n")

        lines.append("---\n")

        # Task results
        max_result_len = 2000 if show_full_results else 300

        for task_id, task in graph.tasks.items():
            # Skip loop iteration tasks in summary unless showing full results
            if "." in task_id and not show_full_results:
                continue

            status_emoji = {
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
                TaskStatus.SKIPPED: "⏭️",
                TaskStatus.RUNNING: "🔄",
                TaskStatus.PENDING: "⏳",
            }.get(task.status, "❓")

            engine_tag = f" ({task.engine})" if task.engine else ""
            lines.append(f"### {status_emoji} {task_id}{engine_tag}\n")

            if task.status == TaskStatus.COMPLETED and task.result:
                result = self._sanitize_task_result(task.result)
                if len(result) > max_result_len:
                    result = result[:max_result_len] + "\n\n... (truncated)"
                lines.append(f"{result}\n")

            if task.error:
                lines.append(f"**Error:** {task.error}\n")

            if task.resume_token:
                resume_engine = task.engine or "engine"
                lines.append(f"Resume: {resume_engine} resume {task.resume_token}\n")

            lines.append("")

        # Build body (everything above footer)
        body = "\n".join(lines)

        # Footer with actions (plain text to avoid Telegram markdown issues)
        footer_lines = ["---\n"]
        wid = checkpoint.workflow_id
        footer_lines.append("**Commands:**")
        commands = [f"- `/workflow status {wid}`", f"- `/workflow result {wid}`"]
        if checkpoint.status in ("failed", "cancelled"):
            commands.insert(0, f"- `/workflow resume {wid}`")
        footer_lines.extend(commands)
        if checkpoint.name:
            footer_lines.append(f"\n[ID: {wid} | Name: {checkpoint.name}]")
        else:
            footer_lines.append(f"\n[ID: {wid}]")
        footer = "\n".join(footer_lines)

        # Truncate body to fit Telegram's 4096 char limit, preserving footer
        max_len = 4000  # Leave margin for safety
        truncate_msg = "\n\n... (report truncated)"
        max_body_len = max_len - len(footer) - len(truncate_msg)

        if len(body) + len(footer) > max_len:
            body = body[:max_body_len] + truncate_msg

        return body + "\n" + footer

    @staticmethod
    def _sanitize_task_result(text: str) -> str:
        """Avoid markdown edge cases by removing backticks from task results."""
        return text.replace("`", "'")

    def _usage_text(self) -> str:
        return """## Workflow Orchestrator

**Commands:**
- `/workflow <spec>` — Run a workflow
- `/workflow status [id]` — Show status
- `/workflow list` — List recent workflows
- `/workflow resume <id>` — Resume a workflow
- `/workflow cancel <id>` — Cancel a workflow
- `/workflow result <id>` — Show full results
- `/workflow prune` — Delete successful workflows
- `/workflow help` — Show help

**Spec format:**
YAML or JSON (LLM parsing is experimental).

See docs/workflow.md for full examples and config.
"""

    async def _send_text(
        self,
        ctx: "CommandContext",
        text: str,
        *,
        notify: bool = True,
        reply_to: "MessageRef | None" = None,
    ) -> None:
        """Send a response with Telegram-aware markdown rendering when available."""
        from takopi.api import RenderedMessage

        message = self._render_message(text)
        await ctx.executor.send(message, reply_to=reply_to, notify=notify)

    @staticmethod
    def _render_message(text: str):
        try:
            from takopi.api import RenderedMessage
            from takopi.telegram.render import render_markdown
        except Exception:
            return text
        rendered_text, entities = render_markdown(text)
        return RenderedMessage(text=rendered_text, extra={"entities": entities})


# The backend instance that Takopi discovers
BACKEND = WorkflowCommand()
