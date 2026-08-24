from __future__ import annotations

import asyncio
import email
import imaplib
import re
import time
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from app.config import get_settings
from app.db import Database

_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'\s+')


def _decode(raw: str | None) -> str:
    if not raw:
        return ''
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001
        return raw


def _snippet_from_bytes(payload: bytes, content_type: str) -> str:
    try:
        for charset in ('utf-8', 'latin-1'):
            try:
                text = payload.decode(charset, errors='ignore')
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            text = payload.decode('utf-8', errors='ignore')
    except Exception:  # noqa: BLE001
        return ''
    if 'html' in (content_type or ''):
        text = _TAG.sub(' ', text)
    return _WS.sub(' ', text).strip()[:280]


def fetch_recent(limit: int = 60) -> list[dict]:
    """Fetch the newest messages from INBOX over IMAPS (blocking)."""
    s = get_settings()
    if not (s.smtp_username and s.smtp_app_password):
        raise RuntimeError('Mail credentials are not configured')

    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    out: list[dict] = []
    try:
        mail.login(s.smtp_username, s.smtp_app_password)
        mail.select('INBOX', readonly=True)
        status, data = mail.search(None, 'ALL')
        if status != 'OK':
            return out
        ids = data[0].split()
        for num in ids[-limit:]:
            status, parts = mail.fetch(num, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] BODY.PEEK[TEXT]<0.1536>)')
            if status != 'OK' or not parts:
                continue
            headers_raw = b''
            body_raw = b''
            seen_header = False
            for part in parts:
                if isinstance(part, tuple):
                    meta = part[0] or b''
                    raw = part[1] or b''
                    if b'HEADER' in meta:
                        headers_raw = raw
                        seen_header = True
                    elif b'TEXT' in meta and not seen_header:
                        body_raw = raw
                    else:
                        body_raw += raw
            header_msg = email.message_from_bytes(headers_raw)
            from_name, from_email = parseaddr(_decode(header_msg.get('From')))
            subject = _decode(header_msg.get('Subject'))
            message_id = _decode(header_msg.get('Message-ID')).strip() or f'generated-{num.decode()}-{int(time.time())}'
            try:
                date_utc = parsedate_to_datetime(header_msg.get('Date')).astimezone(timezone.utc).isoformat(timespec='seconds')
            except Exception:  # noqa: BLE001
                date_utc = datetime.now(timezone.utc).isoformat(timespec='seconds')

            snippet = _snippet_from_bytes(body_raw, 'text/plain')
            if not snippet:
                # Some servers merge both sections into one response; try parsing full.
                try:
                    full = email.message_from_bytes(headers_raw + b'\n' + body_raw)
                    if full.is_multipart():
                        for p in full.walk():
                            if p.get_content_type().startswith('text/'):
                                snippet = _snippet_from_bytes(p.get_payload(decode=True) or b'', p.get_content_type())
                                break
                    else:
                        snippet = _snippet_from_bytes(full.get_payload(decode=True) or b'', full.get_content_type())
                except Exception:  # noqa: BLE001
                    snippet = ''

            out.append({
                'message_id': message_id,
                'from_name': from_name or from_email,
                'from_email': from_email,
                'subject': subject or '(no subject)',
                'date_utc': date_utc,
                'snippet': snippet,
            })
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


async def sync_inbox(db: Database, limit: int = 60) -> dict:
    messages = await asyncio.to_thread(fetch_recent, limit)
    new = 0
    for msg in messages:
        msg['lead_id'] = await db.match_lead_by_email(msg.get('from_email'))
        if await db.upsert_inbox_message(msg):
            new += 1
    total = await db.inbox_count()
    return {'fetched': len(messages), 'new': new, 'total': total}
