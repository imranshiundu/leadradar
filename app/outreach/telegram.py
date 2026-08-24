from __future__ import annotations

import html
import httpx
from app.config import get_settings
from app.db import Database
from app.models import LeadStatus
from app.safety import fingerprint, normalize_text
from app.models import LeadCreate
from app.ai import classify_lead


def configured() -> bool:
    settings = get_settings()
    return bool(settings.telegram_bot_token and settings.telegram_owner_chat_id)


async def send_telegram_message(text: str, reply_markup: dict | None = None, chat_id: str | None = None) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return False
    target_chat = chat_id or settings.telegram_owner_chat_id
    if not target_chat:
        return False
    url = f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage'
    payload = {'chat_id': target_chat, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return True


def lead_alert_text(lead: dict) -> str:
    name = html.escape(str(lead.get('name') or 'Unknown'))
    score = int(lead.get('need_score') or 0)
    category = html.escape(str(lead.get('opportunity_type') or 'lead'))
    source = html.escape(str(lead.get('source_url') or lead.get('source') or ''))
    reason = html.escape(str(lead.get('ai_reason') or 'No reason'))[:900]
    summary = html.escape(str(lead.get('ai_summary') or ''))[:900]
    return (
        f'<b>LeadRadarSafe alert</b>\n'
        f'<b>{name}</b>\n'
        f'Score: <b>{score}/100</b> | Type: {category}\n\n'
        f'{summary}\n\n'
        f'<b>Reason:</b> {reason}\n\n'
        f'<b>Source:</b> {source}'
    )


def action_keyboard(lead_id: int) -> dict:
    return {
        'inline_keyboard': [
            [
                {'text': 'Approve', 'callback_data': f'approve:{lead_id}'},
                {'text': 'Reject', 'callback_data': f'reject:{lead_id}'},
            ],
            [
                {'text': 'Send approved email', 'callback_data': f'send:{lead_id}'},
            ],
        ]
    }


async def alert_lead(lead: dict) -> None:
    if not configured():
        return
    await send_telegram_message(lead_alert_text(lead), reply_markup=action_keyboard(int(lead['id'])))


async def answer_callback(callback_query_id: str, text: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f'https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery',
            json={'callback_query_id': callback_query_id, 'text': text},
        )


async def process_updates(db: Database) -> int:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_polling_enabled:
        return 0
    offset = await db.get_state('telegram_update_offset', 0)
    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.get(
            f'https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates',
            params={'offset': offset, 'timeout': 5, 'allowed_updates': ['message', 'callback_query']},
        )
        resp.raise_for_status()
        payload = resp.json()
    processed = 0
    allowed = settings.allowed_chat_ids()
    for update in payload.get('result', []):
        processed += 1
        await db.set_state('telegram_update_offset', int(update['update_id']) + 1)
        if 'callback_query' in update:
            cb = update['callback_query']
            chat_id = str(cb.get('message', {}).get('chat', {}).get('id', ''))
            if allowed and chat_id not in allowed and chat_id != str(settings.telegram_owner_chat_id):
                continue
            data = cb.get('data', '')
            try:
                action, lead_id_text = data.split(':', 1)
                lead_id = int(lead_id_text)
            except Exception:
                continue
            if action == 'approve':
                await db.set_status(lead_id, LeadStatus.APPROVED, 'approved in Telegram')
                await answer_callback(cb['id'], 'Approved')
            elif action == 'reject':
                await db.set_status(lead_id, LeadStatus.REJECTED, 'rejected in Telegram')
                await answer_callback(cb['id'], 'Rejected')
            elif action == 'send':
                # Sending is handled by the email queue. This only marks the lead approved.
                await db.set_status(lead_id, LeadStatus.APPROVED, 'approved for email send in Telegram')
                await answer_callback(cb['id'], 'Approved for email queue')
            continue

        msg = update.get('message') or {}
        chat_id = str(msg.get('chat', {}).get('id', ''))
        if allowed and chat_id not in allowed:
            continue
        text = normalize_text(msg.get('text') or msg.get('caption') or '')
        if not text or len(text) < 15:
            continue
        username = msg.get('chat', {}).get('title') or msg.get('from', {}).get('username') or f'telegram:{chat_id}'
        lead = LeadCreate(
            source='telegram:allowed_chat',
            source_url=f'telegram-chat:{chat_id}',
            name=str(username)[:180],
            opportunity_type='service_opportunity',
            raw_text=text[:5000],
            fingerprint=fingerprint('telegram', chat_id, text[:240]),
        )
        lead_id, inserted = await db.insert_lead(lead)
        if inserted and lead_id:
            classification = await classify_lead(lead.model_dump())
            await db.update_classification(lead_id, classification.score, classification.summary, classification.reason, classification.draft_message)
    return processed
