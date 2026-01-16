You can push testability (and coverage) quite a bit further from ~75% by focusing on the parts of the codebase that are currently *hardest to test*, not the parts that are already mostly pure/covered.

From the `coverage.xml` you shared, overall line coverage is **~75.4% (7002 / 9288 lines)**. The biggest coverage gaps are concentrated in a small set of “boundary” modules (CLI, Telegram command handlers, and OpenAI voice transcription), while most of the core logic is already in the 85–100% range.

Below is a practical, repo-specific way to improve testability.

---

## 1) Target the real testability hotspots (where the code is “imperative”)

These are your lowest-coverage files (rounded):

* `src/takopi/cli.py` — **42%**
* `src/takopi/telegram/commands/agent.py` — **13%**
* `src/takopi/telegram/commands/trigger.py` — **13%**
* `src/takopi/telegram/commands/media.py` — **40%**
* `src/takopi/runners/tool_actions.py` — **41%**
* `src/takopi/telegram/commands/topics.py` — **49%**
* `src/takopi/telegram/client_api.py` — **54%**
* `src/takopi/telegram/voice.py` — **61%**
* `src/takopi/telegram/commands/file_transfer.py` — **62%**
* `src/takopi/telegram/commands/reasoning.py` — **62%**
* `src/takopi/runners/codex.py` — **63%**
* `src/takopi/telegram/onboarding.py` — **63%**

**Pattern:** these modules are “edge” code: they touch network, filesystem, subprocess, environment, or user interaction. That’s exactly where testability tends to degrade unless you add seams.

---

## 2) Apply “Functional core, imperative shell” consistently

You already do this well in many places (example: `TelegramClient` accepts an injected `http_client` and `sleep`, which makes it highly testable).

Do the same in the remaining hotspots:

### A. Telegram command handlers: return a *reply plan* instead of sending replies inline

Right now handlers do a lot of:

* parse args
* check permissions (calls Telegram)
* resolve context / defaults
* produce message text
* call `reply(...)` (side effect)

If you refactor to:

1. compute the response text (pure-ish)
2. *then* send it

…you can unit-test the computed response without building a full `TelegramBridgeConfig` + fake transport.

**Example shape:**

```py
@dataclass(frozen=True)
class ReplyPlan:
    text: str

async def plan_agent_reply(...deps...) -> ReplyPlan:
    # all logic here
    return ReplyPlan(text=...)

async def _handle_agent_command(...):
    plan = await plan_agent_reply(...)
    await reply(text=plan.text)
```

Testability impact:

* Tests don’t need to care about Telegram transport details.
* You can test “what users see” with simple assertions.

This applies especially to:

* `telegram/commands/agent.py`
* `telegram/commands/trigger.py`
* `telegram/commands/media.py`
* parts of `topics.py`, `file_transfer.py`, `reasoning.py`

### B. Voice transcription: don’t instantiate `AsyncOpenAI` inside the function

`telegram/voice.py` currently does this:

```py
async with AsyncOpenAI(timeout=120) as client:
    response = await client.audio.transcriptions.create(...)
```

That makes tests awkward (you end up monkeypatching a class constructor).

Make it injectable instead:

```py
class VoiceTranscriber(Protocol):
    async def transcribe(self, *, model: str, audio_bytes: bytes) -> str: ...

class OpenAIVoiceTranscriber:
    async def transcribe(self, *, model: str, audio_bytes: bytes) -> str:
        ...

async def transcribe_voice(..., transcriber: VoiceTranscriber = OpenAIVoiceTranscriber()):
    ...
    text = await transcriber.transcribe(model=model, audio_bytes=audio_bytes)
```

Testability impact:

* You can cover:

  * disabled transcription branch
  * max size logic
  * bot get_file / download failures
  * OpenAI error handling
    …without any network, env vars, or monkeypatching constructors.

### C. CLI: split “decision logic” from “effects”

`cli.py` contains lots of decisions + lots of effects (prompting, printing, running backends, reading env vars).

