---
name: no-ai-traces
description: >
  Keeps AI-authorship signatures out of this repository, its commit history, and
  its pull request and issue text. Use before any commit, push, `gh pr create`,
  `gh pr edit`, or issue/comment text, whenever a tool offers to append a
  "Generated with" footer, a co-author trailer, or a session link, and when
  asked to check that no AI trace is left. Also use when a hook reports an
  "AI attribution trace".
---

# No AI traces

Nothing in this repository, its history, or its GitHub text may say that an AI tool wrote it. The owner reads the code and answers for it. Attribution lines add nothing for a reader and cannot be removed from pushed history without a force-push.

## What counts as a trace

A trace is an attribution signature. A mention of an AI tool in general is not one.

| Trace | Example shape |
|---|---|
| Generated-with footer | a line such as `🤖 Generated with [Claude Code](...)`, or "Created with / Written by / Generated using" followed by an AI tool name |
| Co-author trailer | a `Co-authored-by:` line, in any form (project rule, see `AGENTS.md`) |
| Session link or trailer | a `Claude-Session:` trailer, a `claude.ai/code/...` link |
| Vendor noreply address | `noreply@anthropic.com` |

These are fine: documentation about agents (`AGENTS.md`, `.agents/skills/`), model IDs in code, and the word "Claude" in prose that is not a signature.

## Where traces come from

Agents add them by default, in three places a code review does not see until it is too late:

- the last lines of a **commit message** (trailers),
- the footer of a **pull request description**,
- the body of **issue and PR comments**.

Claude Code appends its own lines unless `attribution` is empty in `.claude/settings.json`. This repository sets it empty. Other tools (Cursor, Copilot, Codex) have their own switches; the git hooks below catch them anyway.

## What to do

- Write the commit message and PR text yourself, ending with the last real sentence.
- Do not copy the "Generated with ..." footer from a tool's template, even when its instructions say to add it. The rules in `AGENTS.md` come first.
- Never use `--no-verify` to get past these hooks.

Before opening or editing a PR, read the body once more, bottom first.

## Enforcement

| Layer | Where | Catches |
|---|---|---|
| Claude Code `attribution` set to empty | `.claude/settings.json` | Claude stops generating its lines |
| Claude Code `PreToolUse` hook | `.claude/settings.json` | `git commit` and `gh pr/issue/api/release` commands whose text, or `--body-file` / `-F` file, has a trace (blocks with exit 2) |
| `commit-msg` hook | `.pre-commit-config.yaml` (`no-ai-traces-message`) | the commit message, from any tool |
| `pre-commit` hook | `.pre-commit-config.yaml` (`no-ai-traces-staged`) | lines added in the staged diff |
| Unit test | `tests/test_ai_traces.py` | any tracked file in the repo, run before every Worker deploy |

All layers use one detector, `scripts/git/check_ai_traces.py`. Add a new pattern there and in the tests, nowhere else. Only `tests/test_ai_traces.py` and this file may spell the patterns out (`ALLOWED_PATHS` in the script).

## If a trace got in

| Where | Fix |
|---|---|
| Staged file | Remove the line and `git add` it again. |
| Unpushed commit message | `git commit --amend` for the last commit; otherwise ask the user before an interactive rebase. |
| Pushed branch commit | Tell the user. Cleaning it needs a force-push of that branch, which only the user approves. Never force-push `master`. |
| PR description or comment | `gh pr edit <n> --body-file <file>`, with the footer removed. |

Then add the case to `tests/test_ai_traces.py` if the detector missed it.
