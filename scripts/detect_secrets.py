#!/usr/bin/env python3
"""
Pre-commit security hook: Prevent leakage of sensitive information & credentials.

Checks staged git diffs and filenames for:
- Telegram Bot Tokens
- Cloudflare API tokens / Sync keys
- Scraper API keys
- Private keys (RSA, EC, OpenSSH)
- AWS / GCP / GitHub tokens
- .env and credentials files
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any

# Forbidden filename patterns for git commits
FORBIDDEN_FILENAME_PATTERNS = [
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),  # .env, .env.local, etc.
    re.compile(r"^.*\.pem$", re.IGNORECASE),
    re.compile(r"^.*\.key$", re.IGNORECASE),
    re.compile(r"^.*\.pfx$", re.IGNORECASE),
    re.compile(r"^.*\.p12$", re.IGNORECASE),
    re.compile(r"^id_(?:rsa|dsa|ecdsa|ed25519)(?:\..+)?$", re.IGNORECASE),
    re.compile(r"^(?:client_secrets?|service[-_]?account|credentials)\.json$", re.IGNORECASE),
]

# Allowlisted filename patterns (safe examples / templates)
ALLOWLISTED_FILENAMES = {
    ".env.example",
    ".env.template",
    ".env.sample",
}

# Secret regex rules
SECRET_RULES: List[Dict[str, Any]] = [
    {
        "name": "Telegram Bot Token",
        "pattern": re.compile(r"\b[0-9]{8,10}:[a-zA-Z0-9_-]{35}\b"),
        "severity": "CRITICAL",
    },
    {
        "name": "Cloudflare Sync Key / API Token",
        "pattern": re.compile(
            r"(?:CLOUDFLARE_SYNC_KEY|CLOUDFLARE_API_TOKEN|CF_TOKEN)\s*[:=]\s*['\"][a-zA-Z0-9_-]{16,}['\"]"
        ),
        "severity": "CRITICAL",
    },
    {
        "name": "Scraper API Key",
        "pattern": re.compile(r"(?:SCRAPER_API_KEY)\s*[:=]\s*['\"][a-zA-Z0-9]{16,}['\"]"),
        "severity": "CRITICAL",
    },
    {
        "name": "Private Key Block",
        "pattern": re.compile(r"-----BEGIN [A-Z0-9_ ]*PRIVATE KEY-----"),
        "severity": "CRITICAL",
    },
    {
        "name": "AWS Access Key",
        "pattern": re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"),
        "severity": "HIGH",
    },
    {
        "name": "GitHub Personal Access Token",
        "pattern": re.compile(
            r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{82}\b"
        ),
        "severity": "CRITICAL",
    },
    {
        "name": "OpenAI / Anthropic API Key",
        "pattern": re.compile(r"\bsk-(?:live_|test_)?[a-zA-Z0-9-]{20,}\b"),
        "severity": "HIGH",
    },
    {
        "name": "Slack Token",
        "pattern": re.compile(r"\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,32}\b"),
        "severity": "HIGH",
    },
    {
        "name": "Generic Hardcoded Secret Assignment",
        "pattern": re.compile(
            r"(?:api[_-]?key|secret[_-]?key|auth[_-]?token|bearer[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.]{24,}['\"]",
            re.IGNORECASE,
        ),
        "severity": "HIGH",
    },
]

# Patterns that indicate false positives / mock testing / env variables
SAFE_PATTERNS = [
    re.compile(
        r"(?:dummy|mock|placeholder|example|your[-_]?token|your[-_]?key|test[-_]?token|xxx+)",
        re.IGNORECASE,
    ),
    re.compile(r"\$\{\{\s*secrets\..+?\}\}"),  # GitHub Actions secrets expression
    re.compile(r"os\.(?:getenv|environ)"),
    re.compile(r"TEST_DUMMY_TOKEN"),
    re.compile(r"123456789:ABCdefGHIjklMNOpqrsTUVwxyz01234567"),  # Canonical test mock token
]


def mask_secret(text: str) -> str:
    """Mask sensitive string leaving only small prefix and suffix visible."""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}***...***{text[-4:]}"


def is_allowlisted_file(file_path: str) -> bool:
    name = Path(file_path).name.lower()
    return name in {f.lower() for f in ALLOWLISTED_FILENAMES}


def check_forbidden_filenames(staged_files: List[str]) -> List[Tuple[str, str]]:
    violations = []
    for file_path in staged_files:
        if is_allowlisted_file(file_path):
            continue
        name = Path(file_path).name
        for pattern in FORBIDDEN_FILENAME_PATTERNS:
            if pattern.search(name):
                violations.append(
                    (file_path, f"Forbidden file type: `{name}` matches `{pattern.pattern}`")
                )
                break
    return violations


def check_content_line(line: str) -> List[Tuple[str, str, str]]:
    """
    Check a single added line of text for secret patterns.
    Returns list of (rule_name, severity, masked_match).
    """
    # Quick filter: skip empty or purely comment lines
    stripped = line.strip()
    if not stripped:
        return []

    # Check for safe patterns / placeholders
    for safe in SAFE_PATTERNS:
        if safe.search(line):
            return []

    hits = []
    for rule in SECRET_RULES:
        match = rule["pattern"].search(line)
        if match:
            matched_text = match.group(0)
            hits.append((rule["name"], rule["severity"], mask_secret(matched_text)))
    return hits


def get_staged_files() -> List[str]:
    """Get list of staged files in git."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [f.strip() for f in res.stdout.splitlines() if f.strip()]
    except Exception as e:
        print(f"[!] Warning: Could not inspect staged files: {e}", file=sys.stderr)
        return []


