# Projects

Projects let you route messages to repos from anywhere using `/alias`.

## Register a repo as a project

```sh
cd ~/dev/happy-gadgets
takopi init happy-gadgets
```

This adds a project to your config:

=== "takopi config"

    ```sh
    takopi config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    ```

## Target a project from chat

Send:

```
/happy-gadgets pinky-link two threads
```

## Project-specific settings

Projects can override global defaults:

=== "takopi config"

    ```sh
    takopi config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    takopi config set projects.happy-gadgets.default_engine "claude"
    takopi config set projects.happy-gadgets.worktrees_dir ".worktrees"
    takopi config set projects.happy-gadgets.worktree_base "master"
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    default_engine = "claude"
    worktrees_dir = ".worktrees"
    worktree_base = "master"
    ```

If you expect to edit config while Takopi is running, enable hot reload:

=== "takopi config"

    ```sh
    takopi config set watch_config true
    ```

=== "toml"

    ```toml
    watch_config = true
    ```

## Set a default project

If you mostly work in one repo:

=== "takopi config"

    ```sh
    takopi config set default_project "happy-gadgets"
    ```

=== "toml"

    ```toml
    default_project = "happy-gadgets"
    ```

## Related

- [Context resolution](../reference/context-resolution.md)
- [Worktrees](worktrees.md)
