---
name: git-commits
description: >
  Conventional commit format and scopes for the Real Estate Scraper & Alerting Platform.
  Use when creating git commits, writing commit messages, or when asked to commit changes.
  Ensures clean, structured commit history (feat:, fix:, refactor:, test:, docs:)
  tailored to this repository.
---

# Commit Messages — Real Estate Platform

Use the [Conventional Commits](https://www.conventionalcommits.org/) format. Clean, structured commit messages ensure easy git log auditing, automated release notes, and crystal-clear intent for every code change.

## Subject Line: Exactly One Type, at Most One Scope

Format:
```
<type>(<scope>): <description>

[optional body]

[optional trailers / references]
```

Schema:
```
(?s)(build|ci|docs|feat|fix|perf|refactor|style|test|chore|revert)(\(\S+\))?!?:( [^\n\r]+)((\n\n.*)|(\s*))?$
```

- **Exactly one type**: (e.g. `feat`, `fix`, `refactor`).
- **Optionally one scope** in parentheses: (e.g. `(scrapers)`, `(filters)`).
- **Colon + space + imperative summary**: (e.g. `migrate SS.ge to TNET API`).
- **Subject line ≤ 72 characters**, lowercase, no trailing period.

---

### Anti-Patterns to Avoid

| Bad Subject | Why It Fails | Correction |
|---|---|---|
| `feat(scrapers)+docs: update SS.ge` | Compound types are invalid | Split into 2 commits or use dominant type |
| `feat(scrapers, filters): X` | Multiple scopes in parens | Pick the primary scope or use general `feat(core):` |
| `fixed the ss bug` | Missing conventional type prefix | `fix(scrapers): fix SS.ge listing ID parsing` |
| `feat: ` *(empty description)* | Non-empty description required | `feat(notifier): add markdown formatting` |

---

## Types

| Type | When to Use |
|---|---|
| `feat` | New feature, portal scraper, filter rule, or notification channel |
| `fix` | Bug fix (e.g. parsing error, timeout issue, filter false positive/negative) |
| `docs` | Documentation only (`README.md`, `docs/`, ADRs) |
| `refactor` | Code restructuring that neither fixes a bug nor adds a feature |
| `test` | Adding, updating, or fixing unit tests in `tests/` |
| `chore` | Dependency updates, tooling, scripts, repo organization |
| `perf` | Performance optimization (e.g. reducing API latency, faster DB queries) |
| `style` | Formatting, whitespace, lint fixes (no production logic change) |
| `ci` | GitHub Actions workflow changes (`.github/workflows/scrape.yml`) |
| `revert` | Reverting a previous commit |

---

## Scopes for this Repository

Choose the scope that most directly matches the affected module:

| Scope | Files / Subsystems |
|---|---|
| `scrapers` | `scrapers/` (`myhome.py`, `ss_ge.py`, `area_ge.py`, `base.py`) |
| `filters` | `core/filters.py`, `config/filters.json` (hygiene, district synonyms, stop words) |
| `analytics` | `core/analytics.py` (market valuation, IQR statistics, discount badges) |
| `db` | `core/database.py`, SQLite schema, `seen_ids.txt`, deduplication |
| `sync` | `core/user_sync.py`, Cloudflare Worker KV subscriber sync |
| `notifier` | `notifier/telegram_bot.py`, `notifier/ntfy_notifier.py` |
| `orchestrator` | `main.py`, `app.py`, cycle execution |
| `models` | `core/models.py` (`PropertyListing`, `SearchFilters`, `UserSubscription`) |
| `ci` | `.github/workflows/scrape.yml` |
| `tests` | `tests/` (`test_scrapers.py`, `test_filters.py`, `test_location_filtering.py`, etc.) |
| `skills` | `.agents/skills/` |
| `docs` | `README.md`, `docs/` |

---

## Commit Guidelines

1. **Imperative, present tense**: Write "add feature", not "added feature" or "adds feature".
2. **Body explains the *why*, not the *what***: The diff shows what changed; the commit message explains the motivation, constraints, or bug root-cause.
3. **Reference ADRs & PRs**: If the commit addresses an ADR, reference it: `feat(scrapers): integrate SS.ge API (ADR-0002)`.
4. **Never bypass tests**: Always run `.\.venv\Scripts\python.exe -m unittest discover tests` before committing.

---

## Examples

### Good Commit Messages

```
feat(scrapers): migrate SS.ge scraper to TNET statements JSON API

Switches SS.ge extraction from brittle SSR Next.js HTML parsing to the unified
TNET statements JSON endpoint (https://api-statements.tnet.ge/v1/statements).
Reduces cycle latency from 15s timeouts down to ~200ms and restores instant
listing alerts for active subscribers.
```

```
fix(filters): prevent dropping unconditioned SS.ge listings in API query

Removes 'conditions=1,2,3,5,8' from the API query parameters. On SS.ge, many
sellers leave condition_id unset. The API query was silently dropping ~38% of
genuine listings before Python filters evaluated them.
```

```
refactor(db): streamline per-user seen notifications deduplication

Optimizes user_seen dictionary serialization and adds in-memory cache to
prevent redundant SQLite disk reads during high-frequency scraping cycles.
```

```
test(scrapers): add test cases for SS.ge API URL generation and normalization

Covers sale and rent filter parameter construction and validates that TNET
statement objects are normalized with working home.ss.ge listing links.
```
