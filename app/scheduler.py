from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app import campaigns as campaign_engine
from app import inbox as inbox_module
from app.config import get_settings
from app.db import Database
from app.outreach.emailer import process_email_queue
from app.outreach.telegram import process_updates, send_telegram_message
from app.services.discovery import run_discovery_once


async def safe_job(name: str, coro):
    try:
        return await coro
    except Exception as exc:
        db = Database()
        await db.add_event('job:error', {'job': name, 'error': str(exc)})
        return None


def build_scheduler(db: Database) -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone='UTC')

    async def discovery_job():
        await safe_job('discovery', run_discovery_once(db))

    async def telegram_job():
        await safe_job('telegram_poll', process_updates(db))

    async def email_job():
        await safe_job('email_queue', process_email_queue(db))

    async def campaign_job():
        await safe_job('campaign_queue', campaign_engine.process_due_sends(db))

    async def inbox_job():
        if not settings.inbox_poll_enabled or not inbox_module.imap_configured():
            return
        result = await safe_job('inbox_poll', inbox_module.poll_replies(db))
        # Telegram alerting happens inside poll_replies for interested replies.

    scheduler.add_job(discovery_job, IntervalTrigger(minutes=45), id='discovery', max_instances=1, coalesce=True)
    scheduler.add_job(telegram_job, IntervalTrigger(seconds=15), id='telegram_poll', max_instances=1, coalesce=True)
    scheduler.add_job(email_job, IntervalTrigger(minutes=5), id='email_queue', max_instances=1, coalesce=True)
    scheduler.add_job(campaign_job, IntervalTrigger(minutes=settings.inbox_poll_minutes),
                      id='campaign_queue', max_instances=1, coalesce=True)
    scheduler.add_job(inbox_job, IntervalTrigger(minutes=settings.inbox_poll_minutes),
                      id='inbox_poll', max_instances=1, coalesce=True)
    return scheduler
