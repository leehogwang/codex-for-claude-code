#!/usr/bin/env bash
set -euo pipefail

CLIPROXY_DIR="${CLIPROXY_DIR:-$HOME/CLIProxyAPI}"
CLIPROXY_HOST="${CLIPROXY_HOST:-127.0.0.1}"
CLIPROXY_PORT="${CLIPROXY_PORT:-8317}"
START_SCRIPT="${START_SCRIPT:-$CLIPROXY_DIR/scripts/start-proxy.sh}"
API_KEY_FILE="${API_KEY_FILE:-$CLIPROXY_DIR/runtime/client-api-key.txt}"
AUTH_DIR="${AUTH_DIR:-$CLIPROXY_DIR/runtime/auth}"

resolve_real_claude() {
  if [[ -n "${REAL_CLAUDE:-}" ]]; then
    printf '%s\n' "$REAL_CLAUDE"
    return 0
  fi

  if [[ -x "$HOME/.local/bin/claude" ]]; then
    printf '%s\n' "$HOME/.local/bin/claude"
    return 0
  fi

  if command -v claude >/dev/null 2>&1; then
    command -v claude
    return 0
  fi

  echo "Could not find the Claude Code binary." >&2
  echo "Set REAL_CLAUDE to the path of your Claude executable." >&2
  return 1
}

has_provider_auth() {
  local provider="$1"
  find "$AUTH_DIR" -maxdepth 1 -type f -name '*.json' -print0 2>/dev/null | \
    xargs -r -0 grep -l "\"type\":\"$provider\"" 2>/dev/null | grep -q .
}

should_passthrough_to_claude() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --help|-h|help|--version|-v)
        return 0
        ;;
    esac
  done

  return 1
}

ensure_proxy_running() {
  if ! ss -ltn | grep -q ":${CLIPROXY_PORT} "; then
    "$START_SCRIPT" >/dev/null
  fi
}

require_provider_auth() {
  local provider="$1"
  if has_provider_auth "$provider"; then
    return 0
  fi

  echo "${provider^} OAuth is not configured for CLIProxyAPI." >&2
  echo "Run the login flow for your CLIProxyAPI setup first." >&2
  return 1
}

load_proxy_env() {
  if [[ ! -f "$API_KEY_FILE" ]]; then
    echo "Missing API key file: $API_KEY_FILE" >&2
    return 1
  fi

  export ANTHROPIC_BASE_URL="http://${CLIPROXY_HOST}:${CLIPROXY_PORT}"
  export ANTHROPIC_AUTH_TOKEN
  ANTHROPIC_AUTH_TOKEN="$(cat "$API_KEY_FILE")"
}
