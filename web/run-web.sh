#!/bin/bash
# Launchd entry point for mop-web. Fetches the MOP API key from the
# password-store before exec'ing uvicorn, so the site can call Haiku
# from the playground.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Resolve the uv binary the same way patchbay's run.sh does.
if [[ -n "${UV_BIN:-}" && -x "$UV_BIN" ]]; then
    uv_bin="$UV_BIN"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
    uv_bin="$HOME/.local/bin/uv"
elif [[ -x "/opt/homebrew/bin/uv" ]]; then
    uv_bin="/opt/homebrew/bin/uv"
elif [[ -x "/usr/local/bin/uv" ]]; then
    uv_bin="/usr/local/bin/uv"
elif uv_bin="$(command -v uv 2>/dev/null)"; then
    :
else
    echo "uv not found; sleeping 30s before exit so launchd doesn't tight-loop" >&2
    sleep 30
    exit 1
fi

# Pull the Anthropic key from pass. Keep it scoped to MOP_ANTHROPIC_API_KEY
# so it can't leak into spawned subprocess env (matches patchbay's pattern).
if command -v pass &>/dev/null && [[ -z "${MOP_ANTHROPIC_API_KEY:-}" ]]; then
    _key="$(pass show mop-anthropic-api-key 2>/dev/null || true)"
    [[ -n "$_key" ]] && export MOP_ANTHROPIC_API_KEY="$_key"
fi
unset ANTHROPIC_API_KEY

cd "$REPO_ROOT"
exec "$uv_bin" run --project "$REPO_ROOT" "$REPO_ROOT/web/app.py"
