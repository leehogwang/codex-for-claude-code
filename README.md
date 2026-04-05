# codex-for-claude-code

Utilities for running Codex-backed workflows inside Claude Code through CLIProxyAPI.

## Included

- `scripts/claude-mix`
  - Launches Claude Code with only the `haiku` slot routed to Codex.
- `scripts/claude-codex-only`
  - Launches Claude Code with `haiku`, `sonnet`, and `opus` all routed to Codex aliases.
- `scripts/claude-proxy-common.sh`
  - Shared bootstrap for proxy startup, auth checks, and environment setup.
- `.claude/commands/compare.md`
  - Adds `/compare <prompt>` for side-by-side answer comparison.
- `.claude/commands/compare.py`
  - Compare engine with environment-variable overrides for binaries and models.

## Requirements

- Claude Code installed locally
- CLIProxyAPI installed and reachable
- Codex OAuth configured in CLIProxyAPI
- Claude OAuth configured as well if you want to use `claude-mix` or default Sonnet-backed comparisons

The wrappers assume this layout by default:

- `CLIProxyAPI` at `~/CLIProxyAPI`
- Claude Code binary at `~/.local/bin/claude`

Override any of these with environment variables:

- `CLIPROXY_DIR`
- `CLIPROXY_HOST`
- `CLIPROXY_PORT`
- `REAL_CLAUDE`
- `API_KEY_FILE`
- `AUTH_DIR`

## CLIProxyAPI aliases

These wrappers expect Codex aliases such as `codex-haiku`, `codex-sonnet`, and `codex-opus` to exist in your CLIProxyAPI config. A minimal example:

```yaml
oauth-model-alias:
  codex:
    - name: "gpt-5.4"
      alias: "codex-haiku"
    - name: "gpt-5.4"
      alias: "codex-sonnet"
    - name: "gpt-5.4"
      alias: "codex-opus"

payload:
  override:
    - models:
        - name: "codex-*"
          protocol: "codex"
      params:
        service_tier: "priority"
```

## Wrapper profiles

### `claude-mix`

Keeps Claude as the default backend while routing only the Haiku slot through Codex.

```bash
scripts/claude-mix --dangerously-skip-permissions
```

Optional override:

```bash
HAIKU_MODEL='codex-haiku(xhigh)' scripts/claude-mix
```

### `claude-codex-only`

Routes all Claude Code slots to Codex aliases while preserving the familiar slot names:

- `haiku` -> `codex-haiku(medium)`
- `sonnet` -> `codex-sonnet(high)`
- `opus` -> `codex-opus(xhigh)`

```bash
scripts/claude-codex-only --dangerously-skip-permissions
```

Optional overrides:

```bash
HAIKU_MODEL='codex-haiku(low)' \
SONNET_MODEL='codex-sonnet(medium)' \
OPUS_MODEL='codex-opus(high)' \
scripts/claude-codex-only
```

## `/compare`

Inside a Claude Code session using this repo:

```text
/compare Refactor this Python function for readability without changing behavior.
```

Another example:

```text
/compare Explain the tradeoffs between React Query and SWR for a dashboard app.
```

The command returns:

- `=== Sonnet ===`
- `=== Codex ===`
- `=== Judge ===`

Judge summarizes which answer looks stronger and why.

### `/compare` overrides

The compare command defaults to:

- Sonnet side: local `claude` binary with model `sonnet`
- Codex side: local `claude-mix` binary with model `codex-haiku(xhigh)`
- Judge side: same binary/model as the Codex side

You can override any of them:

```bash
export CLAUDE_COMPARE_CLAUDE_BIN="$HOME/.local/bin/claude"
export CLAUDE_COMPARE_CODEX_BIN="$PWD/scripts/claude-codex-only"
export CLAUDE_COMPARE_JUDGE_BIN="$PWD/scripts/claude-codex-only"
export CLAUDE_COMPARE_SONNET_MODEL="sonnet"
export CLAUDE_COMPARE_CODEX_MODEL="codex-sonnet(high)"
export CLAUDE_COMPARE_JUDGE_MODEL="codex-opus(xhigh)"
```

## Notes

- These scripts are shell wrappers, not a packaged installer.
- Long `/compare` runs can take time. The custom command requests a 15-minute Bash timeout so Claude does not stop it after the default 3 minutes.
