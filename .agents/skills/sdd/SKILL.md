---
name: sdd
description: >
  Spec-driven development for the Real Estate Scraper & Alerting Platform — when
  to write a 1-page ADR before coding, what shape the spec takes, where it lives.
  Use BEFORE starting any non-trivial change, or when asked to "write an ADR",
  "do we need a spec", "add a new scraper portal", "change the database or D1
  access pattern", "add a notification channel", or "deprecate X". Skip for bug
  fixes, filter tweaks, cron edits, and typos.
---

# Spec-driven development — lightweight, not bureaucratic

Decide what to build before deciding how to build it. The specification is the artefact that drives the change. [GitHub Spec Kit](https://github.com/github/spec-kit) formalises that idea as a CLI and a pile of per-feature files (`spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `contracts/`).

Keep the philosophy. Leave the CLI, the artefact set, and the auto-branch command out. A typical change here is a filter tweak, a scraper parser fix, a cron edit, or a Worker binding. Spec Kit's `/speckit.specify` also opens a feature branch per spec. This repo's scraper cron checks out `master`, and a branch is created only when the user asks for one.

Collapse the whole flow onto **one ADR per non-trivial change**:

| Concern (Spec Kit names it) | What to do |
|---|---|
| Specify (the "what" + "why") | One-page ADR — Why / What / Consequences |
| Plan (the "how") | Same ADR — the "What" section names the concrete approach |
| Tasks | A short checklist in that ADR, then smaller commits if the user asks to commit |
| Implement | Code + tests on the current branch, after the user accepts the ADR |

Do not install Spec Kit. Do not run `/speckit.*` commands. To adopt a piece of it (an ambiguity checklist, a task generator), open an ADR proposing that first.

## When to open an ADR

Open one when the change has any of these shapes:

- A new architectural seam: a new portal scraper, a new data store, a new protocol between `cloudflare/worker.js` and the Python cycle, a new notification channel.
- A D1 access or schema change that alters failure behaviour, row-read cost, or dedup safety. ADR 0001 is this shape.
- A choice between plausible approaches a later reader will need: direct TLS impersonation vs a proxy, fail-closed D1 vs a local SQLite fallback, polling cadence when the runner model changes.
- A deprecation: retiring a scraper, the local SQLite path, or a notifier.
- A trade-off someone has to accept on purpose: alert latency vs D1 quota, owner-only strictness vs missed listings, Worker secrets vs what is safe to commit.

Skip the ADR when the change is:

- A typo, or a one-line constant (timeout, user-agent, cron expression, a single price ceiling).
- A blacklist keyword, district synonym, or stop-word added in `config/filters.json` or `core/filters.py`.
- A bug fix whose original intent is obvious (a bad selector, a wrong header, a regex that drops valid listing ids).
- A refactor that does not change behaviour or interfaces.
- A test, or a clarification in `README.md` / `docs/`.

The commit message is the record for those. Use the [`git-commits`](../git-commits/SKILL.md) skill when the user asks to commit.

## The ADR shape

New ADRs live at `docs/adr/NNNN-short-slug.md`. Numbering is zero-padded and sequential. `0001` is taken. The next file is `0002`. Never reuse a number.

`docs/adr/template.md` and `docs/adr/README.md` are not in the tree. Do not create them unless the user asks. The shape below is the template.

```markdown
# NNNN — Short title in present tense

## Why

The problem, the constraint, the reason this decision is forced.
Two to five short paragraphs. Name the current behaviour in the
files that actually do it (`core/database.py`, `scrapers/myhome.py`,
`cloudflare/worker.js`, `.github/workflows/scrape.yml`).

## What

The decision. Concrete enough that a reader in six months can tell
what code shape to expect: which modules change, what happens to
`PropertyListing` / `SearchFilters` / `UserSubscription`, and any
D1 statement or Worker route that changes. Use a Mermaid diagram
when a sequence or topology is clearer than prose (see the
docs-standards skill).

## Consequences

What this enables, what it forbids, what gets harder, what gets
easier. Include subscriber safety: duplicate alerts, missed alerts,
and what happens when D1 or the Worker is down.
```

Hard length cap: **one page when rendered.** A decision that needs more space is two decisions. Split them.

The existing record to copy for tone and specificity is [`docs/adr/0001-eliminate-d1-full-table-scans.md`](../../../docs/adr/0001-eliminate-d1-full-table-scans.md). It predates this heading set. Leave it as written. Its "Context & Problem Statement" is Why, and its "Decision & Specification" is What.

`docs/superpowers/` holds two dated design write-ups (X-Ray, in-place price drop). They are not the spec format. Do not add new specs there.

## ADR lifecycle

- Number sequentially. Never reuse a number.
- Once the user has accepted an ADR, it is append-only. To change the decision, write a new ADR that supersedes it. Leave the old file in the tree. Add a one-line footer: `Superseded by ADR NNNN`.
- There is no ADR index file. Do not create one unless the user asks. AGENTS.md does not carry an ADR table.

## Workflow when an ADR is needed

1. **Specify.** Draft the ADR text in the chat first. Ask the user whether to create the file. Never create a markdown file without that permission. After a yes, write `docs/adr/NNNN-<slug>.md` before any implementation edit and wait for acceptance. Capturing the rationale first is the whole point.
2. **Tasks.** After acceptance, list the chunks in order: model, scraper or Worker, filter, tests. One chunk per commit if the user asks to commit.
3. **Implement.** Follow the [`tdd`](../tdd/SKILL.md) skill where the behaviour is well-specified: failing test, then the minimum code that passes. Run that test from the project virtual environment.
4. **Reference.** When the user asks to commit, put the ADR id in the commit body (`ADR-0002`), using a `git-commits` type and scope. Do not push, and do not open a branch, unless the user asks. Scraper deploys and Worker deploys are separate steps (`deploy-action`, `scripts/deploy_worker.py`). Run them only when the user asks.

## What "lightweight" means in practice

- One page is the cap, not the target. Half a page is enough for many decisions.
- Do not write an ADR for something the commit message can hold.
- Do not write an ADR to postpone a decision. That is a TODO.
- Do not write an ADR to restate Cloudflare, GitHub Actions, or TNET behaviour. Link to those docs, or to [`cloudflare/README.md`](../../../cloudflare/README.md).
- Do not write an ADR before a spike. Spike first. Then write the ADR with what the spike showed.

## When in doubt

Write a two-paragraph proposal in the chat before any code. The user will say whether it becomes an ADR or proceeds without one.
