from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from app.config import get_settings
from app.db import Database
from app.models import LeadStatus
from app.safety import looks_like_personal_email, safe_outreach_message


def configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_username and s.smtp_app_password and (s.smtp_from_email or s.smtp_username))


def build_subject(lead: dict) -> str:
    name = str(lead.get('name') or 'your business')[:70]
    if lead.get('opportunity_type') == 'job':
        return f'Application / interest: {name}'
    return f'Website idea for {name}'


def build_body(lead: dict) -> str:
    draft = lead.get('draft_message') or ''
    if len(draft.strip()) > 20:
        return draft.strip()
    return safe_outreach_message(str(lead.get('name') or 'there'))


async def send_email(to_email: str, subject: str, body: str) -> None:
    await send_email_with_id(to_email, subject, body)


async def send_email_with_id(to_email: str, subject: str, body: str) -> str:
    """Send an email and return the RFC 5322 Message-ID used for reply tracking."""
    s = get_settings()
    from_email = s.smtp_from_email or s.smtp_username
    assert from_email is not None
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((s.smtp_from_name, from_email))
    msg['To'] = to_email
    msg['Message-ID'] = make_msgid(domain=from_email.split('@')[-1])
    msg.set_content(body)
    message_id = msg['Message-ID']

    def _send() -> None:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=25) as smtp:
            smtp.starttls()
            smtp.login(s.smtp_username, s.smtp_app_password)
            smtp.send_message(msg)

    await asyncio.to_thread(_send)
    return message_id


async def process_email_queue(db: Database) -> int:
    s = get_settings()
    if not configured() or not s.auto_send_emails:
        return 0
    sent_today = await db.sent_count_today()
    if sent_today >= s.smtp_daily_limit:
        return 0
    rows = await db.list_leads(status=LeadStatus.APPROVED.value, limit=10)
    sent = 0
    for row in rows:
        if sent_today + sent >= s.smtp_daily_limit:
            break
        lead = dict(row)
        email = lead.get('email')
        if not email or await db.is_opted_out(email):
            await db.set_status(int(lead['id']), LeadStatus.ERROR, 'missing email or opted out')
            continue
        if looks_like_personal_email(email) and lead.get('opportunity_type') == 'website_lead':
            await db.set_status(int(lead['id']), LeadStatus.ERROR, 'personal email skipped for cold business outreach')
            continue
        await send_email(email, build_subject(lead), build_body(lead))
        await db.add_event('email:sent', {'to': email, 'subject': build_subject(lead)}, int(lead['id']))
        await db.set_status(int(lead['id']), LeadStatus.SENT, 'email sent')
        sent += 1
        await asyncio.sleep(s.smtp_min_seconds_between_sends)
    return sent
