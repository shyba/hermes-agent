#!/usr/bin/env bash
# Fleet-safe updater for this Hermes checkout.
#
# One command from a cloned checkout:
#   ./update.sh
#
# What it does by default:
#   1. Fast-forward this git checkout and refresh submodules.
#   2. Ensure a Hermes Python environment can load the MCP extra.
#   3. Install user-level `hermes`, `hermes-harness`, and `harness-check` wrappers.
#   4. Build/install/configure the vendored context-mode MCP server.
#   5. Enable the bundled harness plugin for OpenSpec-backed runs.
#   6. Verify context-mode through both raw MCP and Hermes's MCP command.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

DO_GIT=true
DO_PYTHON_DEPS=true
DO_HERMES_LINK=true
DO_CONTEXT_MODE=true
DO_HARNESS_PLUGIN=true
DO_VERIFY=true
ORIGINAL_ARGS=("$@")

log() {
    printf '[update] %s\n' "$*" >&2
}

warn() {
    printf '[update] warning: %s\n' "$*" >&2
}

die() {
    printf '[update] error: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
Usage: ./update.sh [options]

Options:
  --skip-git            Do not fetch/pull the checkout.
  --skip-python-deps    Do not create/update a Python venv.
  --skip-hermes-link    Do not install ~/.local/bin/hermes and harness wrappers.
  --skip-context-mode   Do not build/install/configure context-mode.
  --skip-harness-plugin Do not enable the bundled harness plugin.
  --no-verify           Skip verification checks.
  --verify-only         Run verification only.
  -h, --help            Show this help.

Environment:
  HERMES_HOME           Hermes data/config directory (default: ~/.hermes).
  HERMES_PYTHON         Python executable to use for the hermes wrapper.
  HERMES_UPDATE_GIT     Set to 0/false/no to skip git update.
USAGE
}

is_false() {
    case "${1:-}" in
        0|false|FALSE|no|NO|off|OFF) return 0 ;;
        *) return 1 ;;
    esac
}

is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *"com.termux/files/usr"* ]]
}

command_link_dir() {
    if is_termux && [ -n "${PREFIX:-}" ]; then
        printf '%s\n' "$PREFIX/bin"
    else
        printf '%s\n' "$HOME/.local/bin"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-git|--no-git)
            DO_GIT=false
            shift
            ;;
        --skip-python-deps)
            DO_PYTHON_DEPS=false
            shift
            ;;
        --skip-hermes-link)
            DO_HERMES_LINK=false
            shift
            ;;
        --skip-context-mode)
            DO_CONTEXT_MODE=false
            shift
            ;;
        --skip-harness-plugin)
            DO_HARNESS_PLUGIN=false
            shift
            ;;
        --no-verify)
            DO_VERIFY=false
            shift
            ;;
        --verify-only)
            DO_GIT=false
            DO_PYTHON_DEPS=false
            DO_HERMES_LINK=false
            DO_CONTEXT_MODE=false
            DO_HARNESS_PLUGIN=false
            DO_VERIFY=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            die "unknown option: $1"
            ;;
    esac
done

if is_false "${HERMES_UPDATE_GIT:-}"; then
    DO_GIT=false
fi

run_git_update() {
    if [ "$DO_GIT" != true ]; then
        log "skipping git update"
        return
    fi

    if ! git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        warn "$ROOT_DIR is not a git checkout; skipping git update"
        return
    fi

    local before after upstream
    before="$(git -C "$ROOT_DIR" rev-parse HEAD)"

    log "fetching repository and submodules"
    git -C "$ROOT_DIR" fetch --prune --recurse-submodules

    upstream="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    if [ -n "$upstream" ]; then
        log "fast-forwarding from $upstream"
        git -C "$ROOT_DIR" pull --ff-only --recurse-submodules
    else
        warn "current branch has no upstream; leaving checkout at current commit"
    fi

    git -C "$ROOT_DIR" submodule sync --recursive
    git -C "$ROOT_DIR" submodule update --init --recursive vendor/context-mode

    after="$(git -C "$ROOT_DIR" rev-parse HEAD)"
    if [ "$before" != "$after" ] && [ "${HERMES_UPDATE_REEXECED:-0}" != "1" ]; then
        log "checkout changed; re-executing latest update.sh"
        HERMES_UPDATE_REEXECED=1 exec "$ROOT_DIR/update.sh" "${ORIGINAL_ARGS[@]}"
    fi
}

