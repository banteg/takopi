# Workflow examples

This page collects workflow specs you can copy and adapt. Examples include YAML specs and natural-language inputs for LLM parsing (experimental).

## Input formats

Workflow specs can be provided as YAML or JSON. The parser tries YAML first, then falls back to JSON.

YAML:
```yaml
tasks:
  - id: collect
    prompt: "Collect recent changelog entries."
  - id: summarize
    prompt: "Summarize these: {collect}"
    depends_on: [collect]
```

JSON:
```json
{
  "tasks": [
    { "id": "collect", "prompt": "Collect recent changelog entries." },
    { "id": "summarize", "prompt": "Summarize these: {collect}", "depends_on": ["collect"] }
  ]
}
```

## Workflow architecture (DAG)

```
Workflow Spec (YAML/JSON)
        |
        v
+-----------------------+
|   parse_workflow()    |
|  -> TaskGraph (DAG)   |
+-----------------------+
        |
        v
+-----------------------+        +-------------------------+
| OrchestrationExecutor |<------>|   CheckpointStore       |
|  - scheduler          |        |  (FileCheckpointStore)  |
|  - conditionals       |        |  save/load/list/delete  |
|  - loops/early exits  |        +-------------------------+
+-----------+-----------+
            |
            v
     +-------------+
     | Task Graph  |
     |  (DAG)      |
     +-------------+
      |    |    |
      |    |    +------------------------------+
      |    |                                   |
      v    v                                   v
+---------+--------+                   +-----------------+
| Task A           |------------------>| Task B          |
| engine=codex     |  edge: depends_on | engine=claude    |
+---------+--------+                   +--------+--------+
          |                                     |
          |                                     v
          |                              +-------------+
          |                              | Task C      |
          |                              | engine=...  |
          |                              +-------------+
          |
          v
+----------------------------+
| CommandExecutor (runtime)  |
|  run_one / run_many        |
+----------------------------+

Legend:
- Nodes = tasks (TaskGraph.tasks)
- Edges = depends_on relationships
- Executor schedules ready nodes, evaluates conditionals/loops, and saves checkpoints
```

---

## YAML spec

### Release notes (mixed engines/outputs)

```yaml
name: Weekly release notes

tasks:
  - id: gather
    prompt: "Collect merged PR titles from the last 7 days."
    engine: codex
    output: text

  - id: classify
    prompt: "Group PRs by area (frontend, backend, infra)."
    depends_on: [gather]
    engine: claude
    output: json

  - id: draft
    prompt: "Write release notes using the grouped JSON: {classify}."
    depends_on: [classify]
    engine: codex
    output: markdown
```

### Migration helper (file context)

```yaml
name: Migration helper

tasks:
  - id: read
    prompt: "Summarize TODOs from docs/migration.md."
    engine: codex

  - id: plan
    prompt: |
      Create a migration checklist using:
      - the summary from {read}
      - the current schema in src/schema.sql
    depends_on: [read]
    engine: claude

  - id: verify
    prompt: "List risk areas and missing test coverage from {plan}."
    depends_on: [plan]
    engine: codex
```

### Long-running workflow (checkpoint-friendly)

```yaml
name: Quarterly tech debt review

tasks:
  - id: inventory
    prompt: "Scan repos and summarize top recurring TODOs."
    engine: codex

  - id: analyze
    prompt: |
      Analyze {inventory} and identify the highest-impact debt items.
      Include rough effort estimates.
    depends_on: [inventory]
    engine: claude

  - id: plan
    prompt: |
      Build a 4-week remediation plan for the top 5 items.
      Include owners and milestones.
    depends_on: [analyze]
    engine: codex

  - id: review
    prompt: "Summarize risks and open questions in {plan}."
    depends_on: [plan]
    engine: claude
```

---

### Parallel task execution

```yaml
name: Parallel fetch

tasks:
  - id: fetch_api
    prompt: "Fetch API changelog."
    engine: codex

  - id: fetch_web
    prompt: "Fetch web changelog."
    engine: codex

  - id: summarize
    prompt: "Summarize {fetch_api} and {fetch_web}."
    depends_on: [fetch_api, fetch_web]
```

### Conditional branching

```yaml
tasks:
  - id: classify
    prompt: "Is this a bug or a feature? Return bug|feature."
    engine: claude

  - id: bug_path
    prompt: "Create a bug report using {classify}."
    depends_on: [classify]
    condition:
      when: "{{ classify.result | lower == 'bug' }}"

  - id: feature_path
    prompt: "Draft a feature request using {classify}."
    depends_on: [classify]
    condition:
      when: "{{ classify.result | lower == 'feature' }}"
```

### Loops (times)

```yaml
loops:
  - id: retries
    type: times
    count: 3
    tasks:
      - id: probe
        prompt: "Probe service health (attempt {loop.iteration})."
        engine: codex
```

### Loops (foreach)

