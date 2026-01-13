# Projects

Projects let you route messages to repos from anywhere using `/alias`.

## Register a repo as a project

```sh
cd ~/dev/happy-gadgets
takopi init happy-gadgets
```

This adds a project to your config:

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

```toml
[projects.happy-gadgets]
path = "~/dev/happy-gadgets"
default_engine = "claude"
worktrees_dir = ".worktrees"
worktree_base = "master"
```

If you expect to edit config while Takopi is running, enable hot reload:

```toml
watch_config = true
```

## Set a default project

If you mostly work in one repo:

```toml
default_project = "happy-gadgets"
```

## Related

- [Context resolution](../reference/context-resolution.md)
- [Worktrees](worktrees.md)

