"""IMAP reply tracker.

Polls the outreach Gmail inbox over IMAPS (port 993), reads message headers
of new mail, and matches replies to previously sent campaign messages via the
In-Reply-To / References / Message-ID chain stored in `messages`.

On a matched reply:
- record it with a keyword classification,
- advance the lead pipeline to 'replied',
- stop any running sequences for that lead,
- (optionally) Telegram alert if configured by caller.

Classification keywords are deliberately conservative: anything unclear stays
'unknown' for human review. No auto-replies are ever sent.
"""
from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from app.config import get_settings
from app.db import Database

INTERESTED = (
    'interested', 'yes please', 'sounds good', 'let\'s do', 'lets do',
    'we can talk', 'happy to', 'keen', 'works for me', 'schedule a call',
    'send more', 'tell me more', 'book', 'available for',
)
MAYBE_LATER = ('next year', 'not now', 'later this year', 'circle back',
               'revisit', 'next quarter', 'busy season')
NOT_INTERESTED = ('not interested', 'no thanks', 'nothankyou', 'unsubscribe',
                  'stop emailing', 'remove me', 'take me off')
BOUNCE = ('undeliverable', 'delivery status notification', 'mailer-daemon',
          'address not found', 'recipient address rejected', 'mailbox unavailable')
OOO = ('out of office', 'automatic reply', 'auto reply', 'on leave',
       'away from the office')


def classify_reply(subject: str, snippet: str, from_addr: str = '') -> str:
    text = f'{subject}\n{snippet}'.lower()
    sender = from_addr.lower()
    if 'mailer-daemon' in sender or 'postmaster' in sender:
        return 'bounce'
    for marker in BOUNCE:
        if marker in text:
            return 'bounce'
    for marker in OOO:
        if marker in text and len(text) < 800:
            return 'ooo'
    for marker in NOT_INTERESTED:
        if marker in text:
            return 'not_interested'
    for marker in BOUNCE:
        if marker in text:
            return 'bounce'
    for marker in MAYBE_LATER:
        if marker in text:
            return 'maybe_later'
    for marker in INTERESTED:
        if marker in text:
            return 'interested'
    return 'unknown'


def _extract_body(msg: email_lib.message.Message, limit: int = 1500) -> str:
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                try:
                    payload = part.get_payload(decode=True) or b''
                    body = payload.decode('utf-8', errors='replace')
                    break
                except Exception:  # noqa: BLE001
                    continue
    else:
        payload = msg.get_payload(decode=True) or b''
        body = payload.decode('utf-8', errors='replace')
    return re.sub(r'\s+', ' ', body).strip()[:limit]


def imap_configured() -> bool:
    s = get_settings()
    return bool(s.imap_host and s.smtp_username and s.smtp_app_password)


def _poll_once_sync(db_state_uid: int) -> list[dict]:
    """Blocking IMAP fetch; returns parsed headers/bodies newer than state uid."""
    s = get_settings()
    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        mail.login(s.smtp_username or '', s.smtp_app_password or '')
        mail.select('INBOX', readonly=True)

        criteria = f'(UID {db_state_uid + 1}:*)' if db_state_uid > 0 else '(UNSEEN)'
        status, data = mail.uid('search', None, criteria)
        if status != 'OK':
            return []
        uid_list = data[0].split()
        results: list[dict] = []
        for uid in uid_list:
            uid_int = int(uid.decode())
            if db_state_uid > 0 and uid_int <= db_state_uid:
                continue
            status2, msg_data = mail.uid('fetch', uid, '(RFC822)')
            if status2 != 'OK' or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            references = msg.get_all('References') or []
            in_reply_to = msg.get('In-Reply-To', '')
            candidates = []
            mid = (msg.get('Message-ID') or '').strip()
            if in_reply_to:
                candidates.append(in_reply_to.strip())
            for ref_line in references:
                candidates.extend(x.strip() for x in ref_line.split() if '@' in x)
            date_hdr = msg.get('Date')
            try:
                received_at = parsedate_to_datetime(date_hdr).isoformat(timespec='seconds') if date_hdr else None
            except (TypeError, ValueError):
                received_at = None
            results.append({
                'imap_uid': uid_int,
                'message_id': mid,
                'candidates': [c for c in candidates if c],
                'from': parseaddr(msg.get('From', ''))[1],
                'subject': msg.get('Subject', ''),
                'body': _extract_body(msg),
                'received_at': received_at,
            })
        return results
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


