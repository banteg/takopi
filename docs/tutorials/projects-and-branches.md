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
saved project 'happy-gadgets' to ~/.takopi/takopi.toml
```

This adds an entry to your config (Takopi also fills in defaults like `worktrees_dir`, `default_engine`, and sometimes `worktree_base`):

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
```

!!! tip "Project aliases are also Telegram commands"
    The alias becomes a `/command` you can use in chat. Keep them short and lowercase: `myapp`, `backend`, `docs`.

## 2. Target a project from chat

Now you can start Takopi from another repo. If you don't specify a project, Takopi runs in the directory where you launched it.

```sh
cd ~/dev/your-project
takopi
```

And target the project by prefixing your message:

!!! user "You"
    /happy-gadgets explain the authentication flow

Takopi runs the agent in `~/dev/happy-gadgets`, not your current directory.

The response includes a context footer:

!!! takopi "Takopi"
    ctx: happy-gadgets<br>
    codex resume abc123

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

!!! user "You"
    /happy-gadgets @feat/new-login add rate limiting to the login endpoint

Takopi:
1. Checks if `.worktrees/feat/new-login` exists (and is a worktree)
2. If the branch exists locally, it adds a worktree for it
3. If the branch doesn't exist, it creates it from `worktree_base` (or the repo default) and adds the worktree
4. Runs the agent in that worktree

The response shows both project and branch:

!!! takopi "Takopi"
    ctx: happy-gadgets @feat/new-login<br>
    codex resume xyz789

Replies stay on the same branch. Your main checkout is untouched.

## 5. Context persistence

Once you've set a context (via `/project @branch` or by replying), it sticks:

!!! user "You"
    /happy-gadgets @feat/new-login add tests

!!! takopi "Takopi"
    ctx: happy-gadgets @feat/new-login

!!! user "reply to the bot's answer"
    also add integration tests

!!! takopi "Takopi"
    ctx: happy-gadgets @feat/new-login

The `ctx:` line in each message carries the context forward.

## 6. Set a default project

If you mostly work in one repo, set it as the default:

```toml
default_project = "happy-gadgets"
```

Now messages without a `/project` prefix go to that repo:

!!! user "You"
    add a health check endpoint

Goes to `happy-gadgets` automatically.

## Putting it together

Here's a typical workflow:

```sh
takopi
```

!!! user "You"
    /happy-gadgets review the error handling

!!! user "You"
    /happy-gadgets @feat/caching implement caching

!!! takopi "Takopi"
    ctx: happy-gadgets @feat/caching

    !!! user "You"
        also add cache invalidation

!!! user "You"
    /backend @fix/memory-leak profile memory usage

!!! user "You"
    /happy-gadgets bump the version number

All from the same Telegram chat, without restarting Takopi or changing directories.

## Project config reference

Full options for `[projects.<alias>]`:

| Key | Default | Description |
|-----|---------|-------------|
| `path` | (required) | Repo root. Expands `~`. |
| `worktrees_dir` | `.worktrees` | Where branch worktrees are created (relative to the project path). |
| `worktree_base` | `null` | Base branch for new worktrees. If unset, Takopi uses `origin/HEAD`, the current branch, or `master`/`main` (in that order). |
| `default_engine` | `null` | Engine to use for this project (overrides global default). |
| `chat_id` | `null` | Bind a Telegram chat/group to this project. |

## Troubleshooting

**"unknown project"**

Run `takopi init <alias>` in the repo first.

**Branch worktree not created**

Make sure the worktrees directory (default `.worktrees`) is writable. If you've customized `worktrees_dir`, verify that path exists or can be created.

**Context not carrying forward**

Make sure you're **replying** to a message with a `ctx:` line. If you send a new message (not a reply), context resets unless you have a `default_project`.

**Worktree conflicts with existing branch**

If the branch already exists locally, Takopi uses it. For a fresh start, delete the worktree **and** the branch, or pick a new branch name.

## Next

You've got projects and branches working. The final tutorial covers using multiple engines effectively.

[Multi-engine workflows →](multi-engine.md)
