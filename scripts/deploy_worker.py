#!/usr/bin/env python3
"""Safe, zero-secret Cloudflare Worker deployment script.

Discovers existing Worker bindings (USERS_KV and DB) from environment variables,
the Cloudflare Worker Settings API, or local Wrangler CLI, validates them
strictly (fail-closed so bindings can never be accidentally detached), generates
an ephemeral cloudflare/wrangler.toml with keep_vars=true, runs `wrangler deploy`,
and removes cloudflare/wrangler.toml in a finally block.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
CLOUDFLARE_DIR = REPO_ROOT / "cloudflare"
WRANGLER_TOML_PATH = CLOUDFLARE_DIR / "wrangler.toml"

_WORKER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_KV_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")
_D1_ID_RE = re.compile(r"^[a-fA-F0-9-]{32,36}$")
_DB_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _load_dotenv_if_present(env_path: Path) -> None:
    """Load key=value pairs from .env into os.environ without overriding existing vars."""
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def _mask_id(value: str) -> str:
    if not value or len(value) < 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def extract_worker_name(worker_name_env: str, proxy_url_env: str) -> str:
    """Resolve the Cloudflare Worker script name from env or CLOUDFLARE_PROXY_URL."""
    explicit = (worker_name_env or "").strip()
    if explicit:
        return explicit

    raw_url = (proxy_url_env or "").strip()
    if not raw_url:
        return ""

    parsed = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
    host = (parsed.hostname or "").strip().lower()
    if host.endswith(".workers.dev"):
        first_label = host.split(".")[0]
        if first_label and first_label != "your-worker":
            return first_label
    return ""


def parse_bindings_from_cf_settings(payload: dict) -> dict[str, str]:
    """Extract USERS_KV and DB binding IDs from Cloudflare Worker settings API response."""
    result = (payload or {}).get("result") or {}
    bindings = result.get("bindings") or []
    resolved: dict[str, str] = {}

    for item in bindings:
        if not isinstance(item, dict):
            continue
        b_type = item.get("type")
        b_name = item.get("name")
        if b_type == "kv_namespace" and b_name == "USERS_KV":
            ns_id = str(item.get("namespace_id") or "").strip()
            if ns_id:
                resolved["kv_namespace_id"] = ns_id
        elif b_type == "d1" and b_name == "DB":
            db_id = str(item.get("id") or item.get("database_id") or "").strip()
            db_name = str(item.get("database_name") or "").strip()
            if db_id:
                resolved["d1_database_id"] = db_id
            if db_name:
                resolved["d1_database_name"] = db_name

    return resolved


def fetch_bindings_via_cf_api(account_id: str, api_token: str, worker_name: str) -> dict[str, str]:
    """Query Cloudflare REST API for the live Worker's attached bindings."""
    if not (account_id and api_token and worker_name):
        return {}

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/workers/scripts/{worker_name}/settings"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return parse_bindings_from_cf_settings(data)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"⚠️ Could not auto-discover bindings from Cloudflare settings API: {exc.__class__.__name__}")
        return {}


