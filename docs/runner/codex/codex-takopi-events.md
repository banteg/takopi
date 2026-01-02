Here’s a clean way to make “Takopi events” just **3 shapes** while still covering **every `codex exec --json` line type** and preserving the invariants you care about (stable IDs, resume/thread ownership, final answer delivery).  

## The 3-event Takopi schema

I’d model it like this (JSON-ish). The important trick is: **your single `action` event needs a `phase`**, otherwise you can’t represent started/updated/completed lifecycles.

### 1) `started`

Emitted once **as soon as you know the resume token** (Codex: `thread.started.thread_id`).

```json
{
  "type": "started",
  "engine": "codex",
  "resume": { "engine": "codex", "value": "0199..." },
  "title": "Codex",               // optional
  "meta": { "raw": { ... } }      // optional: for debugging
}
```

### 2) `action`

Emitted for **everything that is progress / updates / warnings / per-item lifecycle**.

```json
{
  "type": "action",
  "engine": "codex",
  "action": {
    "id": "item_5",
    "kind": "tool",               // command | tool | file_change | web_search | note | turn | warning | telemetry
    "title": "docs.search",       // short label for renderer
    "detail": { ... }             // structured payload (freeform)
  },
  "phase": "started",             // started | updated | completed
  "ok": true,                     // optional; present when phase=completed (or warnings)
  "message": "optional text",     // optional; logs/warnings can use this
  "level": "info"                 // optional: debug|info|warning|error
}
```

### 3) `completed`

Emitted once at end-of-run with the **final answer** (from `agent_message`) and final status.

```json
{
  "type": "completed",
  "engine": "codex",
  "resume": { "engine": "codex", "value": "0199..." },  // if known
  "ok": true,
  "answer": "Done. I updated the docs...",
  "error": null,
  "usage": { "input_tokens": 24763, "cached_input_tokens": 24448, "output_tokens": 122 }  // optional
}
```

Why this fits Takopi cleanly:

* Your `started` corresponds to the old “session.started” concept (runner learns resume token; bridge can now safely serialize per thread). 
* Your `action` is “everything that would have been action.started/action.completed/log/error” collapsed into one stream. 
* Your `completed` corresponds to final `RunResult` + status, using Codex’s `agent_message` as the answer source.  

---

## How everything fits together (end-to-end)

From the bridge/runner point of view:

1. **Bridge receives Telegram prompt**
2. Bridge tries to extract a resume line (`codex resume <uuid>`) from the message/reply (runner-owned parsing). 
3. Bridge calls `runner.run(prompt, resumeTokenOrNone)`
4. Codex runner spawns `codex exec --json ...` and reads JSONL line-by-line. 
5. The *first moment the runner can know thread identity* is:

   * `thread.started` → contains `thread_id` (this is your resume value)
6. Runner must (per Takopi’s concurrency invariant) **acquire the per-thread lock as soon as the new thread token is known**, before emitting `started`. 
7. Runner translates subsequent Codex JSONL lines into `action` events for progress rendering.
8. Runner captures the final answer from `item.completed` where `item.type="agent_message"`. 
9. Runner emits exactly one `completed` event when the run ends (`turn.completed` or failure), including the captured final answer.

---

## Direct translation: every Codex `exec --json` line → your 3-event schema

Codex emits two categories: **top-level lines** and **item lines**. 

### A) Top-level lines

#### `thread.started`

Codex:

```json
{"type":"thread.started","thread_id":"0199..."}
```

→ Takopi:

* emit **`started`**:

  * `resume.value = thread_id`

This is exactly the “learn resume tag” moment you described. 

---

#### `turn.started`

Codex:

```json
{"type":"turn.started"}
```

→ Takopi (recommended):

* emit **`action`** with a synthetic action id, e.g. `"turn_0"`

  * `kind="turn"`, `phase="started"`, `title="turn started"`

You *can* also drop it if your UI doesn’t care, but if you want “every codex type translates”, this maps cleanly into `action`.

---

#### `turn.completed`

Codex includes usage:

```json
{"type":"turn.completed","usage":{...}}
```

→ Takopi:

