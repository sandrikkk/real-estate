"""
Migration script: Copies data from local data/properties.db to Cloudflare D1 via Worker proxy.
Can be safely re-run multiple times (uses INSERT OR IGNORE).
"""

import sqlite3
import sys
import time
from pathlib import Path

# Ensure UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from core.database import D1Client


def migrate():
    local_db_path = Path(settings.DATABASE_PATH)
    if not local_db_path.exists():
        print(f"❌ Local database {local_db_path} not found.")
        return

    worker_url = settings.CLOUDFLARE_PROXY_URL
    sync_key = settings.CLOUDFLARE_SYNC_KEY
    if not worker_url:
        print("❌ CLOUDFLARE_PROXY_URL is not configured in settings / .env")
        return

    print("=" * 60)
    print("  🚀 Cloudflare D1 Migration Tool")
    print(f"  Local DB:   {local_db_path}")
    print(f"  Worker URL: {worker_url}")
    print("=" * 60)

    client = D1Client(worker_url, sync_key, timeout=30)

    # 1. Test D1 connectivity
    print("\n🔍 Testing D1 connection via Worker...")
    try:
        res = client.query("SELECT 1 as test")
        if not res.get("success"):
            print(f"❌ Worker returned error: {res}")
            return
        print("✅ D1 is reachable and ready!")
    except Exception as e:
        print(f"❌ Failed to reach D1 via Worker: {e}")
        print(
            "💡 Make sure you updated cloudflare/worker.js in Cloudflare Dashboard and clicked 'Save and Deploy'!"
        )
        return

    conn = sqlite3.connect(local_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 2. Migrate properties
    cursor.execute("SELECT COUNT(*) FROM properties")
    total_props = cursor.fetchone()[0]
    print(f"\n📦 Found {total_props} properties to migrate.")

    if total_props > 0:
        cursor.execute("PRAGMA table_info(properties)")
        cols = [r["name"] for r in cursor.fetchall()]
        cols_str = ", ".join(cols)
        placeholders = ", ".join(["?"] * len(cols))
        insert_sql = f"INSERT OR IGNORE INTO properties ({cols_str}) VALUES ({placeholders})"

        cursor.execute(f"SELECT {cols_str} FROM properties")
        batch_size = 50
        migrated = 0

        start_time = time.time()
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            queries = [{"sql": insert_sql, "params": list(r)} for r in rows]
            try:
                client.batch(queries)
                migrated += len(rows)
                elapsed = time.time() - start_time
                pct = (migrated / total_props) * 100
                print(
                    f"   Transferred {migrated}/{total_props} properties ({pct:.1f}%) in {elapsed:.1f}s",
                    end="\r",
                )
            except Exception as e:
                print(f"\n❌ Error transferring batch: {e}")
                break

        print(f"\n✅ Finished properties migration: {migrated}/{total_props} transferred.")

    # 3. Migrate user_notifications
    try:
        cursor.execute("SELECT COUNT(*) FROM user_notifications")
        total_notifs = cursor.fetchone()[0]
    except Exception:
        total_notifs = 0

    print(f"\n🔔 Found {total_notifs} user notifications to migrate.")
    if total_notifs > 0:
        cursor.execute("SELECT chat_id, listing_id, sent_at FROM user_notifications")
        insert_notif_sql = "INSERT OR IGNORE INTO user_notifications (chat_id, listing_id, sent_at) VALUES (?, ?, ?)"
        migrated_n = 0
        while True:
            rows = cursor.fetchmany(100)
            if not rows:
                break
            queries = [{"sql": insert_notif_sql, "params": list(r)} for r in rows]
            try:
                client.batch(queries)
                migrated_n += len(rows)
                print(f"   Transferred {migrated_n}/{total_notifs} notifications...", end="\r")
            except Exception as e:
                print(f"\n❌ Error transferring notifications batch: {e}")
                break
        print(f"\n✅ Finished notifications migration: {migrated_n}/{total_notifs} transferred.")

    # 4. Migrate image_hashes if any
    try:
        cursor.execute("SELECT COUNT(*) FROM image_hashes")
        total_hashes = cursor.fetchone()[0]
    except Exception:
        total_hashes = 0

    if total_hashes > 0:
        print(f"\n🖼️ Found {total_hashes} image hashes to migrate.")
        cursor.execute("SELECT listing_id, image_index, phash FROM image_hashes")
        insert_hash_sql = (
            "INSERT OR IGNORE INTO image_hashes (listing_id, image_index, phash) VALUES (?, ?, ?)"
        )
        migrated_h = 0
        while True:
            rows = cursor.fetchmany(100)
            if not rows:
                break
            queries = [{"sql": insert_hash_sql, "params": list(r)} for r in rows]
            try:
                client.batch(queries)
                migrated_h += len(rows)
            except Exception:
                break
        print(f"✅ Finished image hashes migration: {migrated_h}/{total_hashes} transferred.")

    conn.close()
    print("\n" + "=" * 60)
    print("  🎉 Cloudflare D1 Migration Complete!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    migrate()
