---
name: audit-rules
description: Audits this repo's AI-governance layer — every file under `.agents/skills/`, rules in `GEMINI.md`, and any Cursor `.mdc` rules — against published best practices from Anthropic (Skill authoring), Cursor (MDC rules), and the agents.md community spec. Use when the user asks to "audit our skills", "review rules hygiene", "check GEMINI.md", "audit AI governance", or when verifying a newly added skill or rule before merging.
---

# audit-rules — judgement-grade audit of skills and rules

This skill covers **judgement-grade checks** that need a human or LLM to read prose and verify that AI-governance files (.agents/skills, GEMINI.md, rules) are concise, enforceable, and aligned with the Real Estate Scraper & Alerting Platform.

## Sources this skill is derived from

Each check traces to an established public standard:

| Source | What it covers |
|---|---|
| [Anthropic — Skill authoring best practices](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills/best-practices) | Frontmatter shape, description voice, body length, progressive disclosure, anti-patterns |
| [Anthropic's `skill-development` skill](https://github.com/anthropics/claude-plugins-official) | Imperative voice, trigger-phrase quality, target body length |
| [agents.md spec](https://agents.md) | What rules/instructions files are for, conflict resolution |
| [Cursor MDC rules docs](https://cursor.com/docs/rules) | Activation modes, alwaysApply token budget, glob-scoped rules |

## Checklist — work through it in order

```
Audit progress:

Frontmatter and discovery
- [ ] Every SKILL.md description states WHAT and WHEN, in third person
- [ ] Every SKILL.md description includes concrete trigger phrases the user would actually say
- [ ] No skill name reuses "anthropic" or "claude" reserved words
- [ ] No skill name is vague (helper, utils, tools)

Body quality
- [ ] No SKILL.md uses second-person "you should" voice; all use imperative ("Run X", "Add Y") or third person
- [ ] Terminology is consistent within each file (e.g. PropertyListing, TNET API, MyHome, SS.ge, Area.ge)
- [ ] Examples are concrete and runnable, not abstract

Cross-cutting consistency
- [ ] GEMINI.md / AGENTS.md and skills do not contradict each other on any policy claim
- [ ] Every "MUST" / "always" / "never" rule in any skill is enforceable (not purely aspirational)
- [ ] Unit tests pass: `.\.venv\Scripts\python.exe -m unittest discover tests`

Drift and accuracy
- [ ] Live values (API endpoints, scraper domains, environment variables) match the actual codebase
- [ ] Cross-file links resolve properly
- [ ] No legacy or leftover project names appear anywhere in the skills
```

## How to do the judgement-grade checks

### 1. Description voice — third person, with trigger phrases

Each `SKILL.md` description must answer two questions for the agent deciding whether to load it:

- **WHAT** does this skill do?
- **WHEN** should I invoke it? (Anchor on phrases the user would actually type.)

```yaml
# Good (third person, concrete triggers)
description: >
  Conventional commit format and scopes for the Real Estate Scraper & Alerting Platform.
  Use when creating git commits, writing commit messages, or when asked to commit changes.

# Bad (second person, vague)
description: Use this skill when you want to write commits.
```

### 2. Aspirational vs enforceable rules

Every rule that says "MUST", "always", or "never" is a promise that the rule will be honoured. If there is no test, CI check, or explicit manual step enforcing it, the rule creates false confidence.

For each "MUST/always/never" rule, locate its enforcement:

| Rule shape | Where enforcement lives |
|---|---|
| "Never touch scrapers/myhome.py without explicit user request" | System prompt instructions & user rules |
| "All tests MUST pass before deploying" | Pre-flight script in `.agents/skills/deploy-action/scripts/deploy.ps1` |
| "Commit messages MUST follow conventional format" | Pre-commit git hook / `git-commits` skill |
| "Currency values MUST be wrapped in backticks in Markdown" | `docs-standards` skill guidelines |

### 3. Trigger-phrase quality for `description`

A skill is only loaded if the agent's routing mechanism detects relevant intent. The `description` is the primary metadata evaluated:

```
Skill: git-commits
User would say: "დააკომიტე", "commit this", "write a commit message", "git commit"
Description mentions: "creating git commits, writing commit messages, or when asked to commit changes"
Verdict: PASS
```

### 4. Drift between rules and live codebase

Check factual claims against actual files:
- Scraper endpoints: compare with `scrapers/ss_ge.py`, `scrapers/myhome.py`, `scrapers/area_ge.py`.
- Filter parameters: compare with `core/filters.py` and `config/filters.json`.
- Test commands: verify `python -m unittest discover tests` runs clean.

## Output format

When performing an audit, provide a structured findings report:

```markdown
# Audit findings — [Date]

## Pass
- Frontmatter shape & descriptions
- No conflicting rules

## Findings & Recommendations
1. [Finding]: [File + Lines] -> [Recommended Fix]
```
