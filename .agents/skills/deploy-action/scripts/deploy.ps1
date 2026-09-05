<#
.SYNOPSIS
  Automated safe deployment script for Real Estate Scraper 24/7 GitHub Actions.
.DESCRIPTION
  1. Runs all unit tests locally.
  2. Stages code, config, test, and workflow changes cleanly.
  3. Commits changes with descriptive message.
  4. Handles remote database sync (origin/master rebase with data/properties.db safety).
  5. Re-tests post-rebase.
  6. Pushes to origin/master to update GitHub Actions.
#>
param (
    [string]$CommitMessage = "Update Real Estate Scraper codebase and GitHub Actions workflow"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Starting Real Estate Scraper Deployment Workflow          " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Locate Python executable
$pythonExe = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

# 2. Run automated test suite
Write-Host "`n[Step 1/5]: Running automated unit tests..." -ForegroundColor Yellow
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $testOutput = & $pythonExe -m unittest discover tests 2>&1 | Out-String
    $testExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}

if ($testExitCode -ne 0) {
    Write-Host "[ERROR]: Tests failed! Aborting deployment." -ForegroundColor Red
    Write-Host $testOutput
    exit 1
}
Write-Host "[PASS]: All unit tests passed successfully." -ForegroundColor Green

# 3. Stage changes
Write-Host "`n[Step 2/5]: Staging project files..." -ForegroundColor Yellow
git add config/ core/ main.py notifier/ scrapers/ tests/ requirements.txt .github/ .agents/ README.md

$stagedDiff = git diff --staged --name-only
if (-not $stagedDiff) {
    Write-Host "[INFO]: No staged changes detected. Everything up to date." -ForegroundColor Green
    exit 0
}

Write-Host "Staged files for commit:" -ForegroundColor Gray
$stagedDiff | ForEach-Object { Write-Host "  + $_" -ForegroundColor Gray }

# 4. Commit changes
Write-Host "`n[Step 3/5]: Committing changes..." -ForegroundColor Yellow
git commit -m $CommitMessage
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR]: Git commit failed." -ForegroundColor Red
    exit 1
}

# 5. Safe remote synchronization (rebase onto origin/master)
Write-Host "`n[Step 4/5]: Synchronizing with origin/master..." -ForegroundColor Yellow

# Discard local uncommitted changes to tracked database (e.g. from local test runs)
if (Test-Path "data/properties.db") {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    git checkout HEAD -- data/properties.db 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP
}

$hasLocalDb = Test-Path "data/properties.db"
$isDbTrackedLocally = git ls-files "data/properties.db"

# Temporarily move untracked local DB if present to prevent rebase conflicts with CI commits
if ($hasLocalDb -and (-not $isDbTrackedLocally)) {
    Move-Item -Path "data/properties.db" -Destination "data/properties.db.tmp" -Force
}

try {
    git pull --rebase origin master
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR]: Git rebase failed! Aborting rebase..." -ForegroundColor Red
        git rebase --abort
        if (Test-Path "data/properties.db.tmp") {
            Move-Item -Path "data/properties.db.tmp" -Destination "data/properties.db" -Force
        }
        exit 1
    }
} finally {
    if (Test-Path "data/properties.db.tmp") {
        Remove-Item -Path "data/properties.db.tmp" -Force -ErrorAction SilentlyContinue
    }
}

# Post-rebase verification
Write-Host "Re-running tests on rebased commit..." -ForegroundColor Gray
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $pythonExe -m unittest discover tests > $null 2>&1
    $postExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEAP
}
if ($postExitCode -ne 0) {
    Write-Host "[ERROR]: Tests failed post-rebase! Aborting push." -ForegroundColor Red
    exit 1
}

# 6. Push to origin/master
Write-Host "`n[Step 5/5]: Pushing to GitHub (origin/master)..." -ForegroundColor Yellow
git push origin master
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR]: Git push failed." -ForegroundColor Red
    exit 1
}

$latestCommit = git rev-parse --short HEAD
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  Deployment Successful!                                    " -ForegroundColor Green
Write-Host "  Commit: $latestCommit                                     " -ForegroundColor Green
Write-Host "  GitHub Action 'Real Estate Scraper 24/7' is updated.      " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
