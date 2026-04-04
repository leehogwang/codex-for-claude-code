# codex-for-claude-code

Utilities for running Codex-backed workflows inside Claude Code.

## Included

- `scripts/claude-mix`
  - Launches Claude Code with `haiku` routed through CLIProxyAPI to `codex-haiku(xhigh)`.
- `.claude/commands/compare.md`
  - Adds a `/compare <prompt>` slash command for comparing Sonnet vs Codex on the same task.
- `.claude/commands/compare.py`
  - The compare engine used by the slash command. It runs Sonnet, Codex, and a Judge pass.

## `/compare`

Inside a Claude Code session in this repo:

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

## Requirements

- Claude Code installed locally
- `claude-mix` available or configured through `scripts/claude-mix`
- CLIProxyAPI configured with both Claude and Codex OAuth

## Notes

- The command is tuned for Claude Code custom commands, not generic shell use.
- Long comparisons can take time. The command requests a 15-minute Bash timeout so Claude does not stop it after the default 3 minutes.