async def poll_replies(db: Database, telegram_notify=None) -> dict:
    """Fetch new inbox messages; match to sent messages; update leads."""
    if not imap_configured():
        return {'checked': 0, 'matched': 0}

    state_key = 'imap_last_uid'
    last_uid = int(await db.get_state(state_key, 0) or 0)
    messages = await asyncio.to_thread(_poll_once_sync, last_uid)

    matched = 0
    highest_uid = last_uid
    for item in messages:
        highest_uid = max(highest_uid, int(item['imap_uid']))
        row = await db.find_message_by_rfc_ids(item['candidates'])
        if row is None:
            # No matching outbound message — still create a thread for incoming
            if lead_row := _find_lead_by_email(item['from'], db):
                await db.upsert_thread(int(lead_row['id']), item['from'], item['subject'],
                                       'inbound', item['received_at'])
            continue
        matched += 1
        classification = classify_reply(item['subject'], item['body'], item['from'])
        lead_row = await db.get_lead(int(row['lead_id'])) if row['lead_id'] else None
        await db.record_reply(
            int(row['lead_id']) if row['lead_id'] else None,
            int(row['id']),
            item['from'], item['subject'],
            item['body'][:600], classification, item['received_at'],
        )
        # Create/update email thread
        if lead_row:
            thread_key = item['from']
            await db.upsert_thread(int(lead_row['id']), thread_key, item['subject'],
                                   'inbound', item['received_at'])
            # Log activity
            await db.log_activity(int(lead_row['id']), None, 'reply',
                                  f'{classification}: {item["subject"][:80]}',
                                  metadata={'classification': classification, 'from': item['from']})
            # Record send-time for optimization (replied=True)
            if item['received_at']:
                try:
                    from datetime import datetime as dt
                    received = dt.fromisoformat(item['received_at'].replace('Z', '+00:00'))
                    await db.record_send_time(int(lead_row['id']),
                                              received.hour, received.weekday(), replied=True)
                except (ValueError, TypeError):
                    pass
        if lead_row:
            stage = {'interested': 'replied', 'maybe_later': 'replied',
                     'unknown': 'replied', 'not_interested': 'lost',
                     'bounce': 'lost', 'ooo': 'contacted'}.get(classification, 'replied')
            await db.set_pipeline_stage(int(lead_row['id']), stage,
                                        f'reply classified: {classification}')
        if row['lead_id']:
            await db.stop_campaign_leads_for_reply(int(row['lead_id']))
        # Trigger webhooks for interesting replies
        if classification in ('interested', 'not_interested', 'bounce'):
            await db.trigger_webhooks('reply', {
                'lead_id': row['lead_id'],
                'from': item['from'],
                'subject': item['subject'],
                'classification': classification,
                'snippet': item['body'][:300],
            })
        if telegram_notify and classification == 'interested' and lead_row:
            await telegram_notify(
                f"Interested reply from {lead_row['name']} ({item['from']}):\n"
                f"Subject: {item['subject']}\n\n{item['body'][:400]}")

    if highest_uid > last_uid:
        await db.set_state(state_key, highest_uid)
    await db.add_event('inbox:polled', {'new': len(messages), 'matched': matched})
    return {'checked': len(messages), 'matched': matched}


async def _find_lead_by_email(email: str, db: Database):
    """Find a lead by email address."""
    async with db.connect() as conn:
        conn.row_factory = __import__('aiosqlite').Row
        cur = await conn.execute('SELECT * FROM leads WHERE email=? LIMIT 1', (email,))
        return await cur.fetchone()
