import asyncio
import os
import sys
import threading
import time
from datetime import datetime
import gradio as gr
from main import RealEstateOrchestrator
from config import settings

activity_logs = []
last_run_stats = {
    "time": "Initializing...",
    "stats": "Starting 24/7 background worker..."
}
orchestrator = None
is_running = True

def add_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] {message}"
    activity_logs.append(entry)
    if len(activity_logs) > 100:
        activity_logs.pop(0)
    print(entry)

def run_worker_loop():
    global orchestrator, last_run_stats, is_running
    add_log("🚀 Background 24/7 Polling Engine Initialized...")
    
    # Run async event loop in this background worker thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    orchestrator = RealEstateOrchestrator()
    interval = settings.CHECK_INTERVAL_SECONDS or 180
    add_log(f"⏱️ Check interval: {interval} seconds (~{interval//60} mins)")

    while is_running:
        try:
            add_log("🔍 Scanning MyHome.ge and SS.ge for new properties...")
            stats = loop.run_until_complete(orchestrator.run_cycle())
            last_run_stats = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "stats": f"Fetched: {stats.get('total_fetched', 0)} | New: {stats.get('new_listings', 0)} | Matched: {stats.get('matched_filters', 0)} | Alerts: {stats.get('notifications_sent', 0)}"
            }
            add_log(f"✅ Cycle completed: {last_run_stats['stats']}")
        except Exception as e:
            add_log(f"⚠️ Error during cycle: {e}")
        
        time.sleep(interval)

# Start background daemon thread
worker_thread = threading.Thread(target=run_worker_loop, daemon=True)
worker_thread.start()

def get_live_status():
    logs_text = "\n".join(reversed(activity_logs[-30:]))
    summary_text = (
        f"### 🟢 System Status: **ACTIVE (24/7 Background Polling)**\n\n"
        f"- **Last Run Time:** `{last_run_stats['time']}`\n"
        f"- **Last Cycle Stats:** `{last_run_stats['stats']}`\n"
        f"- **Filter Criteria:** `$45,000 – $75,000` | `40 – 70 m²` | `Physical Owner`\n"
        f"- **Telegram Bot:** `Active (@tbilisi_real_estate1_bot)`\n"
        f"- **Cloudflare Proxy:** `Active (0.1s Fast Fetch)`"
    )
    return summary_text, logs_text

def manual_scan():
    add_log("⚡ Manual Instant Scan Triggered by User!")
    if orchestrator:
        try:
            loop = asyncio.new_event_loop()
            stats = loop.run_until_complete(orchestrator.run_cycle())
            loop.close()
            add_log(f"⚡ Manual Scan Finished: Fetched: {stats.get('total_fetched', 0)}, Alerts: {stats.get('notifications_sent', 0)}")
        except Exception as e:
            add_log(f"❌ Manual Scan Error: {e}")
    return get_live_status()

# Simple Gradio UI
with gr.Blocks(title="Tbilisi Real Estate Radar 24/7") as demo:
    gr.Markdown("# 🏢 Tbilisi Real Estate Radar (24/7 Cloud Tracker)")
    gr.Markdown("Real-time automated scraper for MyHome.ge and SS.ge. Sends instant alerts to Telegram when new apartments appear.")
    
    status_md = gr.Markdown(value="### 🟢 System Status: Active (24/7 Background Polling)")
    
    with gr.Row():
        scan_btn = gr.Button("🔄 Run Instant Check Now", variant="primary")
        refresh_btn = gr.Button("📋 Refresh Logs")
    
    logs_box = gr.Textbox(value="Initializing live activity logs...", label="Live Activity & Alert Logs", lines=14, max_lines=25)
    
    demo.load(fn=get_live_status, outputs=[status_md, logs_box])
    refresh_btn.click(fn=get_live_status, outputs=[status_md, logs_box])
    scan_btn.click(fn=manual_scan, outputs=[status_md, logs_box])

if __name__ == "__main__":
    demo.launch()
