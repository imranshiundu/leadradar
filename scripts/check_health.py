import asyncio
import json
from app.db import Database
from app.config import get_settings

async def check_health():
    db = Database()
    settings = get_settings()
    
    print("--- System Health Report ---")
    print(f"AI Enabled: {settings.ai_enabled}")
    print(f"Telegram Polling: {settings.telegram_polling_enabled}")
    
    # Check Leads
    leads = await db.list_leads(limit=100)
    print(f"Total Leads in DB: {len(leads)}")
    
    scored_leads = [l for l in leads if l['need_score'] > 0]
    print(f"Scored Leads (>0): {len(scored_leads)}")
    
    status_counts = {}
    for l in leads:
        status = l['status']
        status_counts[status] = status_counts.get(status, 0) + 1
    print(f"Status Counts: {status_counts}")
    
    # Check Events
    async with db.connect() as conn:
        conn.row_factory = None
        cur = await conn.execute("SELECT event_type, COUNT(*) FROM events_log GROUP BY event_type")
        events = await cur.fetchall()
        print(f"Events: {events}")

    if len(leads) == 0:
        print("WARNING: No leads found. Discovery may have failed or hasn't run yet.")
    elif len(scored_leads) == 0 and settings.ai_enabled:
        print("WARNING: AI is enabled but no leads are scored. AI might be failing or discovery ran without AI.")

if __name__ == "__main__":
    asyncio.run(check_health())
