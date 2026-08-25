"""Find Taptap hosts (event organizers) at scale.

Three sources, all funneled through the same dedupe gate:
1. Gmail mining — scan INBOX + Sent headers for organizer-looking addresses.
2. Web discovery — DuckDuckGo HTML search + page crawl with keyword scoring.
3. CT subdomain discovery — crt.name Certificate Transparency index for target domains.

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
    '"event planners" nairobi "info@" contact',
    'kenya event organisers directory contacts',
    '"events company" nairobi bookings email .co.ke',
    'wedding planners kenya "email us"',
    'corporate events nairobi "bookings@" OR "events@"',
    'mombasa kisumu nakuru event organizers contact',
    'kenya party decorators sound hire events email',
    'eventbrite nairobi organizer',
    'kenya concerts festivals organizers contacts',
    'team building companies kenya contact email',
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

    from app.relevance import relevance_score
    rows = await loop.run_in_executor(None, work)
    gated = [(e, src) for e, src in ((e, f'gmail:{f}') for e, f in rows)
             if relevance_score(e) >= 4]
    added = await insert_hosts(db, existing, gated)
    state['scanned'] += len(rows)
    return added


async def _collect_links(cx: httpx.AsyncClient, q: str) -> list[str]:
    """Multi-engine search: Bing, Mojeek, DDG-lite. Returns deduped external URLs."""
    urls: list[str] = []
    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0',
               'Accept-Language': 'en-US,en;q=0.9'}
    try:
        r = await cx.get('https://www.bing.com/search', params={'q': q, 'count': 20}, headers=headers)
        for m in re.findall(r'<h2[^>]*><a[^>]+href="(https?://[^"]+)"', r.text):
            if 'bing.com' not in m and 'microsoft' not in m:
                urls.append(m.split('&')[0] if 'bing.com/ck/' not in m else m)
        # Bing redirect wrappers
        for m in re.findall(r'href="(https://www\.bing\.com/ck/a?!.*?)["<]', r.text):
            inner = re.search(r'u=a1(https?%3a%2f%2f[^&]+)', m)
            if inner:
                from urllib.parse import unquote
                urls.append(unquote(inner.group(1)))
    except Exception:  # noqa: BLE001
        pass
    try:
        r = await cx.get('https://www.mojeek.com/search', params={'q': q}, headers=headers)
        urls += [u for u in re.findall(r'<a class="ob" href="(https?://[^"]+)"', r.text)]
        urls += [u for u in re.findall(r'href="(https?://[^"]+)" class="title"', r.text)]
    except Exception:  # noqa: BLE001
        pass
    try:
        r = await cx.get('https://lite.duckduckgo.com/lite/', params={'q': q}, headers=headers)
        urls += [u for u in re.findall(r'href="(https?://[^"]+)"', r.text)
                 if 'duckduckgo' not in u]
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for u in urls:
        clean = u.rstrip('/')
        if clean.startswith('http') and clean not in seen and \
                not re.search(r'(google|bing|mojeek|duckduckgo|youtube|facebook|instagram|twitter|x)\.', clean):
            seen.add(clean)
            out.append(clean)
    return out


async def web_find(db: Database, existing: set[str], per_query_pages: int = 10,
                   overall_budget_s: int = 300) -> int:
    """Search-engine harvest -> fetch pages -> emails near org keywords."""
    started = time.monotonic()
    added_total = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as cx:
        for q in _DDGS:
            if state['found'] >= state['target'] or time.monotonic() - started > overall_budget_s:
                break
            state['phase'] = f'web: {q[:38]}'
            links = await _collect_links(cx, q)
            for url in links[:per_query_pages]:
                if state['found'] >= state['target']:
                    break
                try:
                    pr = await cx.get(url)
                    if pr.status_code != 200 or 'text/html' not in pr.headers.get('content-type', 'html'):
                        continue
                    body = pr.text
                    emails = extract_emails(re.sub(r'<[^>]+>', ' ', body))
                    ctx = body[:4000] + ' ' + url
                    pairs = []
                    for e in emails:
                        sc, _ = score_email(e, ctx)
                        if sc >= 3:
                            pairs.append((e, f'web:{url[:60]}'))
                    added_total += await insert_hosts(db, existing, pairs)
                except Exception:  # noqa: BLE001
                    continue
            await asyncio.sleep(2)
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
    active_leads = sum(1 for r in rows if dict(r).get('status') != 'rejected')
    state['target'] = max(target - active_leads, 0)
    if state['target'] == 0:
        state.update(phase='already at target', running=False, done=True)
        return

    try:
        state['phase'] = 'mining gmail'
        n = await mine_gmail(db, existing)
        state['sources']['gmail'] = n
        if state['found'] < state['target']:
            n = await crt_discover(db, existing)
            state['sources']['crt_name'] = n
        if state['found'] < state['target']:
            n = await web_find(db, existing)
            state['sources']['web'] = n
        if state['found'] < state['target']:
            n = await crawl_eventbrite(db, existing)
            state['sources']['eventbrite'] = n
        state['phase'] = 'done'
    except Exception as exc:  # noqa: BLE001
        state['phase'] = f'error: {exc}'
    finally:
        total = await db.count_leads()
        state['leads_total'] = total
        state['running'] = False
        state['done'] = True


async def crawl_eventbrite(db: Database, existing: set[str], max_pages: int = 8,
                           max_events: int = 120) -> int:
    """Crawl Eventbrite Kenya listings -> event pages -> organizer emails."""
    added = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'}) as cx:
        event_urls = []
        for city in ('kenya--nairobi', 'kenya--mombasa', 'kenya--kisumu'):
            for page in range(1, max_pages + 1):
                if len(event_urls) >= max_events or state['found'] >= state['target']:
                    break
                try:
                    r = await cx.get(f'https://www.eventbrite.com/d/{city}/events/',
                                     params={'page': page})
                    if r.status_code != 200:
                        break
                    found_links = re.findall(r'href="(https://www\.eventbrite\.com/e/[^"#?]+)', r.text)
                    for u in found_links:
                        if u not in event_urls:
                            event_urls.append(u)
                except Exception:  # noqa: BLE001
                    continue
        state['phase'] = f'eventbrite: {len(event_urls)} events'
        for url in event_urls[:max_events]:
            if state['found'] >= state['target']:
                break
            try:
                pr = await cx.get(url)
                if pr.status_code != 200:
                    continue
                body = pr.text
                # Event name for context
                title_m = re.search(r'<title>([^<]+)</title>', body)
                title = title_m.group(1) if title_m else ''
                emails = extract_emails(re.sub(r'<[^>]+>', ' ', body))
                pairs = []
                for e in emails:
                    sc, _ = score_email(e, title + ' event organizer tickets ' + url)
                    if sc >= 3:
                        pairs.append((e, f'eventbrite:{url.rsplit("/",2)[-2][:50]}'))
                before = state['found']
                added += await insert_hosts(db, existing, pairs)
                _ = before
                await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001
                continue
    return added


async def harvest_urls(db: Database, urls: list[str]) -> dict:
    """Extract organizer emails from arbitrary user-supplied pages."""
    existing = {(dict(r).get('email') or '').lower() for r in await db.list_leads(limit=10000)}
    state.update(target=9999)
    added, scanned = 0, 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'}) as cx:
        for url in urls:
            if not url.startswith('http'):
                url = 'https://' + url
            try:
                pr = await cx.get(url)
                body = re.sub(r'<[^>]+>', ' ', pr.text)
                pairs = []
                for e in extract_emails(body):
                    sc, _ = score_email(e, body[:4000] + ' ' + url)
                    if sc >= 3:
                        pairs.append((e, f'harvest:{url[:60]}'))
                added += await insert_hosts(db, existing, pairs)
                scanned += 1
            except Exception:  # noqa: BLE001
                continue
    return {'scanned': scanned, 'added': added}


async def harvest_text(db: Database, existing: set[str], text: str,
                       source_url: str = 'paste') -> dict:
    """Score+insert organizer emails found in raw page text (bookmarklet/paste)."""
    clean = re.sub(r'\s+', ' ', text)
    pairs = []
    for e in extract_emails(clean):
        sc, _ = score_email(e, clean[:3000] + ' ' + source_url)
        if sc >= 3:
            pairs.append((e, f'harvest:{source_url}'))
    added = await insert_hosts(db, existing, pairs)
    return {'added': added, 'pages': 1}


# ── crt.name Certificate Transparency discovery ──────────────────────

CRT_TARGET_DOMAINS = [
    'eventbrite.co.ke', 'eventbrite.com',
    'taptap.africa',
    'brightermonday.co.ke',
    'businesslist.co.ke',
    'nairobifunctionhalls.com',
    'eventpark.co.ke',
    'ticketbox.co.ke',
    'mkeja.co.ke',
    'proudkenyan.co.ke',
    'venuenai.com',
    'eventskenya.co.ke',
    'nairobidiaries.com',
    'kenyabuzz.com',
]


async def _crt_name_search(cx: httpx.AsyncClient, apex: str) -> list[str]:
    """Query crt.name for subdomains of an apex domain. Returns list of subdomains."""
    try:
        r = await cx.get(
            'https://crt.name/v1/search',
            params={'apex': apex, 'format': 'json'},
            headers={'Accept': 'application/json'},
        )
        if r.status_code != 200:
            return []
        data = r.json()
        names = []
        for item in (data if isinstance(data, list) else data.get('names', data.get('results', []))):
            if isinstance(item, dict):
                name = item.get('name', item.get('subdomain', ''))
            else:
                name = str(item)
            name = name.strip().lower()
            if name and name != apex and '*' not in name:
                names.append(name)
        return list(dict.fromkeys(names))
    except Exception:  # noqa: BLE001
        return []


async def _crt_name_search_text(cx: httpx.AsyncClient, apex: str) -> list[str]:
    """Fallback: query crt.name plain-text endpoint."""
    try:
        r = await cx.get(
            'https://crt.name/v1/search',
            params={'apex': apex},
            headers={'Accept': 'text/plain'},
        )
        if r.status_code != 200:
            return []
        names = []
        for line in r.text.strip().splitlines():
            name = line.strip().lower()
            if name and name != apex and '*' not in name:
                names.append(name)
        return list(dict.fromkeys(names))
    except Exception:  # noqa: BLE001
        return []


async def crt_discover(db: Database, existing: set[str],
                       max_subdomains_per_domain: int = 50) -> int:
    """Use crt.name CT index to discover subdomains of event platforms,
    then crawl those subdomains for organizer emails."""
    added = 0
    async with httpx.AsyncClient(
        timeout=15, follow_redirects=True,
        headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'}
    ) as cx:
        all_subdomains = []
        for apex in CRT_TARGET_DOMAINS:
            if state['found'] >= state['target']:
                break
            state['phase'] = f'crt.name: {apex}'
            subs = await _crt_name_search(cx, apex)
            if not subs:
                subs = await _crt_name_search_text(cx, apex)
            for s in subs[:max_subdomains_per_domain]:
                if s not in all_subdomains:
                    all_subdomains.append(s)

        state['phase'] = f'crt.name: crawling {len(all_subdomains)} subdomains'
        for sub in all_subdomains:
            if state['found'] >= state['target']:
                break
            for scheme in ('https', 'http'):
                if state['found'] >= state['target']:
                    break
                try:
                    pr = await cx.get(f'{scheme}://{sub}', timeout=10)
                    if pr.status_code < 400:
                        body = pr.text
                        emails = extract_emails(re.sub(r'<[^>]+>', ' ', body))
                        pairs = []
                        for e in emails:
                            sc, _ = score_email(e, body[:3000] + ' ' + sub)
                            if sc >= 3:
                                pairs.append((e, f'crt.name:{sub[:50]}'))
                        added += await insert_hosts(db, existing, pairs)
                        break
                except Exception:  # noqa: BLE001
                    continue
            await asyncio.sleep(0.3)
    return added
