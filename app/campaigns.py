"""Campaign engine: sequenced outreach over approved/imported contacts.

Safety model preserved from LeadRadarSafe:
- Global + per-campaign daily send caps.
- Minimum seconds between sends.
- Opt-out check before every send.
- Campaign must be 'active' to send; dashboard sends remain one-by-one
  manual unless AUTO_SEND_EMAILS=true.
- Replies stop sequences automatically (see app/inbox.py).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.config import get_settings
from app.db import Database
from app.importer import lead_template_context, render_template
from app.models import LeadStatus


def _utc_iso(dt: datetime) -> str:
    return dt.isoformat(timespec='seconds')


async def attach_contacts(db: Database, campaign_id: int, limit: int | None = None,
                          priority: str | None = None) -> int:
    """Attach eligible leads (with emails, not opted out) to a campaign."""
    rows = await db.list_leads(status=None, limit=10000)
    attached = 0
    for row in rows:
        lead = dict(row)
        if limit is not None and attached >= limit:
            break
        email = lead.get('email')
        if not email:
            continue
        if await db.is_opted_out(email):
            continue
        if priority and lead.get('priority') != priority:
            continue
        if await db.attach_lead_to_campaign(campaign_id, int(lead['id'])):
            attached += 1
    await db.add_event('campaign:attached', {'campaign_id': campaign_id, 'count': attached}, None)
    return attached


async def process_due_sends(db: Database, sender=None) -> dict:
    """Send all due sequence steps across active campaigns within rate limits.

    `sender` may be injected for tests; defaults to the SMTP sender. It must
    return the RFC Message-ID used (or a generated one).
    """
    if sender is None:
        from app.outreach.emailer import send_email_with_id as sender  # type: ignore[no-redef]

    s = get_settings()
    summary = {'sent': 0, 'failed': 0, 'skipped': 0}

    campaigns = await db.list_campaigns()
    for campaign_row in campaigns:
        campaign = dict(campaign_row)
        if campaign['status'] != 'active':
            continue
        steps = [dict(r) for r in await db.list_sequence_steps(int(campaign['id']))]
        steps.sort(key=lambda x: x['step_order'])

        sent_today_campaign = await count_campaign_sent_today(db, int(campaign['id']))
        global_sent_today = await db.sent_count_today()

        due = await db.due_campaign_leads(int(campaign['id']), limit=200)
        for cl in due:
            if sent_today_campaign >= int(campaign['daily_limit']):
                break
            if global_sent_today >= s.smtp_daily_limit:
                return summary
            email = cl.get('email')
            lead_id = int(cl['lead_row_id'])
            if not email:
                await db.advance_campaign_lead(int(cl['id']), int(cl['current_step']), None, True, 'no_email')
                continue
            if await db.is_opted_out(email):
                await db.advance_campaign_lead(int(cl['id']), int(cl['current_step']), None, True, 'opted_out')
                continue
            if int(cl['current_step']) == 0 and await db.was_recently_contacted(email, s.contact_cooldown_days):
                summary['skipped'] += 1
                continue

            current_step = int(cl['current_step'])
            subject_tpl = campaign['subject_template']
            body_tpl = campaign['body_template']
            next_delay_days: int | None = None

            if current_step == 0:
                if steps:
                    next_delay_days = int(steps[0]['delay_days'])
                step_order = 1
            else:
                step_index = current_step - 1
                if step_index >= len(steps):
                    await db.advance_campaign_lead(int(cl['id']), current_step, None, True, 'sequence_done')
                    continue
                step = steps[step_index]
                subject_tpl = step.get('subject_template') or subject_tpl
                body_tpl = step['body_template']
                step_order = current_step + 1
                if step_index + 1 < len(steps):
                    next_delay_days = int(steps[step_index + 1]['delay_days'])

            ctx = lead_template_context(cl)
            ctx['name'] = ctx['name'] or 'there'
            subject = render_template(subject_tpl, ctx).strip()[:200]
            body = render_template(body_tpl, ctx)

            try:
                message_id = await sender(email, subject, body)
                await db.record_message(lead_id, int(campaign['id']), step_order,
                                        email, subject, body,
                                        message_id=message_id, status='sent')
                await db.touch_contact(email, 'sent')
                await db.add_event('email:sent', {
                    'to': email, 'campaign_id': campaign['id'], 'step': step_order}, lead_id)
                if current_step == 0:
                    await db.set_status(lead_id, LeadStatus.SENT, f'campaign {campaign["id"]} step 1')
                    await db.set_pipeline_stage(lead_id, 'contacted', 'initial campaign send')
                sent_today_campaign += 1
                global_sent_today += 1
                summary['sent'] += 1
            except Exception as exc:  # noqa: BLE001 - record failure and keep going
                await db.record_message(lead_id, int(campaign['id']), step_order,
                                        email, subject, body, status='failed', error=str(exc))
                await db.add_event('email:failed', {'to': email, 'error': str(exc)}, lead_id)
                summary['failed'] += 1

            if summary['sent'] + summary['failed'] > 0:
                await asyncio.sleep(s.smtp_min_seconds_between_sends)

            if next_delay_days is not None:
                nxt = datetime.now(timezone.utc) + timedelta(days=next_delay_days)
                await db.advance_campaign_lead(int(cl['id']), step_order, _utc_iso(nxt), False)
            else:
                await db.advance_campaign_lead(int(cl['id']), step_order, None, True, 'sequence_done')
    return summary


async def count_campaign_sent_today(db: Database, campaign_id: int) -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    async with db.connect() as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE campaign_id=? AND status='sent' AND sent_at LIKE ?",
            (campaign_id, f'{today}%'),
        )
        row = await cur.fetchone()
        return int(row['c'] if row else 0)
