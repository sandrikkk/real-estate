#!/usr/bin/env python3
"""Keep AI-authorship signatures out of commits, staged code, and agent-written PR text.

    check_ai_traces.py commit-msg FILE   git commit-msg hook: exit 1 when the message has a trace
    check_ai_traces.py staged            pre-commit hook: scan the lines added in the staged diff
    check_ai_traces.py claude-hook       Claude Code PreToolUse hook: JSON on stdin, exit 2 blocks

A trace is an attribution signature (a "generated with <AI>" footer, a co-author trailer, a
session link), not any mention of an AI tool. Policy and the fix procedure:
.agents/skills/no-ai-traces/SKILL.md. Standard library only, so every hook can run it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

AI_NAMES = (
    r"(?:claude|anthropic|chatgpt|gpt-?\d*|openai|copilot|gemini|cursor|codex|devin|aider"
    r"|windsurf|codeium|an?\s+ai|ai)"
)

RULES = (
    ("co-author trailer", re.compile(r"^\s*co-authored-by:", re.IGNORECASE)),
    (
        "'generated with/by <AI>' footer",
        re.compile(
            rf"\b(?:generated|written|created|authored|assisted|produced|made)"
            rf"\s+(?:with|by|using)\s+\[?(?:(?:github|microsoft|google)\s+)?{AI_NAMES}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "AI session link or trailer",
        re.compile(r"^\s*claude-session:|claude\.ai/code/|claude\.com/claude-code", re.IGNORECASE),
    ),
    ("AI vendor noreply address", re.compile(r"noreply@anthropic\.com", re.IGNORECASE)),
)

# Files that must spell the patterns out: the tests and the policy skill.
ALLOWED_PATHS = frozenset({"tests/test_ai_traces.py", ".agents/skills/no-ai-traces/SKILL.md"})

# Commands whose text ends up in commit history or on GitHub.
PUBLISHING_COMMAND = re.compile(
    r"\bgit\b[^\n|;&]*\bcommit\b|\bgh\s+(?:pr|issue|api|release)\b", re.IGNORECASE
)
BODY_FILE_ARG = re.compile(r"""(?:--body-file|--file|-F)(?:\s+|=)("[^"]+"|'[^']+'|[^\s;&|]+)""")
MAX_FILE_BYTES = 1_000_000


def find_traces(text: str) -> list[tuple[int, str, str]]:
    """Return (line number, rule name, trimmed line) for every trace in the text."""
    hits = []
    for number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in RULES:
            if pattern.search(line):
                hits.append((number, name, line.strip()[:120]))
                break
    return hits


def report(where: str, hits: list[tuple[int, str, str]]) -> None:
    print(f"AI attribution trace found in {where}:", file=sys.stderr)
    for number, name, line in hits:
        print(f"  line {number} [{name}]: {line}", file=sys.stderr)
    print(
        "Remove these lines and try again. Policy: .agents/skills/no-ai-traces/SKILL.md",
        file=sys.stderr,
    )


def check_commit_msg(path: str) -> int:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    hits = find_traces(body)
    if hits:
        report("the commit message", hits)
    return 1 if hits else 0


def check_staged() -> int:
    diff = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "--cached", "--no-color", "-U0"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", errors="replace")
    failed = False
    current = None
    added: list[str] = []

    def flush() -> None:
        nonlocal failed
        if current and current not in ALLOWED_PATHS and added:
            hits = find_traces("\n".join(added))
            if hits:
                report(f"staged additions to {current}", hits)
                failed = True

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            current, added = None, []
        elif line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    flush()
    return 1 if failed else 0


def referenced_files(command: str, cwd: str) -> list[Path]:
    paths = []
    for raw in BODY_FILE_ARG.findall(command):
        value = raw.strip("\"'")
        if value == "-":
            continue
        path = Path(value)
        paths.append(path if path.is_absolute() else Path(cwd) / path)
    return paths


def check_claude_hook() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        command = payload["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        return 0
    if not isinstance(command, str) or not PUBLISHING_COMMAND.search(command):
        return 0

    sources = [("the command", command)]
    for path in referenced_files(command, payload.get("cwd") or os.getcwd()):
        try:
            if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                sources.append((str(path), path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue

    failed = False
    for where, text in sources:
        hits = find_traces(text)
        if hits:
            report(where, hits)
            failed = True
    return 2 if failed else 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("commit-msg", "staged", "claude-hook"))
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    if args.mode == "commit-msg":
        return check_commit_msg(args.files[0]) if args.files else 0
    if args.mode == "staged":
        return check_staged()
    return check_claude_hook()


if __name__ == "__main__":
    sys.exit(main())
