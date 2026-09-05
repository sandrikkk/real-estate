---
name: deploy-action
description: Automatically deploys code changes to GitHub, synchronizes remote repository state, runs pre-flight tests, and updates the 'Real Estate Scraper 24/7' GitHub Actions workflow. Use this skill when the user asks to deploy, push changes, update GitHub Actions, or run the deployment pipeline (e.g., "დაფუშე", "გაუშვი დიფლოი", "action განაახლე", "deploy").
---

# Deploy Real Estate Scraper & GitHub Actions Workflow

This skill encapsulates the safe, zero-downtime deployment process for the Real Estate Scraper repository (`https://github.com/sandrikkk/real-estate.git`). It ensures all changes pass automated test suites, safely reconciles remote database commits created by the GitHub Actions bot (`data/properties.db`), and updates the **Real Estate Scraper 24/7** workflow (`.github/workflows/scrape.yml`).

---

## 1. Quick Automated Execution

To execute the entire deployment in one command, run the automated helper script:

```powershell
powershell -ExecutionPolicy Bypass -File .agents/skills/deploy-action/scripts/deploy.ps1 -CommitMessage "Descriptive commit message"
```

The script automatically executes all steps below with built-in rollback and error prevention.

---

## 2. Step-by-Step Deployment Runbook

When executing manually or inspecting pipeline failures, follow these exact procedures:

### Step 1: Pre-Flight Automated Verification
Before touching git or pushing any code, run the full test suite locally:
```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```
- **Success Criteria**: All 31+ unit tests pass with `OK` (0 failures, 0 errors).
- **Rule**: If any test fails, **ABORT** deployment immediately and fix the regression.

### Step 2: Surgical Staging
Stage only relevant project files (do not commit temporary test files, scratch scripts, or local `.env`):
```powershell
git add config/ core/ main.py notifier/ scrapers/ tests/ requirements.txt .github/ .agents/
```
Verify staged files with:
```powershell
git status --short
```

### Step 3: Atomic Commit
Commit staged files with a clear, concise summary of the changes:
```powershell
git commit -m "Your descriptive commit message"
```

### Step 4: Safe Remote Synchronization (Rebase with DB Safety)
The GitHub Actions workflow runs every 15 minutes and commits updated listing states (`Auto-update seen properties database [skip ci]`) to `origin/master`.

To avoid untracked working-tree conflicts:
1. Temporarily move untracked local SQLite database if present:
   ```powershell
   if (Test-Path "data/properties.db") { Move-Item "data/properties.db" "data/properties.db.tmp" -Force }
   ```
2. Pull and rebase onto remote master:
   ```powershell
   git pull --rebase origin master
   ```
3. Remove temporary local DB copy (the official database is checked out from remote):
   ```powershell
   if (Test-Path "data/properties.db.tmp") { Remove-Item "data/properties.db.tmp" -Force }
   ```

### Step 5: Post-Rebase Verification
Verify tests pass on the newly rebased tree:
```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```

### Step 6: Push to Origin Master
Push the verified commit to GitHub:
```powershell
git push origin master
```

---

## 3. Post-Deployment Verification

After the push completes:
1. Verify the latest commit hash:
   ```powershell
   git log -n 1 --oneline
   ```
2. Confirm the GitHub Actions workflow **[Real Estate Scraper 24/7](file:///c:/Users/user/PycharmProjects/PythonProject1/.github/workflows/scrape.yml)** is updated. It will trigger automatically on the next 15-minute cron cycle, or can be triggered immediately via `workflow_dispatch`.
