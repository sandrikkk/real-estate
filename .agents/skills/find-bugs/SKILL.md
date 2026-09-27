---
name: find-bugs
description: Find bugs, security vulnerabilities, edge cases, and code quality issues in local branch changes. Use when asked to "review changes", "find bugs", "security review", "audit code on branch", "შეამოწმე კოდი", "რაიმე ბაგი ხომ არ არის", or "სად შეიძლება გატყდეს".
---

# Find Bugs

Review changes on this branch for logic bugs, edge cases, external API failure modes, security vulnerabilities, and code quality issues.

## Phase 1: Complete Input Gathering

1. Get the FULL diff against the default branch (`master`):
   ```bash
   git diff origin/master...HEAD
   ```
   Or for uncommitted/working tree changes:
   ```bash
   git diff
   git status --short
   ```
2. If output is long, read each changed file individually using file viewing tools until you have inspected every changed line.
3. List all files modified before proceeding.

## Phase 2: Attack Surface & Failure Mode Mapping

For each changed file (scrapers, filters, database, API, notifier), identify:

* **External API calls**: Network timeouts, missing keys in JSON, format changes, HTTP 403/429 status codes, rate limits.
* **Input validation**: Missing query params, malformed URLs, empty strings, null/None values.
* **Database & State operations**: Duplicate key insertions, SQLite locking, concurrent writes.
* **Regex / Parsing**: Fragile patterns, index out of range on split/match.
* **Business logic**: Price calculation edge cases, currency conversion, false positive/negative filter matches.

## Phase 3: Quality & Security Checklist

* [ ] **Error Handling**: Are external API failures (TNET, MyHome, Area) caught without crashing the entire scrape loop?
* [ ] **Data Integrity**: Are null or missing fields in property listings handled with safe defaults?
* [ ] **Injection / Escaping**: Are Telegram Markdown/HTML messages properly escaped so bad listing titles don't break message delivery?
* [ ] **Resource Leaks**: Are HTTP sessions / database connections closed properly?
* [ ] **Race Conditions**: Is SQLite or state access safe across scraping iterations?
* [ ] **Secrets & Config**: Are bot tokens or API keys exposed in code or logs?

## Phase 4: Verification

For each potential issue:

* Check if it's already caught or handled elsewhere in the pipeline.
* Search for existing unit tests covering the scenario in `tests/`.
* Read surrounding context to verify whether the issue is genuine or a false positive.

## Phase 5: Output Format

Prioritize: **Critical bugs / crashes > Security issues > Logic flaws > Performance / Minor issues**.

Skip stylistic/formatting comments.

For each issue:

* **File:Line** — Brief description
* **Severity**: Critical / High / Medium / Low
* **Problem**: What's wrong and what causes it to break
* **Evidence**: Concrete scenario where it fails
* **Fix**: Concrete code fix suggestion

If everything is clean and solid, state that clearly with evidence.