* emit **`completed`**

  * `ok=true`
  * `answer = last seen agent_message text` (or `""` if none)
  * `usage = usage` (optional)

This is your authoritative “run succeeded” boundary. 

---

#### `turn.failed`

Codex:

```json
{"type":"turn.failed","error":{"message":"..."}}
```

→ Takopi:

* emit **`completed`**

  * `ok=false`
  * `error = error.message`
  * `answer = last seen agent_message` (if any; usually empty)

This is “run ended, but failed”. 

---

#### Top-level `error` (stream error)

Codex:

```json
{"type":"error","message":"stream error: broken pipe"}
```

Cheatsheet meaning: this is a **fatal stream failure** (not just a tool failure).
However, Codex may also emit transient reconnect notices as `type="error"` with
messages like `"Reconnecting... 1/5"` while it retries a dropped stream. Treat
those as non-fatal progress updates (do **not** end the run).

→ Takopi:

* if you haven’t emitted `completed` yet: emit **`completed`** with `ok=false` and `error=message`
* if you *already* emitted `completed`, treat it as an extra warning (or ignore; it’s “post-mortem noise”)

---

### B) Item lines: `item.started`, `item.updated`, `item.completed`

All item lines include `item.id` and it is stable across updates/completion. 
That means your `action.action.id` should just be `item.id` — perfect match to “stable within a run”.

#### General rule (for any item.* line)

* `action.action.id = item.id`
* `action.phase = started | updated | completed`
* `action.action.kind` derived from `item.type`
* `action.action.detail` contains the relevant item fields (possibly trimmed)

Now, map each `item.type`:

---

## Item-type mapping: `item.type` → `action.kind/title/detail/ok`

Below is a “complete coverage” mapping for all item types listed in the cheatsheet. 

### 1) `agent_message` (only `item.completed`)

Codex:

```json
{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"..."}}
```

→ Takopi:

* **do not emit an `action`** (recommended)
* instead: **store** `final_answer = item.text`
* final answer will be surfaced by the eventual `completed` event

Reason: you want `completed` to be “final answer delivery”, and you probably don’t want the answer duplicated in progress rendering. 

(If you *do* want to render it as it arrives, you can emit an `action` too, but then your renderer must avoid showing it twice.)

---

### 2) `reasoning` (only `item.completed`, if enabled)

Codex gives a text breadcrumb. 

→ Takopi `action`:

* `kind="note"`
* `title="reasoning"` (or “thought”)
* `phase="completed"`
* `message=item.text` (or put it under `detail.text`)

This is usually safe to show as a short “what it’s doing” line (or ignore if you don’t want to surface it).

---

### 3) `command_execution` (`item.started` and `item.completed`)

Codex fields include `command`, `status`, `aggregated_output` (often noisy), and
`exit_code` (null or omitted until completion). 

→ Takopi `action`:

* `kind="command"`
* `title=item.command` (or a shortened version like `pytest`)
* `detail={ command, exit_code, status }` (optionally include output tail)
* `phase="started"` on `item.started`
* `phase="completed"` on `item.completed`
* `ok = (item.status == "completed")` (and `exit_code == 0` when present)

Note: “failed” command becomes `ok=false` but it’s still just an `action` completion — the overall run might still succeed later, depending on agent behavior.

---

### 4) `file_change` (only `item.completed`)

Codex contains `changes[]` and `status`. 

→ Takopi `action`:

* `kind="file_change"`
* `title="file changes"`
* `detail={ changes }`
* `phase="completed"`
* `ok = (item.status == "completed")`

This is a great progress line for your UI (“updated docs/…, added …”).

---

### 5) `mcp_tool_call` (`item.started` and `item.completed`)

Codex contains server/tool/arguments/status and may include result/error on
completion. Result can be large; may include base64 in content blocks. 

→ Takopi `action`:

* `kind="tool"`
* `title=f"{item.server}.{item.tool}"`
* `detail={ server, tool, arguments, status }`
* on completion, include *summary* of result:

  * e.g. `detail.result_summary = { content_blocks: N, has_structured: bool }`
  * include `detail.error_message` if failed
* `phase="started"` or `"completed"`
* `ok = (item.status == "completed")`