python_has_mcp_deps() {
    "$1" -c 'import mcp, yaml' >/dev/null 2>&1
}

first_existing_python() {
    local candidate
    for candidate in \
        "${HERMES_PYTHON:-}" \
        "$HERMES_HOME/hermes-agent/venv/bin/python3" \
        "$ROOT_DIR/.venv/bin/python3" \
        "$ROOT_DIR/venv/bin/python3"
    do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    command -v python3 2>/dev/null || true
}

select_hermes_python() {
    local candidate

    if [ -n "${HERMES_PYTHON:-}" ]; then
        [ -x "$HERMES_PYTHON" ] || die "HERMES_PYTHON is not executable: $HERMES_PYTHON"
        printf '%s\n' "$HERMES_PYTHON"
        return
    fi

    for candidate in \
        "$HERMES_HOME/hermes-agent/venv/bin/python3" \
        "$ROOT_DIR/.venv/bin/python3" \
        "$ROOT_DIR/venv/bin/python3"
    do
        if [ -x "$candidate" ] && python_has_mcp_deps "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    done

    if [ "$DO_PYTHON_DEPS" = true ]; then
        candidate="$ROOT_DIR/.venv/bin/python3"
        if [ ! -x "$candidate" ]; then
            local bootstrap_python
            bootstrap_python="$(command -v python3 2>/dev/null || true)"
            [ -n "$bootstrap_python" ] || die "python3 not found; install Python 3.11+ first"
            log "creating $ROOT_DIR/.venv"
            "$bootstrap_python" -m venv "$ROOT_DIR/.venv"
        fi

        log "installing Hermes MCP Python extra into $ROOT_DIR/.venv"
        "$candidate" -m pip install --upgrade pip setuptools wheel
        "$candidate" -m pip install -e "$ROOT_DIR[mcp]"
        printf '%s\n' "$candidate"
        return
    fi

    candidate="$(first_existing_python)"
    [ -n "$candidate" ] || die "python3 not found"
    warn "selected Python may be missing MCP deps: $candidate"
    printf '%s\n' "$candidate"
}

