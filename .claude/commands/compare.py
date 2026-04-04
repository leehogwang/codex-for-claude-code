#!/usr/bin/env python3

import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


CLAUDE_BIN = Path(
    os.environ.get("CLAUDE_COMPARE_CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
)
CLAUDE_MIX_BIN = Path(
    os.environ.get("CLAUDE_COMPARE_CLAUDE_MIX_BIN", str(Path.home() / ".local/bin/claude-mix"))
)
CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_COMPARE_PROJECTS_DIR", str(Path.home() / ".claude/projects"))
)
READ_ONLY_TOOLS = "Read,Grep,Glob"
MAX_ERROR_CHARS = 2000
SESSION_PROMPT_RETRY_COUNT = 120
SESSION_PROMPT_RETRY_SLEEP_SECONDS = 0.5
VISIBLE_TRANSCRIPT_MAX_MESSAGES = 40
VISIBLE_TRANSCRIPT_MAX_CHARS = 12000
CURRENT_PROMPT_LOOKBACK_SECONDS = 15

COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
COMPARE_REPORT_MARKERS = (
    "=== Compare ===",
    "=== Sonnet ===",
    "=== Codex ===",
    "=== Judge ===",
)


@dataclass
class ModelResult:
    label: str
    success: bool
    output: str
    error: str = ""


def detect_workspace_root() -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=os.getcwd(),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Path.cwd()

    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip())

    return Path.cwd()


WORKSPACE_ROOT = detect_workspace_root()


def read_prompt(argv_tail: list[str]) -> str:
    prompt = sys.stdin.read().strip()
    if prompt:
        return prompt

    if argv_tail:
        return " ".join(argv_tail).strip()

    return ""


def parse_cli_args(argv: list[str]) -> tuple[str | None, str, datetime | None, list[str]]:
    session_id = None
    command_name = "/compare"
    started_at = None
    idx = 1

    while idx < len(argv):
        arg = argv[idx]
        if arg == "--session-id" and idx + 1 < len(argv):
            session_id = argv[idx + 1].strip() or None
            idx += 2
            continue
        if arg == "--command-name" and idx + 1 < len(argv):
            command_name = argv[idx + 1].strip() or command_name
            idx += 2
            continue
        if arg == "--started-at" and idx + 1 < len(argv):
            started_at = parse_started_at(argv[idx + 1].strip())
            idx += 2
            continue
        break

    return session_id, command_name, started_at, argv[idx:]


def parse_started_at(raw_value: str) -> datetime | None:
    if not raw_value:
        return None

    try:
        return datetime.strptime(raw_value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def locate_session_file(session_id: str) -> Path | None:
    if not session_id or not CLAUDE_PROJECTS_DIR.exists():
        return None

    matches = list(CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    if matches:
        return matches[0]

    return None


def load_session_entries(session_id: str) -> list[dict]:
    session_file = locate_session_file(session_id)
    if session_file is None:
        return []

    entries: list[dict] = []
    try:
        for line in session_file.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []

    return entries


def strip_command_prefix(raw_prompt: str, command_name: str) -> str:
    text = raw_prompt.strip()
    if not text:
        return ""

    if text == command_name:
        return ""

    prefix = f"{command_name} "
    if text.startswith(prefix):
        return text[len(prefix) :].lstrip()

    return text


def extract_command_args_from_user_entry(entry: dict, command_name: str) -> str:
    if entry.get("type") != "user":
        return ""

    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        return ""

    name_match = COMMAND_NAME_RE.search(content)
    args_match = COMMAND_ARGS_RE.search(content)
    if name_match is None or args_match is None:
        return ""
    if name_match.group(1).strip() != command_name:
        return ""

    return args_match.group(1).strip()


def parse_entry_timestamp(entry: dict) -> datetime | None:
    raw_value = entry.get("timestamp")
    if not isinstance(raw_value, str) or not raw_value:
        return None

    normalized = raw_value.replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)

    return timestamp.astimezone(timezone.utc)


def is_recent_command_entry(entry: dict, invocation_started_at: datetime) -> bool:
    timestamp = parse_entry_timestamp(entry)
    if timestamp is None:
        return False

    return timestamp >= invocation_started_at - timedelta(seconds=CURRENT_PROMPT_LOOKBACK_SECONDS)


def entry_contains_compare_report(entry: dict) -> bool:
    stack = [entry]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if any(marker in current for marker in COMPARE_REPORT_MARKERS):
                return True
            continue
        if isinstance(current, list):
            stack.extend(current)
            continue
        if isinstance(current, dict):
            stack.extend(current.values())
    return False


def find_current_compare_command(
    entries: list[dict], command_name: str, invocation_started_at: datetime
) -> tuple[int, str]:
    recent_commands: list[tuple[int, str]] = []
    compare_report_indices = [
        idx for idx, entry in enumerate(entries) if entry_contains_compare_report(entry)
    ]

    for idx, entry in enumerate(entries):
        prompt = extract_command_args_from_user_entry(entry, command_name)
        if not prompt or not is_recent_command_entry(entry, invocation_started_at):
            continue
        recent_commands.append((idx, prompt))

    if not recent_commands:
        return -1, ""

    for idx, prompt in reversed(recent_commands):
        if not any(report_idx > idx for report_idx in compare_report_indices):
            return idx, prompt

    return recent_commands[-1]


def extract_prompt_from_session(
    session_id: str, command_name: str, invocation_started_at: datetime
) -> tuple[int, str]:
    for attempt in range(SESSION_PROMPT_RETRY_COUNT):
        entries = load_session_entries(session_id)
        anchor_index, prompt = find_current_compare_command(
            entries, command_name, invocation_started_at
        )
        if prompt:
            return anchor_index, prompt
        if attempt + 1 < SESSION_PROMPT_RETRY_COUNT:
            time.sleep(SESSION_PROMPT_RETRY_SLEEP_SECONDS)

    return -1, ""


def extract_visible_text_from_entry(entry: dict) -> str:
    message = entry.get("message") or {}
    content = message.get("content")
    entry_type = entry.get("type")

    if entry_type == "user":
        if not isinstance(content, str):
            return ""
        if "<command-message>" in content:
            return ""
        text = content.strip()
        if any(marker in text for marker in COMPARE_REPORT_MARKERS):
            return ""
        return text

    if not isinstance(content, list):
        return ""

    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())

    text = "\n".join(texts).strip()
    if any(marker in text for marker in COMPARE_REPORT_MARKERS):
        return ""
    return text


