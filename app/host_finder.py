"""Find Taptap hosts (event organizers) at scale.

Two sources, both funneled through the same dedupe gate:
1. Gmail mining — scan INBOX + Sent headers for organizer-looking addresses.
2. Web discovery — DuckDuckGo HTML search + page crawl with keyword scoring.

Nothing here sends email; it only creates leads for the Outbox flow.
"""
from __future__ import annotations

import asyncio
import imaplib
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

from app.config import get_settings
from app.db import Database

_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_BAD_EXT = re.compile(r'\.(png|jpe?g|gif|webp|svg|css|js|ico)$', re.I)
_ORG_HINTS = re.compile(
    r'event|planner|organiz|host|mce|mc\b|entertain|sound|decor|cater|photograph|'
    r'ticket|festival|concert|conference|wedding|party|expo|venue|bashes|raves', re.I)
_KE_TLD = re.compile(r'\.co\.ke$|\.or\.ke$|\.ne\.ke$', re.I)
_ROLE_OK = re.compile(r'^(info|hello|hi|contact|bookings?|events?|team|admin|office|sales|support|careers?)@', re.I)

_DDGS = [
    '"event organizers" in nairobi "@gmail.com"',
    'event planners kenya contact "info@"',
    '"event planning" company kenya "@gmail.com" OR "@co.ke"',
    'kenya events company bookings email "@co.ke"',
    'nairobi party & events organisers contact us',
    'kenya wedding planners email contact',
    'mombasa event organizers contact email',
    'corporate events kenya "bookings@" OR "events@"',
]

state = {'running': False, 'phase': '', 'found': 0, 'scanned': 0, 'target': 0,
         'started_at': None, 'done': False, 'sources': {}}


def status() -> dict:
    return {**state, 'running': bool(state['running'])}


def extract_emails(text: str) -> list[str]:
    seen, out = set(), []
    for m in _EMAIL_RE.findall(text or ''):
        e = m.strip('.').lower()
        if _BAD_EXT.search(e) or len(e) > 80:
            continue
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def score_email(email_addr: str, context: str = '') -> tuple[int, str]:
    """Return (priority, note). Higher score -> higher priority."""
    score = 0
    if _KE_TLD.search(email_addr):
        score += 2
    if _ROLE_OK.match(email_addr):
        score += 1
    if _ORG_HINTS.search(context or ''):
        score += 3
    if any(d in email_addr for d in ('event', 'planner', 'party', 'fest', 'bash')):
        score += 3
    priority = 'high' if score >= 4 else ('medium' if score >= 2 else 'low')
    return score, priority


async def mine_gmail(db: Database, existing: set[str], max_headers: int = 1500) -> int:
    """Pull sender addresses from INBOX and Sent Mail."""
    s = get_settings()
    loop = asyncio.get_running_loop()

    def work() -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        mail = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
        try:
            mail.login(s.smtp_username, s.smtp_app_password)
            for folder in ['INBOX', '[Gmail]/Sent Mail']:
                try:
                    mail.select(folder, readonly=True)
                except Exception:  # noqa: BLE001
                    continue
                st, data = mail.search(None, 'ALL')
                if st != 'OK':
                    continue
                ids = data[0].split()
                for num in ids[-max_headers:]:
                    try:
                        _, parts = mail.fetch(num, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
                        for p in parts:
                            if isinstance(p, tuple):
                                raw = p[1].decode('utf-8', errors='ignore')
                                for e in extract_emails(raw):
                                    pairs.append((e, folder))
                    except Exception:  # noqa: BLE001
                        continue
        finally:
            try:
                mail.logout()
            except Exception:  # noqa: BLE001
                pass
        return pairs

    rows = await loop.run_in_executor(None, work)
    added = await insert_hosts(db, existing, [(e, f'gmail:{f}') for e, f in rows])
    state['scanned'] += len(rows)
    return added


async def web_find(db: Database, existing: set[str], per_query_pages: int = 8,
                   overall_budget_s: int = 240) -> int:
    """DuckDuckGo HTML search -> fetch pages -> harvest emails near org keywords."""
    started = time.monotonic()
    added_total = 0
    headers = {'User-Agent': get_settings().user_agent}
    async with httpx.AsyncClient(headers=headers, timeout=15, follow_redirects=True) as cx:
        for q in _DDGS:
            if state['found'] >= state['target'] or time.monotonic() - started > overall_budget_s:
                break
            state['phase'] = f'web: {q[:38]}'
            try:
                r = await cx.get('https://html.duckduckgo.com/html/', params={'q': q})
                links = re.findall(r'href="(https?://[^"]+)"[^>]*class="result__a"', r.text)
                if not links:
                    links = re.findall(r'result__url"[^>]*>\s*(https?://\S+)', r.text)
                seen_links = []
                for u in links:
                    clean = u.split('uddg=')[-1]
                    if clean.startswith('http') and clean not in seen_links:
                        seen_links.append(clean.split('&rut=')[0])
                for url in seen_links[:per_query_pages]:
                    if state['found'] >= state['target']:
                        break
                    try:
                        pr = await cx.get(url)
                        body = pr.text
                        emails = extract_emails(re.sub(r'<[^>]+>', ' ', body))
                        ctx = body[:4000]
                        pairs = []
                        for e in emails:
                            sc, _ = score_email(e, ctx + ' ' + str(_DDGS))
                            if sc >= 2:
                                pairs.append((e, f'web:{url[:60]}'))
                        added_total += await insert_hosts(db, existing, pairs)
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                continue
    return added_total


async def insert_hosts(db: Database, existing: set[str], pairs: list[tuple[str, str]]) -> int:
    from app.models import LeadCreate
    from app.safety import fingerprint as make_fp
    added = 0
    for email_addr, source in pairs:
        if state['found'] >= state['target']:
            break
        e = email_addr.lower()
        if e in existing:
            continue
        existing.add(e)
        sc, priority = score_email(e, source + ' ' + e)
        name_guess = e.split('@')[0].replace('.', ' ').replace('_', ' ')[:60].title()
        lead = LeadCreate(
            source='host_discovery',
            source_url=None,
            name=name_guess,
            email=e,
            opportunity_type='event_organizer',
            raw_text=f'auto-discovered via {source}',
            fingerprint=make_fp(e),
        )
        try:
            new_id, created = await db.insert_lead(lead)
            if created:
                from app.db import utc_now
                await db.execute_raw(
                    'UPDATE leads SET need_score=?, priority=? WHERE id=?',
                    (min(100, sc * 12), priority, new_id))
                added += 1
                state['found'] += 1
        except Exception:  # noqa: BLE001
            continue
    return added


async def run(db: Database, target: int = 300) -> None:
    if state['running']:
        return
    state.update(running=True, phase='starting', found=0, scanned=0, target=target,
                 done=False, started_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))

    rows = await db.list_leads(limit=10000)
    existing = {(dict(r).get('email') or '').lower() for r in rows}
    current_leads = len(rows)
    state['target'] = max(target - current_leads, 0)
    if state['target'] == 0:
        state.update(phase='already at target', running=False, done=True)
        return

    try:
        state['phase'] = 'mining gmail'
        n = await mine_gmail(db, existing)
        state['sources']['gmail'] = n
        if state['found'] < state['target']:
            n = await web_find(db, existing)
            state['sources']['web'] = n
        state['phase'] = 'done'
    except Exception as exc:  # noqa: BLE001
        state['phase'] = f'error: {exc}'
    finally:
        total = await db.count_leads()
        state['leads_total'] = total
        state['running'] = False
        state['done'] = True