A good refactor pattern:

* Move decision logic into small pure helpers that return “what to do”
* Keep Typer functions thin wrappers

Example:

```py
@dataclass(frozen=True)
class AutoRouterPlan:
    transport_id: str
    default_engine: str
    should_onboard: bool
    ...

def plan_auto_router(...inputs...) -> AutoRouterPlan:
    ...

def _run_auto_router(...):
    plan = plan_auto_router(...)
    # do side effects
```

Testability impact:

* You can test 80% of CLI logic without invoking Typer or anyio.
* You can test the remaining 20% with `CliRunner` integration tests.

---

## 3) Standardize fakes/fixtures so adding tests is cheap

You already *have* good fakes in `tests/test_telegram_bridge.py`:

* `_FakeBot` implements `BotClient`
* `_FakeTransport`
* `_make_cfg(...)`

That’s great — but it’s trapped inside one giant test file.

To improve testability across the repo, extract these into reusable fixtures, e.g.:

* `tests/telegram_fakes.py` (classes)
* `tests/conftest.py` (fixtures like `fake_bot`, `fake_transport`, `make_cfg`)

This makes it *much* easier to add tests for currently low-covered modules like `/agent` and `/trigger`, because every new test won’t need to reinvent the world.

---

## 4) Add tests where they buy the most reliability per line

If the goal is “more testability” (not just a vanity coverage number), prioritize:

### Telegram commands: correctness of user-visible behavior

Add tests for:

* `/agent`:

  * show mode in private chat
  * set/clear in group when admin vs non-admin
  * invalid engine error text
* `/trigger`:

  * show mode picks topic override vs chat default vs default
  * set/clear permission checks
* `/media`:

  * media group resolution: chooses command message correctly
  * auto-put behavior with and without caption
  * error cases in directive parsing

These tests are “cheap” because you can reuse the existing `_FakeBot/_FakeTransport/_make_cfg` harness.

### Voice transcription: deterministic branch coverage

Add tests for:

* disabled -> returns `None` and replies with hint
* size too large (metadata and actual bytes)
* `get_file` failure / download failure
* transcriber raises -> replies error, returns None
* success -> returns transcript

### CLI: lock/errors and non-interactive behavior

Add tests for:

* `_resolve_transport_id` fallback behaviors
* `_should_run_interactive` toggles via env + isatty
* `plugins_cmd --load` with fake entrypoints (you already have `tests/plugin_fixtures.py`)
* `init` writing project config with `tmp_path`

---

## 5) Consider turning on branch coverage (optional but powerful)

Right now the Cobertura report shows **0 branches measured**, which usually means you’re only tracking line coverage.

For “edge-heavy” code (CLI + Telegram commands), **branch coverage is often a better proxy for confidence** because it forces you to test the “sad paths” you care about (permission denied, missing token, invalid config, retries, etc.).

In pytest, this is as simple as adding `--cov-branch` (or the coverage config equivalent).

---

## 6) A realistic coverage jump without heroic effort

To get from ~75% → **80%**, you need about **+429 lines covered** (with the same `lines-valid`). That’s very achievable by adding tests to just a few of the low-covered modules above.

The bigger payoff, though, is the *testability unlock*: once you’ve introduced a couple of seams (especially for voice transcription and command reply planning), adding future tests becomes fast and pleasant instead of “mocking everything”.

---

## Suggested next steps (small, high-impact)

1. **Extract the Telegram fakes** from `tests/test_telegram_bridge.py` into shared fixtures.
2. Add tests for `/agent` and `/trigger` (these are currently at ~13% coverage and are quite testable with your existing fakes).
3. Refactor `transcribe_voice()` to accept an injected transcriber (or client factory) and add the 5–6 branch tests listed above.
4. Optionally: start measuring branch coverage once the seams are in place.

If you want, paste (or point me to) the specific areas you find hardest to test right now (CLI, Telegram commands, runners, etc.), and I’ll propose concrete refactor boundaries and example tests tailored to those files.
