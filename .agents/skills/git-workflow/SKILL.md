---
name: git-workflow
description: >
  Branch creation for the Real Estate Scraper & Alerting Platform. Use at the
  start of any coding session, before the first edit of a tracked file, so the
  work is not done on master. Also use when asked to "create a branch",
  "new feature branch", "don't work on master", or before a commit on master.
---

# Git workflow — branch before the first edit

The scraper cron checks out `master`. A push to `master` is a release. New work therefore happens on a short-lived branch. Creating that branch is required. Committing, pushing, and deploying still happen only when the user asks.

## Scope check — before creating a branch

Create a branch only when the task will change a tracked file.

| Request | Branch? |
|---|---|
| Read, explain, or review code. Run the unit suite or `python main.py --stats` without editing. | No |
| Install a venv, fill gitignored `.env`, or change anything outside this worktree | No |
| Edit a tracked file, or add or remove one | Yes |

If `git status` would still be clean when the task is finished, do not create a branch.

## Pre-flight

Run this before the first edit:

```bash
git status
git branch --show-current
```

- **One repo, one folder.** All work happens in this checkout. Never create a git worktree, a second clone, or a copy of the project folder.
- On `master` with only untracked files: create the branch here, as below. Untracked files stay where they are, and they are not committed.
- On `master` with uncommitted changes to tracked files, or while another agent is editing this checkout: stop and ask the user. Do not stash, commit, or move their work.

- On a clean `master`: create the branch, naming the start point. A bare `git checkout -b <slug>` branches from whatever `HEAD` happens to be.

  ```bash
  git fetch origin master
  git checkout -b feat/<slug> origin/master
  ```

- Already on a feature branch that matches this task: stay on it. Do not open a second branch for the same change.

## Names

| Pattern | When |
|---|---|
| `feat/<slug>` | New behaviour |
| `fix/<slug>` | Bug fix |
| `chore/<slug>` | CI, tooling, repo organisation |
| `docs/<slug>` | Documentation only, and only when the user asked for the doc edit |

Slug: kebab-case, short, no ticket numbers.

## Do not

- Edit tracked files on `master`.
- Create a git worktree, a second clone, or a copy of the project folder.
- Push the branch, open a PR, or deploy, unless the user asks.
- Force-push `master`.
- Commit the dirty tree that was already on `master` just to get onto a branch. Leave another agent's uncommitted files where they are.
