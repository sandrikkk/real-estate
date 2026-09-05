---
name: security-audit
description: Performs a comprehensive, evidence-based security audit of the codebase. Focuses on exploitable vulnerabilities, secrets detection, dependency risks, and configuration weaknesses with minimal false positives. Use when asked to audit code security, review vulnerabilities, or run a security check.
---

# Security Audit Skill

Performs a rigorous, multi-category security audit of the target codebase, identifying exploitable vulnerabilities, secret exposures, supply-chain risks, and configuration weaknesses while actively suppressing false positives.

## Core Principles

1. **Only Report What Has Real Impact**:
   - Every finding must describe a concrete attack path: who the attacker is, the vector of exploitation, and the actual impact.
   - Suppress theoretical or inapplicable vulnerabilities (e.g. do not report CORS/CSRF on command-line tools or scrapers with no web interface).
2. **Context-Aware Baseline**:
   - Calibrate checks based on application type (CLI, Web Service, Scraper, Telegram Bot, Background Job).
3. **Actionable Remediation**:
   - Every identified issue must be accompanied by the exact file, line number, severity rating, and concrete remediation instructions or code diff.

---

## Audit Pipeline

### Phase 1: Reconnaissance
- Map components, entry points, background jobs, external API interactions, and configuration sources.
- Identify trust boundaries (e.g. untrusted scraped web content, environment variables, GitHub Actions runners).

### Phase 2: Vulnerability Hunting

#### 1. Secrets & Credentials Management
- Detect hardcoded secrets, API tokens, bot tokens, and credentials in source code and git status.
- Verify `.gitignore` coverage (ensuring `.env`, secret dumps, credential caches are excluded).
- Check log statements for inadvertent credential exposure.

#### 2. CI/CD & GitHub Actions Security
- Audit workflow triggers, permissions (`permissions: read-all` vs least privilege).
- Check for secrets exposure in build logs, runner environments, or script arguments.
- Pin third-party actions to immutable commit SHAs instead of mutable tags.

#### 3. Injection & Input Validation
- Path Traversal in file read/write operations (e.g., dynamic cache keys, file saving).
- Command injection or arbitrary code execution via subprocesses or dynamic imports.
- Telegram message injection (unescaped user/scraped text in HTML/Markdown parse modes).
- Input validation on configuration files (e.g., `filters.json`).

#### 4. Network, Scraping & Operational Resilience
- Unbounded network requests (missing timeouts, uncontrolled retry storms).
- Server-Side Request Forgery (SSRF) if URLs are constructed from untrusted sources.
- Error handling that could reveal internal architecture or crash background daemons.

#### 5. Dependencies & Supply Chain Risks
- Review direct and transitive dependencies in `requirements.txt` / package definitions.
- Flag outdated libraries or dependencies with known vulnerabilities (CVEs).

---

### Phase 3: Adversarial Validation
- Review candidate findings against the codebase.
- Attempt to disprove each finding: Is the input actually attacker-controlled? Is there mitigating validation upstream?
- Discard findings that cannot be practically exploited or that represent standard, harmless patterns.

---

### Phase 4: Reporting
Generate a structured report containing:
1. **Security Scorecard**: Overview table categorized by severity (Critical, High, Medium, Low, Informational).
2. **Findings with Details**:
   - Title & Severity
   - Vulnerability Category
   - Exact Location (`file:///path/to/file#L...`)
   - Proof of Concept / Attack Vector
   - Remediation Guidance (Concrete code fix)
3. **Positive Security Practices**: Acknowledge defenses and good security patterns already in place.
