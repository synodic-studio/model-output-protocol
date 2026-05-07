#!/bin/bash
# Launchd entry point for mop-web. Fetches the MOP API key from the
# password-store before exec'ing uvicorn, so the site can call Haiku
# from the playground.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Resolve the uv binary across common install locations.
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

# If `pass` is available, pull the Anthropic key from password-store and
# expose it as MOP_ANTHROPIC_API_KEY. Scoping the name (vs ANTHROPIC_API_KEY)
# keeps it from leaking into agent subprocesses that might inherit env.
if command -v pass &>/dev/null && [[ -z "${MOP_ANTHROPIC_API_KEY:-}" ]]; then
    _key="$(pass show mop-anthropic-api-key 2>/dev/null || true)"
    [[ -n "$_key" ]] && export MOP_ANTHROPIC_API_KEY="$_key"
fi
unset ANTHROPIC_API_KEY

# Bind to all interfaces by default for LAN/Tailscale access. Override
# by exporting MOP_WEB_HOST=127.0.0.1 (or any specific iface) before
# invoking this script.
export MOP_WEB_HOST="${MOP_WEB_HOST:-0.0.0.0}"
export MOP_WEB_PORT="${MOP_WEB_PORT:-7731}"

cd "$REPO_ROOT"
exec "$uv_bin" run --project "$REPO_ROOT" "$REPO_ROOT/web/app.py"
