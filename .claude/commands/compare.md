---
description: Compare Sonnet and Codex on the same prompt
argument-hint: <prompt>
allowed-tools: Bash
---
You are handling a user-invoked compare command.

The user's current `/compare` request and its arguments are already in the session transcript.

Do this:
1. Use Bash exactly once with a timeout of `900000` milliseconds and `run_in_background` set to `false` to run:
   `REPO_ROOT="$(git rev-parse --show-toplevel)"; python3 "$REPO_ROOT/.claude/commands/compare.py" --session-id "${CLAUDE_SESSION_ID}" --command-name "/compare"`
2. Wait for it to finish.
3. If it succeeds, reply with exactly the script output and nothing else.
4. If it fails, reply with a short error summary and include the stderr.

Rules:
- Do not ask follow-up questions.
- Do not summarize the report.
- Do not add commentary before or after the report.
- Do not pass the user prompt on the command line.
- Do not use the Bash tool's default timeout.
