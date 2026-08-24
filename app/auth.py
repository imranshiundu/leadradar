from __future__ import annotations

import asyncio
import hashlib
import hmac
import imaplib
import secrets
import time
from email.message import EmailMessage
from email.utils import formatdate

from app.config import get_settings
from app.db import Database

_ITERATIONS = 120_000


def _hash(secret: str, salt: str = '') -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', secret.encode(), salt.encode(), _ITERATIONS).hex()
    return f'{salt}${digest}'


def verify_hash(secret: str, stored: str) -> bool:
    try:
        salt, _ = stored.split('$', 1)
    except ValueError:
        return False
    return hmac.compare_digest(_hash(secret, salt), stored)


def hash_password(pw: str) -> str:
    return _hash(pw)


async def ensure_admin(db: Database) -> None:
    s = get_settings()
    if await db.count_users() > 0:
        return
    password = s.admin_password or s.admin_email
    await db.create_user(s.admin_email, hash_password(password))


async def login(db: Database, email: str, password: str) -> dict | None:
    user = await db.get_user_by_email(email)
    if not user or not verify_hash(password, user['pass_hash']):
        await asyncio.sleep(0.6)
        return None
    token = secrets.token_hex(32)
    expires = await db.create_session(int(user['id']), token)
    return {'token': token, 'expires': expires, 'email': user['email']}


def generate_otp() -> tuple[str, str]:
    code = f'{secrets.randbelow(1_000_000):06d}'
    return code, _hash(code)


def append_draft(to_addr: str, subject: str, body: str) -> None:
    """Append a message to the Gmail Drafts folder via IMAP (blocking)."""
    s = get_settings()
    if not (s.smtp_username and s.smtp_app_password):
        raise RuntimeError('IMAP credentials are not configured')

    msg = EmailMessage()
    msg['From'] = s.smtp_username
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=False)
    msg.set_content(body)

    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        mail.login(s.smtp_username, s.smtp_app_password)
        mail.append('"[Gmail]/Drafts"', '(\\Draft)', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass


async def send_recovery_draft(db: Database, email: str) -> str:
    """Generate an OTP, store its hash, and drop the code into the admin's Gmail drafts."""
    user = await db.get_user_by_email(email)
    if not user:
        raise RuntimeError('No account with that email')
    code, otp_hash = generate_otp()
    await db.set_otp(email, otp_hash)
    subject = f'{s.brand_name} recovery code: {code}'
    body = (
        'Your LeadRadar recovery code is:\n\n'
        f'    {code}\n\n'
        'It expires in 15 minutes. Enter it on the recovery screen to set a new password.\n\n'
        'If you did not request this, ignore this draft — nothing was sent to anyone.'
    )
    await asyncio.to_thread(append_draft, email, subject, body)
    return code


async def reset_password(db: Database, email: str, otp: str, new_password: str) -> bool:
    if not await db.consume_otp(email, otp):
        return False
    user = await db.get_user_by_email(email)
    if not user:
        return False
    await db.update_password(int(user['id']), hash_password(new_password))
    return True


def append_draft_with_bcc(to_addr: str, subject: str, body: str, bcc: list[str]) -> None:
    """Append a message with a BCC list to the Gmail Drafts folder (blocking)."""
    s = get_settings()
    if not (s.smtp_username and s.smtp_app_password):
        raise RuntimeError('IMAP credentials are not configured')

    msg = EmailMessage()
    msg['From'] = s.smtp_username
    msg['To'] = to_addr
    msg['Bcc'] = ', '.join(bcc)
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=False)
    msg.set_content(body)

    mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        mail.login(s.smtp_username, s.smtp_app_password)
        mail.append('"[Gmail]/Drafts"', '(\\Draft)', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    finally:
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