```yaml
tasks:
  - id: list_services
    prompt: "List services as a comma-separated string."
    engine: codex

loops:
  - id: per_service
    type: foreach
    items_from: list_services
    item_separator: ","
    tasks:
      - id: check
        prompt: "Check {loop.item} for errors."
        engine: claude
```

### Loops (until)

```yaml
loops:
  - id: refine
    type: until
    condition:
      when: "{{ loop.previous | lower == 'ok' }}"
    tasks:
      - id: improve
        prompt: "Refine the draft until you can answer OK."
        engine: codex
```

### Loops (while)

```yaml
tasks:
  - id: seed
    prompt: "Return a number from 1-3."
    engine: codex

loops:
  - id: iterate
    type: while
    depends_on: [seed]
    condition:
      when: "{{ loop.previous | int < 3 }}"
    tasks:
      - id: bump
        prompt: "Increase the number by 1."
        engine: codex
```

### Early exit

```yaml
tasks:
  - id: fetch
    prompt: "Fetch the latest API schema from the repo and summarize changes."
    engine: codex

  - id: validate
    prompt: "Validate the schema against our rules and output PASS or FAIL."
    depends_on: [fetch]
    engine: claude
    early_exit:
      when: "{{ validate.result | lower == 'fail' }}"
      message: "Stopping: schema validation failed."

  - id: publish
    prompt: "Publish the schema docs and notify #api-changes."
    depends_on: [validate]
    engine: codex
```

---

## LLM parsing mode (experimental)

If your workflow description is too free-form for strict YAML/JSON, Takopi can ask an LLM to convert it into a YAML spec.
The parsed YAML is shown back to you before execution.

Example input (natural language):

```
hey claude and codex, tell me a joke about rust and javascript, then rank them.
```

Example output (generated YAML preview):

```yaml
name: joke_ranker
tasks:
  - id: rust_joke
    prompt: "Tell me a joke about rust"
    engine: claude
  - id: javascript_joke
    prompt: "Tell me a joke about javascript"
    engine: codex
  - id: rank_jokes
    prompt: |
      Rank the following jokes:

      rust joke: {rust_joke}

      javascript joke: {javascript_joke}
    depends_on: [rust_joke, javascript_joke]
```

### On-call rundown

```
Summarize new alerts in the last 2 hours, group by severity and component,
then draft a response plan for Sev-1 only using Claude.
```

### Support triage

```
Review new support tickets from the last 24 hours, tag them as bug/feature/docs,
then draft responses for bug tickets using Claude.
```

---

## Orchestration executor examples

These examples show how to drive the orchestration engine programmatically.

Run a workflow spec:

```py
from takopi.plugins.workflow.executor import run_workflow, ExecutionConfig

config = ExecutionConfig(max_parallel=2, continue_on_failure=True)
result = await run_workflow(ctx.executor, spec, config=config)
print(result.checkpoint.status)
```

Resume from a saved checkpoint:

```py
from takopi.plugins.workflow.executor import resume_workflow
from takopi.plugins.workflow.checkpoint import FileCheckpointStore

store = FileCheckpointStore(base_dir=config_path.parent / "takopi_workflows")
checkpoint = store.load("wf-1234")
if checkpoint:
    result = await resume_workflow(ctx.executor, checkpoint)
```

Cancel a running workflow:

```py
import asyncio
from takopi.plugins.workflow.executor import OrchestrationExecutor

orchestrator = OrchestrationExecutor(executor=ctx.executor)
task = asyncio.create_task(orchestrator.run(graph, spec=spec))
orchestrator.cancel()
await task
```

Hook into progress callbacks:

```py
from takopi.plugins.workflow.executor import ExecutionConfig, OrchestrationExecutor

async def on_task_start(task):
    print(f"Starting {task.id}")

async def on_task_complete(task):
    print(f"Done {task.id}: {task.status.value}")

config = ExecutionConfig(on_task_start=on_task_start, on_task_complete=on_task_complete)
orchestrator = OrchestrationExecutor(executor=ctx.executor, config=config)
await orchestrator.run(graph, spec=spec)
```

Checkpointing and resume (manual store):

```py
from takopi.plugins.workflow.checkpoint import FileCheckpointStore
from takopi.plugins.workflow.executor import ExecutionConfig, OrchestrationExecutor

store = FileCheckpointStore(base_dir=config_path.parent / "takopi_workflows")
config = ExecutionConfig(checkpoint_store=store, checkpoint_interval=1)
orchestrator = OrchestrationExecutor(executor=ctx.executor, config=config)
result = await orchestrator.run(graph, spec=spec)
checkpoint_id = result.workflow_id
```

Resume later:

```py
checkpoint = store.load(checkpoint_id)
if checkpoint:
    await resume_workflow(ctx.executor, checkpoint, config=config)
```

---

## Notes

- `early_exit` stops execution when its condition evaluates true.
- Outputs can be `text`, `json`, or `markdown` depending on how you want to consume results.
- You can mix engines across tasks to trade cost, speed, or quality.
