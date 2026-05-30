# Worktrees

Use `@branch` to run tasks in a dedicated git worktree for that branch.

## Enable worktree-based runs for a project

Add a `worktrees_dir` (and optionally a base branch) to the project:

=== "takopi config"

    ```sh
    takopi config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    takopi config set projects.happy-gadgets.worktrees_dir ".worktrees"
    takopi config set projects.happy-gadgets.worktree_base "master"
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    worktrees_dir = ".worktrees"      # relative to project path
    worktree_base = "master"          # base branch for new worktrees
    ```

## Run in a branch worktree

Send a message like:

```
/happy-gadgets @feat/memory-box freeze artifacts forever
```

## Copy env files after worktree creation

Set `worktree_setup_script` to copy `.env`, agent configs, or other files into the new worktree:

=== "takopi config"

    ```sh
    takopi config set projects.happy-gadgets.worktree_setup_script "bash scripts/setup-worktree.sh"
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    worktree_setup_script = "bash .takopi/setup-worktree.sh"
    ```

The script receives:

- `TAKOPI_WORKTREE_PATH` — absolute path to the new worktree
- `TAKOPI_PROJECT_PATH` — absolute path to the project root
- `TAKOPI_BRANCH` — the branch name

Example script (`.takopi/setup-worktree.sh`):

```bash
cp "$TAKOPI_PROJECT_PATH/.env" "$TAKOPI_WORKTREE_PATH/.env"
cp -r "$TAKOPI_PROJECT_PATH/.codex" "$TAKOPI_WORKTREE_PATH/.codex"
```

The script only runs when the worktree is first created. Subsequent messages to the same branch skip it.

## Ignore `.worktrees/` in git status

If you use the default `.worktrees/` directory inside the repo, add it to a gitignore.
One option is a global ignore:

```sh
git config --global core.excludesfile ~/.config/git/ignore
echo ".worktrees/" >> ~/.config/git/ignore
```

## Context persistence

When project/worktree context is active, Takopi includes a `ctx:` footer in messages.
When you reply, this context carries forward (you usually don’t need to repeat `/<project-alias> @branch`).

## Related

- [Context resolution](../reference/context-resolution.md)