install_hermes_link() {
    if [ "$DO_HERMES_LINK" != true ]; then
        log "skipping hermes command wrapper"
        return
    fi

    local py link_dir link harness_link check_link marker backup tmp
    py="$(select_hermes_python)"
    link_dir="$(command_link_dir)"
    link="$link_dir/hermes"
    harness_link="$link_dir/hermes-harness"
    check_link="$link_dir/harness-check"
    marker="hermes-local-compaction-override-2026-04-27"

    mkdir -p "$link_dir"

    if { [ -e "$link" ] || [ -L "$link" ]; } && ! grep -q "$marker" "$link" 2>/dev/null; then
        backup="$link.bak.$(date +%Y%m%d%H%M%S)"
        cp -P "$link" "$backup"
        log "backed up existing hermes command to $backup"
    fi

    tmp="$(mktemp "$link.tmp.XXXXXX")"
    {
        printf '#!/usr/bin/env bash\n'
        printf '# rollback/original: restore the newest %s.bak.* file, or reinstall Hermes normally.\n' "$link"
        printf 'export HERMES_LOCAL_OVERRIDE=%q\n' "$ROOT_DIR"
        printf 'export HERMES_LOCAL_MARKER=%q\n' "$marker"
        printf 'export HERMES_TUI="${HERMES_TUI:-1}"\n'
        printf 'if [ "${1:-}" = "--local-marker" ] || [ "${1:-}" = "--which-hermes" ]; then\n'
        printf '  printf "Hermes local override: %%s\\n" "$HERMES_LOCAL_OVERRIDE"\n'
        printf '  printf "Marker: %%s\\n" "$HERMES_LOCAL_MARKER"\n'
        printf '  exit 0\n'
        printf 'fi\n'
        printf 'exec %q %q "$@"\n' "$py" "$ROOT_DIR/hermes"
    } > "$tmp"
    chmod +x "$tmp"
    mv "$tmp" "$link"

    if { [ -e "$harness_link" ] || [ -L "$harness_link" ]; } && ! grep -q "$marker" "$harness_link" 2>/dev/null; then
        backup="$harness_link.bak.$(date +%Y%m%d%H%M%S)"
        cp -P "$harness_link" "$backup"
        log "backed up existing hermes-harness command to $backup"
    fi

    tmp="$(mktemp "$harness_link.tmp.XXXXXX")"
    {
        printf '#!/usr/bin/env bash\n'
        printf '# rollback/original: restore the newest %s.bak.* file, or reinstall Hermes normally.\n' "$harness_link"
        printf 'export HERMES_LOCAL_OVERRIDE=%q\n' "$ROOT_DIR"
        printf 'export HERMES_LOCAL_MARKER=%q\n' "$marker"
        printf 'export PYTHONPATH="$HERMES_LOCAL_OVERRIDE${PYTHONPATH:+:$PYTHONPATH}"\n'
        printf 'exec %q -m plugins.harness.cli "$@"\n' "$py"
    } > "$tmp"
    chmod +x "$tmp"
    mv "$tmp" "$harness_link"

    if { [ -e "$check_link" ] || [ -L "$check_link" ]; } && ! grep -q "$marker" "$check_link" 2>/dev/null; then
        backup="$check_link.bak.$(date +%Y%m%d%H%M%S)"
        cp -P "$check_link" "$backup"
        log "backed up existing harness-check command to $backup"
    fi

    tmp="$(mktemp "$check_link.tmp.XXXXXX")"
    {
        printf '#!/usr/bin/env bash\n'
        printf '# rollback/original: restore the newest %s.bak.* file, or reinstall Hermes normally.\n' "$check_link"
        printf 'export HERMES_LOCAL_OVERRIDE=%q\n' "$ROOT_DIR"
        printf 'export HERMES_LOCAL_MARKER=%q\n' "$marker"
        printf 'export PYTHONPATH="$HERMES_LOCAL_OVERRIDE${PYTHONPATH:+:$PYTHONPATH}"\n'
        printf 'exec %q -m plugins.harness.cli check "$@"\n' "$py"
    } > "$tmp"
    chmod +x "$tmp"
    mv "$tmp" "$check_link"

    export PATH="$link_dir:$PATH"
    log "installed Hermes wrappers at $link, $harness_link, and $check_link using $py"
}

install_context_mode() {
    if [ "$DO_CONTEXT_MODE" != true ]; then
        log "skipping context-mode install"
        return
    fi

    log "installing context-mode"
    "$ROOT_DIR/scripts/context-mode" install
}

enable_harness_plugin() {
    if [ "$DO_HARNESS_PLUGIN" != true ]; then
        log "skipping harness plugin enable"
        return
    fi

    if [ ! -d "$ROOT_DIR/plugins/harness" ]; then
        warn "bundled harness plugin not present; skipping enable"
        return
    fi

    if ! command -v hermes >/dev/null 2>&1; then
        warn "hermes command not found on PATH; cannot enable harness plugin"
        return
    fi

    log "enabling bundled harness plugin"
    if ! hermes plugins enable harness; then
        warn "could not enable harness plugin; run manually: hermes plugins enable harness"
    fi
}

verify_update() {
    if [ "$DO_VERIFY" != true ]; then
        log "skipping verification"
        return
    fi

    log "verifying context-mode"
    "$ROOT_DIR/scripts/context-mode" verify

    if command -v hermes >/dev/null 2>&1; then
        log "verifying Hermes MCP registration"
        hermes mcp test context-mode
    else
        warn "hermes command not found on PATH; raw context-mode verification passed"
    fi

    if command -v hermes-harness >/dev/null 2>&1 && command -v harness-check >/dev/null 2>&1; then
        log "verifying harness commands"
        hermes-harness status >/dev/null
        harness-check --help >/dev/null
    else
        warn "harness commands not found on PATH; run ./update.sh without --skip-hermes-link"
    fi
}

main() {
    cd "$ROOT_DIR"
    run_git_update
    install_hermes_link
    install_context_mode
    enable_harness_plugin
    verify_update
    log "update complete"
}

main
