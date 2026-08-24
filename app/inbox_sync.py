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
_RE_PREFIX = re.compile(r'^\s*((re|fwd|fw)\s*:\s*)+', re.IGNORECASE)


def normalize_subject(subject: str | None) -> str:
    s = _RE_PREFIX.sub('', (subject or '').strip()).lower()
    return _WS.sub(' ', s)


def make_group_key(from_email: str | None, subject: str | None) -> str:
    return f'{(from_email or "unknown").strip().lower()}|{normalize_subject(subject)}'


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
                'group_key': make_group_key(from_email, subject),
            })
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def _find_uid(mail: imaplib.IMAP4_SSL, message_id: str) -> bytes | None:
    status, data = mail.search(None, '(HEADER Message-ID "%s")' % message_id.replace('"', ''))
    if status != 'OK' or not data or not data[0]:
        return None
    uids = data[0].split()
    return uids[-1] if uids else None


def fetch_full_body(message_id: str) -> str:
    """Fetch the full readable text of a message by Message-ID (blocking)."""
    s = get_settings()
    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        mail.login(s.smtp_username, s.smtp_app_password)
        mail.select('INBOX', readonly=True)
        uid = _find_uid(mail, message_id)
        if not uid:
            return ''
        status, parts = mail.fetch(uid, '(BODY.PEEK[])')
        if status != 'OK' or not parts:
            return ''
        raw = b''.join(p[1] for p in parts if isinstance(p, tuple))
        msg = email.message_from_bytes(raw)
        texts: list[str] = []

        def walk(part):
            ct = part.get_content_type()
            if part.is_multipart():
                preferred = [p for p in part.get_payload() if p.get_content_type() == 'text/plain']
                for p in (preferred or part.get_payload()):
                    walk(p)
            elif ct.startswith('text/'):
                payload = part.get_payload(decode=True) or b''
                text = _snippet_from_bytes(payload, ct)
                if text:
                    texts.append(text)

        walk(msg)
        return '\n\n'.join(texts)[:20000]
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


def imap_set_read(message_id: str, read: bool) -> bool:
    s = get_settings()
    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        mail.login(s.smtp_username, s.smtp_app_password)
        mail.select('INBOX')
        uid = _find_uid(mail, message_id)
        if not uid:
            return False
        flag = '+FLAGS' if read else '-FLAGS'
        mail.store(uid, f'{flag} (\\Seen)')
        return True
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


def imap_trash(message_id: str) -> bool:
    """Move a message to Gmail trash."""
    s = get_settings()
    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        mail.login(s.smtp_username, s.smtp_app_password)
        typ, _ = mail.select('INBOX')
        uid = _find_uid(mail, message_id)
        if not uid:
            return False
        if typ == 'OK':
            try:
                mail.uid('MOVE', uid.decode(), '[Gmail]/Trash')
                return True
            except Exception:  # noqa: BLE001
                pass
        mail.copy(uid, '[Gmail]/Trash')
        mail.store(uid, '+FLAGS (\\Deleted)')
        mail.expunge()
        return True
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


async def sync_inbox(db: Database, limit: int = 60) -> dict:
    messages = await asyncio.to_thread(fetch_recent, limit)
    new = 0
    for msg in messages:
        msg['lead_id'] = await db.match_lead_by_email(msg.get('from_email'))
        if await db.upsert_inbox_message(msg):
            new += 1
    total = await db.inbox_count()
    return {'fetched': len(messages), 'new': new, 'total': total}