def fetch_bindings_via_wrangler_cli() -> dict[str, str]:
    """Fallback discovery via local `npx wrangler` CLI when logged in interactively."""
    resolved: dict[str, str] = {}
    try:
        kv_proc = subprocess.run(
            ["npx", "--yes", "wrangler@3", "kv", "namespace", "list"],
            cwd=str(CLOUDFLARE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if kv_proc.returncode == 0 and kv_proc.stdout.strip().startswith("["):
            kv_list = json.loads(kv_proc.stdout.strip())
            if isinstance(kv_list, list):
                for entry in kv_list:
                    title = str(entry.get("title") or "").upper()
                    if "USERS_KV" in title or len(kv_list) == 1:
                        resolved["kv_namespace_id"] = str(entry.get("id") or "").strip()
                        break
    except Exception:
        pass

    try:
        d1_proc = subprocess.run(
            ["npx", "--yes", "wrangler@3", "d1", "list", "--json"],
            cwd=str(CLOUDFLARE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if d1_proc.returncode == 0 and d1_proc.stdout.strip().startswith("["):
            d1_list = json.loads(d1_proc.stdout.strip())
            if isinstance(d1_list, list):
                for entry in d1_list:
                    name = str(entry.get("name") or "").strip()
                    if name == "real-estate-db" or len(d1_list) == 1:
                        resolved["d1_database_id"] = str(entry.get("uuid") or "").strip()
                        if name:
                            resolved["d1_database_name"] = name
                        break
    except Exception:
        pass

    return resolved


def render_wrangler_toml(
    worker_name: str,
    kv_namespace_id: str,
    d1_database_id: str,
    d1_database_name: str = "real-estate-db",
) -> str:
    """Validate all identifiers (fail-closed) and render wrangler.toml content."""
    w_name = (worker_name or "").strip()
    kv_id = (kv_namespace_id or "").strip()
    d1_id = (d1_database_id or "").strip()
    db_name = (d1_database_name or "real-estate-db").strip() or "real-estate-db"

    if not _WORKER_NAME_RE.match(w_name):
        raise ValueError(
            "Invalid or missing Worker name. Set CLOUDFLARE_WORKER_NAME or CLOUDFLARE_PROXY_URL."
        )
    if not _KV_ID_RE.match(kv_id):
        raise ValueError(
            "Invalid or missing USERS_KV namespace ID (expected 32-char hex). "
            "Refusing to deploy to prevent detaching KV binding."
        )
    if not _D1_ID_RE.match(d1_id):
        raise ValueError(
            "Invalid or missing D1 database ID (expected UUID). "
            "Refusing to deploy to prevent detaching D1 binding."
        )
    if not _DB_NAME_RE.match(db_name):
        raise ValueError("Invalid D1 database name.")

    return (
        f'name = "{w_name}"\n'
        f'main = "worker.js"\n'
        f'compatibility_date = "2024-11-01"\n'
        f"keep_vars = true\n\n"
        f"[[kv_namespaces]]\n"
        f'binding = "USERS_KV"\n'
        f'id = "{kv_id}"\n\n'
        f"[[d1_databases]]\n"
        f'binding = "DB"\n'
        f'database_name = "{db_name}"\n'
        f'database_id = "{d1_id}"\n'
    )


def main() -> int:
    _load_dotenv_if_present(REPO_ROOT / ".env")

    worker_name = extract_worker_name(
        os.environ.get("CLOUDFLARE_WORKER_NAME", ""),
        os.environ.get("CLOUDFLARE_PROXY_URL", ""),
    )
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()

    kv_id = os.environ.get("CLOUDFLARE_KV_NAMESPACE_ID", "").strip()
    d1_id = os.environ.get("CLOUDFLARE_D1_DATABASE_ID", "").strip()
    db_name = os.environ.get("CLOUDFLARE_D1_DATABASE_NAME", "").strip() or "real-estate-db"

    if not (kv_id and d1_id) and account_id and api_token and worker_name:
        discovered = fetch_bindings_via_cf_api(account_id, api_token, worker_name)
        kv_id = kv_id or discovered.get("kv_namespace_id", "")
        d1_id = d1_id or discovered.get("d1_database_id", "")
        if os.environ.get("CLOUDFLARE_D1_DATABASE_NAME", "").strip() == "":
            db_name = discovered.get("d1_database_name", db_name) or db_name

    if not (kv_id and d1_id):
        cli_discovered = fetch_bindings_via_wrangler_cli()
        kv_id = kv_id or cli_discovered.get("kv_namespace_id", "")
        d1_id = d1_id or cli_discovered.get("d1_database_id", "")
        if os.environ.get("CLOUDFLARE_D1_DATABASE_NAME", "").strip() == "":
            db_name = cli_discovered.get("d1_database_name", db_name) or db_name

    try:
        toml_content = render_wrangler_toml(worker_name, kv_id, d1_id, db_name)
    except ValueError as err:
        print(f"❌ Pre-deploy validation failed: {err}", file=sys.stderr)
        return 1

    print(
        f"✅ Verified Worker '{worker_name}' bindings "
        f"(USERS_KV={_mask_id(kv_id)}, DB={_mask_id(d1_id)} [{db_name}])"
    )

    try:
        WRANGLER_TOML_PATH.write_text(toml_content, encoding="utf-8")
        cmd = ["npx", "--yes", "wrangler@3", "deploy"]
        result = subprocess.run(cmd, cwd=str(CLOUDFLARE_DIR), check=False)
        return result.returncode
    finally:
        if WRANGLER_TOML_PATH.exists():
            WRANGLER_TOML_PATH.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
