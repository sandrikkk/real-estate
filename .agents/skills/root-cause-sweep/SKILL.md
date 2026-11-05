---
name: root-cause-sweep
description: >
  Traces a reported symptom to a reusable defect shape and searches the
  codebase for every other instance before patching. Use when asked to "fix this
  bug", "this is broken", "we already fixed this", "it regressed", "wrong alert",
  "duplicate alert", "listing leaked through the filter", or when a one-line fix
  in one scraper, filter, or D1 call risks leaving the same failure in a sibling.
---

# Root-cause / same-class sweep

A bug report names one symptom. The defect behind it usually has siblings: another portal scraper, the other filter path, another D1 call inside a loop. Patch only the reported line and the next report is the sibling.

Pairs with [`tdd`](../tdd/SKILL.md) (failing test first) and [`sdd`](../sdd/SKILL.md) (ADR when the sweep exposes a design seam). Past incidents that shaped this checklist are in [`lessons-learned`](../lessons-learned/SKILL.md).

## When this applies

Every bug fix, and every change to scraping, filtering, deduplication, D1 access, notification, or the Worker. Exempt: docs-only edits, lint-only edits, and chores with no behaviour change.

Fixing every instance of **one** root cause is still one root cause. It does not count as bundling unrelated changes.

## Four phases

### 1. Name the defect shape

Write one sentence that survives a change of file. Prefer shapes over `file:line`.

| Shape | What it looked like once |
|---|---|
| A per-item network call inside a loop over listings | `save_listing`, `is_user_notified`, and `_get_hashes` each sent one HTTP request to the Worker per listing. The fix was batching (`save_listings_batch`, `filter_unnotified_listings`, `_get_hashes_batch`). |
| A rule enforced on one path of two | Price and area bounds are checked in both `ListingFilter.matches` and `ListingFilter.matches_user`. A leak of listings priced `$88k`–`$98k` came from the per-user path. |
| A field trusted as proof of a role | `user_type: "physical"` treated as owner, while private brokers register that way with `is_owner` false. |
| A local calculation overwriting a portal's own value | MyHome's price badge replaced by a median computed from stored listings. |

### 2. Map instances

Search for the shape, not the symptom. Record every hit and give each one a verdict: **fix now**, **defer with a reference**, or **rule out with a reason**.

Search surfaces for this repo:

- **Sibling portals.** A parsing or normalisation bug in `scrapers/myhome.py` is checked in `scrapers/ss_ge.py` and `scrapers/area_ge.py`, and in the shared `scrapers/base.py`.
- **Both filter paths.** A fix in `ListingFilter.matches` is checked in `matches_user`, and in the tentative pre-check used before detail enrichment (`matches_user(..., tentative=True)`).
- **Both database modes.** Anything in `core/database.py` runs against Cloudflare D1 and against local SQLite. Check the `use_d1` branch and the SQLite branch.
- **Both runners.** The cron job runs `main.py --once`. `app.py` runs the same cycle in a loop. A change to cycle behaviour affects both.
- **Python and Worker.** `cloudflare/worker.js` implements its own filter buttons, user storage, and X-Ray lookup. A rule that exists in `core/` may also exist there.
- **Tests and docs.** Tests that locked the broken behaviour, and prose that described it.

A useful first pass:

```bash
rg -n "<distinctive token of the shape>" scrapers core notifier cloudflare main.py app.py tests
```

### 3. Choose tools

Load the skill that governs each surface before editing: [`tdd`](../tdd/SKILL.md) for the test file, [`sdd`](../sdd/SKILL.md) if the sweep shows a design decision, [`docs-standards`](../docs-standards/SKILL.md) if a doc described the old behaviour. Prefer the cheapest change that closes the class.

### 4. Specify, then test

If the sweep finds a systemic seam (for example a new D1 access pattern), write the ADR first with `sdd`. For each instance kept in scope, write the failing test first, watch it fail for the expected reason, then fix it. Run the narrowest test file from the matrix in `tdd`, then the full suite, from the project virtual environment.

## Where the sweep gets recorded

There is no PR template or ADR template in this repo. Record the sweep in two places:

- The reply to the user: the shape in one sentence, then the instance list with a verdict per hit.
- The commit body, when the user asks to commit (see [`git-commits`](../git-commits/SKILL.md)).

Nothing enforces this automatically. It is a habit, so state the sweep result even when the answer is "one instance, ruled out the other two portals because …".
