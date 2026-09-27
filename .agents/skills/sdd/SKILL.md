---
name: sdd
description: Spec-driven development for the Real Estate Scraper & Alerting Platform — when to write a 1-page ADR before coding, what shape the spec takes, where it lives. Use BEFORE starting any non-trivial change, or when asked to "write an ADR", "do we need a spec", "add a new scraper portal", "change database architecture", or "deprecate X". Skip for bug fixes, filter tweaks, and typo fixes.
---

# Spec-Driven Development (SDD) — Real Estate Platform

This project uses a **lightweight, practical** spec-driven workflow. Deciding *what to build and why* must be clearly separated from *how to build it*. Specifications and architectural decision records (ADRs) serve as first-class artifacts that drive implementation rather than after-the-fact documentation.

We keep it **lightweight, not bureaucratic** — one ADR per non-trivial architectural change:

| Concern | What we do |
|---|---|
| **Specify (the "what" + "why")** | One-page ADR: Context, Decision, Alternatives, Consequences |
| **Plan (the "how")** | Same ADR: Architecture, data model impact, scraper/filter changes |
| **Tasks** | Clear step-by-step implementation checklist |
| **Implement** | Code + tests pair-programmed using the `tdd` skill |

---

## When to Open an ADR

### OPEN an ADR when the change has any of these shapes:
- **Adding a new real estate portal / scraper source** (e.g. adding Area.ge, Korter.ge, or a new API gateway).
- **Database / storage architecture changes** (e.g. migrating from SQLite `properties.db` to PostgreSQL, Cloudflare D1, or modifying multi-user schema).
- **Major changes to the market valuation algorithm** or price recommendation tiers (`MarketAnalytics`, IQR calculation, pricing status badges).
- **Subscriber sync or multi-user infrastructure changes** (Cloudflare Worker KV sync protocol, user model changes).
- **Adding or changing notification channels** (e.g. adding Webhooks, WhatsApp, mobile push, or rewriting Telegram alert templates).
- **Deprecating or replacing a core component** (e.g. retiring an old HTML scraper or deprecating a legacy filter engine).
- **Non-obvious trade-offs** (polling frequency vs rate limits, scraping through proxies vs direct TLS impersonation, database footprint vs historical depth).

### SKIP the ADR when the change is:
- A straightforward bug fix (e.g. fixing a regex for listing IDs or patching an API header).
- Adding a new district synonym or blacklisted keyword in `core/filters.py` or `config/filters.json`.
- Modifying scraper timeouts, user-agents, or selector fallbacks.
- Routine refactoring that preserves existing public interfaces and behaviors.
- Adding tests or updating documentation.

The commit message is the record for those.

---

## The ADR Shape & Template

ADRs live at `docs/adr/NNNN-short-slug.md` (zero-padded 4-digit sequential numbering):

```markdown
# NNNN — Short Title in Present Tense

## Status
[Proposed | Accepted | Superseded by NNNN]

## Context & Problem Statement
The issue, constraint, or requirement that forces this decision.
Explain what happens today and why it needs to change (2–4 short paragraphs).

## Decision & Specification
The concrete architectural decision.
Specify:
- Affected components (`scrapers/`, `core/`, `notifier/`, `main.py`, `.github/workflows/`)
- Data model changes (`PropertyListing`, `SearchFilters`, `UserSubscription`)
- API endpoints, request contracts, or database schema migrations

## Consequences
- **Positive:** What this enables or improves (e.g. zero timeouts, 10x faster scraping).
- **Trade-offs / Negative:** What gets more complex or requires ongoing maintenance.
- **Safety / Compatibility:** How backwards compatibility with existing subscribers is preserved.
```

> **Hard length cap:** **One page when rendered.** A decision that requires more space is actually multiple decisions — split them into separate ADRs.

---

## ADR Lifecycle & Rules

1. **Sequential Numbering:** Never reuse an ADR number.
2. **Append-Only Record:** Once accepted, ADRs are permanent historical records. To reverse or change a decision, open a new ADR that **supersedes** the previous one (add `Superseded by ADR NNNN` at the top of the old file).
3. **Traceability:** Always reference the ADR number in commit messages (e.g. `feat(scrapers): migrate SS.ge to TNET API (ADR-0003)`).

---

## Workflow When an ADR is Needed

1. **Draft the ADR FIRST:** Write `docs/adr/NNNN-<slug>.md` before touching implementation code.
2. **Review & Confirm:** Confirm the approach, surface potential failure points, and agree on trade-offs with the user.
3. **Break Down Tasks:** Formulate a step-by-step plan:
   - Model updates
   - Scraper / API changes
   - Filter & analytics adaptations
   - Pre-flight test additions
4. **Implement via TDD:** Use the [`tdd`](../tdd/SKILL.md) skill: write the failing test first, make it pass, verify the suite.
5. **Deploy safely:** Use the [`deploy-action`](../deploy-action/SKILL.md) skill to test, reconcile database state, and push to GitHub.