Recommendation: **do not dump** full `result.content` into `detail` if it can contain large blobs; keep a summary and optionally stash full raw elsewhere for debugging.

---

### 6) `web_search` (only `item.completed`)

Codex includes `query`. 

→ Takopi `action`:

* `kind="web_search"`
* `title="web search"`
* `detail={ query }`
* `phase="completed"`
* `ok=true` (this is just “it did a search”; success/failure is typically not expressed here)

---

### 7) `todo_list` (`item.started`, `item.updated`, `item.completed`)

Codex includes checklist items with `completed` booleans. 

→ Takopi `action`:

* `kind="note"` (or `"todo"`)
* `title="plan"`
* `detail={ items, done: count_done, total: count_total }`
* `phase` maps 1:1 to started/updated/completed
* `ok=true` when phase completed (optional)

This is the one case where `item.updated` is common; your unified `action` event is exactly the right shape for it.

---

### 8) Item `error` (non-fatal warning as an item; only `item.completed`)

Codex:

```json
{"type":"item.completed","item":{"id":"item_9","type":"error","message":"command output truncated"}}
```

Cheatsheet: this is a **non-fatal warning** (different from top-level fatal `error`). 

→ Takopi `action`:

* `kind="warning"` (or `"note"`)
* `title="warning"`
* `message=item.message`
* `level="warning"`
* `phase="completed"`
* `ok=true` (because it’s informational) **or** omit `ok`

---

## Suggested “single-pass” translator logic (pseudocode)

This shows how to implement it without needing more than one pass or complicated buffering:

```python
final_answer = None
resume = None
did_emit_started = False
did_emit_completed = False
turn_index = 0

def emit(evt): yield evt  # emit to the output event stream

for line in codex_jsonl_stream:
    t = line["type"]

    if t == "thread.started":
        resume = {"engine": "codex", "value": line["thread_id"]}
        # acquire per-thread lock here (for new sessions) before emitting started
        emit({"type":"started","engine":"codex","resume":resume,"title":"Codex"})
        did_emit_started = True
        continue

    if t == "turn.started":
        emit({"type":"action","engine":"codex",
              "action":{"id":f"turn_{turn_index}","kind":"turn","title":"turn started","detail":{}},
              "phase":"started"})
        continue

    if t == "item.started" or t == "item.updated" or t == "item.completed":
        item = line["item"]
        item_type = item["type"]
        item_id = item["id"]

        if t == "item.completed" and item_type == "agent_message":
            final_answer = item.get("text","")
            continue

        # map item_type -> kind/title/detail/ok
        action_evt = map_item_to_action(item, phase=t.split(".")[1])
        emit(action_evt)
        continue

    if t == "turn.completed":
        emit({"type":"completed","engine":"codex","resume":resume,
              "ok":True,"answer":final_answer or "",
              "error":None,"usage":line.get("usage")})
        did_emit_completed = True
        continue

    if t == "turn.failed":
        emit({"type":"completed","engine":"codex","resume":resume,
              "ok":False,"answer":final_answer or "",
              "error":line["error"]["message"]})
        did_emit_completed = True
        continue

    if t == "error":  # fatal stream error
        if not did_emit_completed:
            emit({"type":"completed","engine":"codex","resume":resume,
                  "ok":False,"answer":final_answer or "",
                  "error":line.get("message")})
            did_emit_completed = True
        continue

# Optional: if stream ends without turn.completed/failed,
# emit completed with ok=False and error="unexpected EOF"
```

This design preserves the Takopi ordering/serialization principles: `started` happens as soon as resume token is known, actions stream in order, and exactly one `completed` closes the run. 

---

## One practical note: what “completed” should mean

Even though you *learn* the final answer at `agent_message`, you generally want `completed` to be emitted at the **turn boundary** (`turn.completed` / `turn.failed`), because:

* you can attach usage (`turn.completed.usage`) only there, 
* you guarantee `completed` is truly the last event,
* you still use `agent_message` as the authoritative answer payload.

That still matches your intent (“completed is when we get final answer”) because the answer comes from `agent_message`; you just *publish* it at the terminal boundary.
