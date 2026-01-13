# Projects and branches

This tutorial shows you how to register repos as projects and run tasks on feature branches without switching directories.

**What you'll learn:** How to target repos from anywhere with `/project`, and run on branches with `@branch`.

## The problem

So far, Takopi runs in whatever directory you started it. If you want to work on a different repo, you have to:

1. Stop Takopi
2. `cd` to the other repo
3. Restart Takopi

Projects fix this. Once you register a repo, you can target it from chat—even while Takopi is running elsewhere.

## 1. Register a project

Navigate to the repo and run `takopi init`:

```sh
cd ~/dev/happy-gadgets
takopi init happy-gadgets
```

Output:

```
added project "happy-gadgets" → ~/dev/happy-gadgets
```

This adds an entry to your config:

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
```

!!! tip "Project aliases are also Telegram commands"
    The alias becomes a `/command` you can use in chat. Keep them short and lowercase: `myapp`, `backend`, `docs`.

## 2. Target a project from chat

Now you can start Takopi anywhere:

```sh
cd ~  # doesn't matter where
takopi
```

And target the project by prefixing your message:

```
/happy-gadgets explain the authentication flow
```

Takopi runs the agent in `~/dev/happy-gadgets`, not your current directory.

The response includes a context footer:

```
The authentication flow uses JWT tokens stored in
httpOnly cookies...

──────────────────────────────────
codex --resume abc123
ctx: happy-gadgets
```

That `ctx:` line tells you which project is active. When you reply, Takopi automatically uses the same project—you don't need to repeat `/happy-gadgets`.

## 3. Set up worktrees

Worktrees let you run tasks on feature branches without touching your main checkout. Instead of `git checkout`, Takopi creates a separate directory for each branch.

Add worktree config to your project:

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
worktrees_dir = ".worktrees"      # where branches go
worktree_base = "main"            # base for new branches
```

!!! note "Ignore the worktrees directory"
    Add `.worktrees/` to your global gitignore so it doesn't clutter `git status`:
    ```sh
    echo ".worktrees/" >> ~/.config/git/ignore
    ```

## 4. Run on a branch

Use `@branch` after the project:

```
/happy-gadgets @feat/new-login add rate limiting to the login endpoint
```

Takopi:
1. Checks if `.worktrees/feat/new-login` exists
2. If not, creates it: `git worktree add .worktrees/feat/new-login -b feat/new-login`
3. Runs the agent in that worktree

The response shows both project and branch:

```
Added rate limiting middleware to the login endpoint.
Limited to 5 attempts per minute per IP...

──────────────────────────────────
codex --resume xyz789
ctx: happy-gadgets @feat/new-login
```

Replies stay on the same branch. Your main checkout is untouched.

## 5. Context persistence

Once you've set a context (via `/project @branch` or by replying), it sticks:

```
You: /happy-gadgets @feat/new-login add tests

Bot: Added unit tests for rate limiting...
     ctx: happy-gadgets @feat/new-login

You: (reply) also add integration tests    ← no need to repeat context

Bot: Added integration tests...
     ctx: happy-gadgets @feat/new-login
```

The `ctx:` line in each message carries the context forward.

## 6. Set a default project

If you mostly work in one repo, set it as the default:

```toml
default_project = "happy-gadgets"
```

Now messages without a `/project` prefix go to that repo:

```
add a health check endpoint
```

Goes to `happy-gadgets` automatically.

## Putting it together

Here's a typical workflow:

```
# Start Takopi once, anywhere
takopi

# Work on main
/happy-gadgets review the error handling

# Work on a feature branch
/happy-gadgets @feat/caching implement redis caching

# Continue on the branch (just reply)
↩️ also add cache invalidation

# Switch to another project
/backend @fix/memory-leak profile memory usage

# Quick task on main (new message, no reply)
/happy-gadgets bump the version number
```

All from the same Telegram chat, without restarting Takopi or changing directories.

## Project config reference

Full options for `[projects.<alias>]`:

| Key | Default | Description |
|-----|---------|-------------|
| `path` | (required) | Repo root. Expands `~`. |
| `worktrees_dir` | `.worktrees` | Where branch worktrees are created. |
| `worktree_base` | `null` | Base branch for new worktrees. If unset, uses the branch the worktree command specifies. |
| `default_engine` | `null` | Engine to use for this project (overrides global default). |
| `chat_id` | `null` | Bind a Telegram chat/group to this project. |

## Troubleshooting

**"unknown project"**

Run `takopi init <alias>` in the repo first.

**Branch worktree not created**

Make sure `worktrees_dir` is set in the project config. Check that the directory is writable.

**Context not carrying forward**

Make sure you're **replying** to a message with a `ctx:` line. If you send a new message (not a reply), context resets unless you have a `default_project`.

**Worktree conflicts with existing branch**

If the branch already exists locally, Takopi uses it. If you want a fresh start, delete the worktree directory: `rm -rf ~/dev/happy-gadgets/.worktrees/feat/old-branch`.

## Next

You've got projects and branches working. The final tutorial covers using multiple engines effectively.

[Multi-engine workflows →](multi-engine.md)