def get_staged_diff() -> str:
    """Get staged diff with 0 context lines."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "-U0"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return res.stdout
    except Exception as e:
        print(f"[!] Warning: Could not read git diff: {e}", file=sys.stderr)
        return ""


def scan_diff_for_secrets(diff_text: str) -> List[Dict[str, Any]]:
    """Scan staged diff lines for secrets."""
    violations = []
    current_file = ""
    current_line_num = 0

    for raw_line in diff_text.splitlines():
        # Track current file
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip()
            continue

        # Skip files in allowlist or tests
        if is_allowlisted_file(current_file) or current_file.startswith("tests/"):
            continue

        # Track line number from chunk header (e.g., @@ -1,0 +15,2 @@)
        if raw_line.startswith("@@"):
            chunk_match = re.search(r"\+(\d+)", raw_line)
            if chunk_match:
                current_line_num = int(chunk_match.group(1)) - 1
            continue

        # Only inspect added lines
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current_line_num += 1
            added_content = raw_line[1:]
            hits = check_content_line(added_content)
            for rule_name, severity, masked in hits:
                violations.append(
                    {
                        "file": current_file,
                        "line": current_line_num,
                        "rule": rule_name,
                        "severity": severity,
                        "masked": masked,
                    }
                )
        elif not raw_line.startswith("-"):
            current_line_num += 1

    return violations


def main() -> int:
    staged_files = get_staged_files()
    if not staged_files:
        return 0

    # 1. Check staged filenames
    filename_violations = check_forbidden_filenames(staged_files)

    # 2. Check staged content additions
    diff_text = get_staged_diff()
    content_violations = scan_diff_for_secrets(diff_text)

    if not filename_violations and not content_violations:
        print(
            "\033[92m[PASS]\033[0m Pre-commit security check: No secrets or credentials detected."
        )
        return 0

    # Render error box
    print("\n" + "=" * 76)
    print("\033[91m[BLOCKED]\033[0m PRE-COMMIT SECURITY VIOLATION: SENSITIVE DATA DETECTED!")
    print("=" * 76)

    if filename_violations:
        print("\n\033[93m[!] Staged forbidden files:\033[0m")
        for file_path, reason in filename_violations:
            print(f"  - {file_path}: {reason}")
        print("\nFix: Remove these files from git staging with: git reset HEAD <file>")

    if content_violations:
        print("\n\033[93m[!] Staged credentials / secrets in code:\033[0m")
        for v in content_violations:
            print(
                f"  - {v['file']}:{v['line']} [{v['severity']}] {v['rule']} (Value: {v['masked']})"
            )
        print("\nFix: Use environment variables or GitHub Secrets instead of hardcoding secrets.")

    print("=" * 76 + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
