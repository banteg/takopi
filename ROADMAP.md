# Takopi Enhancement Roadmap

## Overview

Evolve takopi into a customizable Telegram-to-Claude bridge with project-specific hooks.

## Completed (2026-01-01)

- [x] Voice transcription via local Whisper
- [x] Inline button support (reply_markup)
- [x] Callback query handling
- [x] Config: `[whisper]` section
- [x] Python version fix (>=3.10)

Branch: `feature/voice-and-buttons`

## Planned Architecture

### User Profile (Walid/Tiro use case)

**Primary use:** All modes - quick captures, full conversations, status checks
**Device:** Mobile only
**Voice content:** Stream of consciousness brain dumps
**Response style:** Adaptive (brief for captures, detailed for questions)

**Key insight:** Buttons should predict intent so user rarely types. Typing = button failure.

### General Enhancements (benefit all takopi users)

#### 1. Context File Injection
```toml
[context]
files = ["CLAUDE.md", "state/state.json"]
cwd = "~/prod/tiro"
```
- Inject project files into Claude's context
- Set working directory for Claude subprocess

#### 2. Button Configuration
```toml
[buttons]
startup = [
    {text = "📊 Status", data = "/status"},
    {text = "📥 Capture", data = "/capture"},
    {text = "🎯 Next", data = "/next"},
]

[buttons.voice]
enabled = true
options = [
    {text = "🔄 Process", data = "!process"},
    {text = "📥 Store", data = "!store"},
]
```

#### 3. Hook System
```toml
[hooks]
module = "my_hooks"  # Python module path
```

Hook interface:
```python
async def on_message(text: str, ctx: dict) -> str:
    """Transform input before Claude."""

async def on_response(text: str, ctx: dict) -> tuple[str, dict | None]:
    """Transform output, optionally add buttons."""

async def on_voice(transcript: str, ctx: dict) -> str | None:
    """Handle voice. Return None for confirmation buttons."""

async def on_startup(ctx: dict) -> str | None:
    """Custom startup message."""
```

#### 4. Queue Management
- Messages queue in order (no lost rapid-fire captures)
- Failures notify user, don't silently drop

#### 5. Voice Confirmation Flow
After transcription, show configurable buttons:
```
"Got it. I heard: [transcript preview]"
[🔄 Process] [📥 Store] [🔍 Review]
```
No timeout - wait for user decision.

### Tiro-Specific (lives in config, not takopi code)

- Button definitions in takopi.toml
- Hook implementations in tiro_hooks.py
- Context files: CLAUDE.md, state/state.json
- Domain auto-detection (via hooks)
- Proactive notifications (via hooks + external scheduler)

## Design Decisions

| Question | Answer |
|----------|--------|
| Voice processing | Ask each time (Process or Store) |
| Buttons | Quick actions + Session controls + Contextual |
| Response verbosity | Adaptive |
| Domain handling | Auto-detect from content |
| Task storage | Ask each time (commitments vs inbox) |
| Session model | Shared state with Claude Code |
| Context access | Full (journals, state, history) |
| Queue model | Queue in order, don't lose |
| Fallback on failure | Notify user |
| Confirmation timeout | No timeout |
| Architecture | General takopi + project hooks |

## Files Changed

```
src/takopi/
├── telegram.py      # +get_file, +download_file, +answer_callback_query, +reply_markup
├── bridge.py        # +voice handling, +callback_query handling, +WhisperConfig
├── transcribe.py    # NEW - Whisper transcription
├── cli.py           # +whisper config parsing
├── hooks.py         # TODO - Hook system
└── context.py       # TODO - Context injection
```

## Next Steps

1. [ ] Implement context file injection (`[context]` config)
2. [ ] Implement hook system (`[hooks]` config)
3. [ ] Implement button configuration (`[buttons]` config)
4. [ ] Add queue management for rapid-fire messages
5. [ ] Voice confirmation flow with buttons
6. [ ] Create example hooks for Tiro (tiro_hooks.py)
7. [ ] Documentation and examples

## Testing

```bash
# Install from fork
pip install -e ~/workspace/takopi-fork

# Run with Tiro
cd ~/prod/tiro && source venv/bin/activate && takopi claude

# Or via tiro-tg
tiro-tg start
tiro-tg attach  # View logs
tiro-tg stop
```

## Config Location

`~/.takopi/takopi.toml`
