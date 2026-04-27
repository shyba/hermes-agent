# Context Mode Integration Plan

Goal: integrate https://github.com/mksglu/context-mode without vendoring code
or maintaining a long-lived fork.

## Source Layout

- `vendor/context-mode` is a Git submodule pinned by Hermes.
- Fresh clone:

  ```bash
  git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git
  ```

- Existing clone:

  ```bash
  git submodule update --init --recursive vendor/context-mode
  ```

## Build Helpers

- `scripts/context-mode init` initializes the submodule.
- `scripts/context-mode doctor` runs the vendored CLI bundle.
- `scripts/context-mode build` installs dependencies and rebuilds bundles.
- `scripts/build-context-mode` is the short CI/fresh-clone wrapper.
- `scripts/context-mode install-bin` creates a local wrapper at
  `$HERMES_HOME/bin/context-mode`.

The integration should resolve context-mode in this order:

1. `HERMES_CONTEXT_MODE_COMMAND`
2. `$HERMES_HOME/bin/context-mode`
3. `vendor/context-mode/cli.bundle.mjs`
4. `context-mode` on `PATH`

## Hermes Integration Shape

Prefer new files:

- `agent/context_mode_bridge.py`
- `plugins/context_engine/context_mode/__init__.py`
- `plugins/context_engine/context_mode/plugin.yaml`
- `plugins/context-mode/__init__.py`
- `plugins/context-mode/plugin.yaml`
- `tests/agent/test_context_mode_bridge.py`
- `tests/agent/test_context_mode_engine.py`
- `tests/plugins/test_context_mode_plugin.py`

Avoid editing `agent/context_compressor.py` unless the existing
`ContextEngine` interface cannot express a required behavior.

## Runtime Behavior

- Session start: notify context-mode and load its compact session guide.
- Tool calls: send before/after metadata to context-mode.
- Tool results: transform large refetchable outputs into tombstones before
  they enter the model context.
- Pre-compress: ask context-mode for a session guide and use that as the
  compaction summary.
- MiniMax: run deterministic reorganization first because MiniMax bills per
  request. Summarization is the fallback, not the first move.

## Rebase Strategy

- Keep third-party code in the submodule.
- Keep Hermes glue in new plugin/bridge files.
- Put independent tests in new files.
- Keep edits to `run_agent.py` and `agent/context_compressor.py` as small
  hook invocations only.