def build_recent_context(session_id: str | None, anchor_index: int) -> str:
    if not session_id:
        return ""

    entries = load_session_entries(session_id)
    if not entries:
        return ""

    if anchor_index < 0:
        return ""
    selected: list[str] = []
    total_chars = 0

    for entry in reversed(entries[:anchor_index]):
        entry_type = entry.get("type")
        if entry_type not in {"user", "assistant"}:
            continue
        if entry.get("isMeta") is True:
            continue

        text = extract_visible_text_from_entry(entry)
        if not text:
            continue

        label = "User" if entry_type == "user" else "Assistant"
        block = f"{label}: {text}"
        selected.append(block)
        total_chars += len(block)
        if (
            len(selected) >= VISIBLE_TRANSCRIPT_MAX_MESSAGES
            or total_chars >= VISIBLE_TRANSCRIPT_MAX_CHARS
        ):
            break

    if not selected:
        return ""

    selected.reverse()
    context = "\n\n".join(selected)
    if len(context) > VISIBLE_TRANSCRIPT_MAX_CHARS:
        context = context[-VISIBLE_TRANSCRIPT_MAX_CHARS :]

    return context


def truncate_error(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_ERROR_CHARS:
        return text
    return text[: MAX_ERROR_CHARS - 3] + "..."


def build_answer_prompt(user_prompt: str, recent_context: str) -> str:
    parts = [
        "Answer the user's request directly.",
        "Treat the supplied transcript as the current session context.",
        "You are the acting assistant inside this CLI session, not an external adviser.",
        "Do not tell the user to ask Claude, ChatGPT, Codex, or any other model.",
        "Do not say you will hand this off, pass this to another assistant, or write a prompt for someone else unless the user explicitly asks for that.",
        "If the user is asking whether something should be built, analyzed, or implemented, speak as the assistant who would do that work here.",
        "If repository context matters, inspect the current repository with read-only tools.",
        "Do not modify files or suggest that you already changed anything.",
        "Keep the answer focused and practical.",
    ]

    if recent_context:
        parts.append(f"Visible conversation transcript so far:\n{recent_context}")

    parts.append(f"User request:\n{user_prompt}")
    return "\n\n".join(parts)


def build_judge_prompt(user_prompt: str, sonnet_output: str, codex_output: str) -> str:
    return (
        "Compare the two candidate answers for the user's request.\n"
        "Choose one of: Sonnet, Codex, 상황별로 다름.\n"
        "Use 상황별로 다름 only when the tradeoff is genuinely split.\n"
        "Judge using these criteria: 정확성, 구체성, 실행가능성, 코드 작업 적합성.\n"
        "Reply in Korean using exactly this structure:\n"
        "더 나아 보이는 답변: <Sonnet|Codex|상황별로 다름>\n"
        "이유:\n"
        "- <reason 1>\n"
        "- <reason 2>\n"
        "- <reason 3>\n"
        "점수:\n"
        "| 항목 | Sonnet | Codex |\n"
        "| --- | ---: | ---: |\n"
        "| 정확성 | <1-10> | <1-10> |\n"
        "| 구체성 | <1-10> | <1-10> |\n"
        "| 실행가능성 | <1-10> | <1-10> |\n"
        "| 코드 작업 적합성 | <1-10> | <1-10> |\n\n"
        f"User request:\n{user_prompt}\n\n"
        f"Sonnet answer:\n{sonnet_output}\n\n"
        f"Codex answer:\n{codex_output}"
    )


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TERM"] = "dumb"
    return subprocess.run(
        command,
        cwd=str(WORKSPACE_ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def run_answer_model(
    label: str,
    binary: Path,
    model: str,
    prompt: str,
    recent_context: str,
) -> ModelResult:
    command = [
        str(binary),
        "-p",
        "--model",
        model,
        "--tools",
        READ_ONLY_TOOLS,
        "--allowed-tools",
        READ_ONLY_TOOLS,
        "--permission-mode",
        "plan",
        "--disable-slash-commands",
        "--no-session-persistence",
        build_answer_prompt(prompt, recent_context),
    ]
    try:
        completed = run_command(command)
    except OSError as exc:
        return ModelResult(label=label, success=False, output="", error=f"Failed to launch model process: {exc}")

    if completed.returncode != 0:
        error_text = completed.stderr.strip() or completed.stdout.strip() or f"Process exited with status {completed.returncode}."
        return ModelResult(label=label, success=False, output="", error=truncate_error(error_text))

    return ModelResult(label=label, success=True, output=completed.stdout.strip())


def run_judge_model(prompt: str, sonnet_output: str, codex_output: str) -> str | None:
    command = [
        str(CLAUDE_MIX_BIN),
        "-p",
        "--model",
        "codex-haiku(xhigh)",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--disable-slash-commands",
        "--no-session-persistence",
        build_judge_prompt(prompt, sonnet_output, codex_output),
    ]

    try:
        completed = run_command(command)
    except OSError:
        return None

    if completed.returncode != 0:
        return None

    text = completed.stdout.strip()
    if not text:
        return None

    return text


def format_result(result: ModelResult) -> str:
    if result.success:
        return result.output or "(빈 응답)"
    return f"[오류]\n{result.error or '알 수 없는 오류'}"


def format_judge_block(judge_text: str | None, incomplete_reason: str | None) -> str:
    if incomplete_reason is not None:
        return f"더 나아 보이는 답변: 비교 불가\n이유:\n- {incomplete_reason}"

    if judge_text is None:
        return "더 나아 보이는 답변: 비교 불가\n이유:\n- Judge 호출이 실패해서 자동 비교를 완료하지 못했습니다."

    return judge_text


def build_report(prompt: str, sonnet_result: ModelResult, codex_result: ModelResult, judge_text: str | None) -> str:
    incomplete_reason = None
    if not sonnet_result.success and not codex_result.success:
        incomplete_reason = "두 모델 호출이 모두 실패했습니다."
    elif not sonnet_result.success:
        incomplete_reason = "Sonnet 호출이 실패해서 완전한 비교를 할 수 없습니다."
    elif not codex_result.success:
        incomplete_reason = "Codex 호출이 실패해서 완전한 비교를 할 수 없습니다."

    lines = [
        "=== Compare ===",
        prompt,
        "",
        "=== Sonnet ===",
        format_result(sonnet_result),
        "",
        "=== Codex ===",
        format_result(codex_result),
        "",
        "=== Judge ===",
        format_judge_block(judge_text, incomplete_reason),
    ]
    return "\n".join(lines).strip()


def main() -> int:
    session_id, command_name, started_at, prompt_args = parse_cli_args(sys.argv)
    invocation_started_at = started_at or datetime.now(timezone.utc)
    prompt = read_prompt(prompt_args)
    anchor_index = -1
    if not prompt and session_id:
        anchor_index, prompt = extract_prompt_from_session(
            session_id, command_name, invocation_started_at
        )
    if not prompt:
        print("Could not capture the current /compare prompt. Please run the command again.")
        return 1

    recent_context = build_recent_context(session_id, anchor_index)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        sonnet_future = executor.submit(
            run_answer_model,
            "Sonnet",
            CLAUDE_BIN,
            "sonnet",
            prompt,
            recent_context,
        )
        codex_future = executor.submit(
            run_answer_model,
            "Codex",
            CLAUDE_MIX_BIN,
            "codex-haiku(xhigh)",
            prompt,
            recent_context,
        )
        sonnet_result = sonnet_future.result()
        codex_result = codex_future.result()

    judge_text = None
    if sonnet_result.success and codex_result.success:
        judge_text = run_judge_model(prompt, sonnet_result.output, codex_result.output)

    print(build_report(prompt, sonnet_result, codex_result, judge_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
