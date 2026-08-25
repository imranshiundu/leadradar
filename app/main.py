from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import campaigns as campaign_engine
from app import inbox as inbox_module
from app import auth
from app import deepverify
from app import host_finder
from app import inbox_sync
from app.config import get_settings
from app.db import Database
from app.importer import extract_event_intel, parse_contacts
from app.models import AppHealth, LeadStatus
from app.scheduler import build_scheduler
from app.outreach.emailer import configured as email_configured
from app.outreach.emailer import build_subject, build_body, send_email_with_id
from app.outreach.telegram import configured as telegram_configured

settings = get_settings()
db = Database(settings.database_path)
templates = Jinja2Templates(directory='app/web')
scheduler = None

PIPELINE_STAGES = ['new', 'contacted', 'replied', 'meeting', 'won', 'lost']


async def require_token(request: Request) -> None:
    """Shared-secret guard for mutating routes when DASHBOARD_TOKEN is set."""
    if not settings.dashboard_token:
        return
    supplied = request.headers.get('X-Dashboard-Token') or ''
    if not supplied:
        try:
            body = await request.body()
            if body:
                data = json.loads(body)
                supplied = data.get('token', '')
        except Exception:
            pass
    if not supplied:
        ct = request.headers.get('content-type', '')
        if 'multipart' in ct or 'form' in ct:
            form = await request.form()
            supplied = form.get('token', '')
    if supplied != settings.dashboard_token:
        raise HTTPException(401, 'Invalid or missing dashboard token')


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    await db.init()
    await auth.ensure_admin(db)
    scheduler = build_scheduler(db)
    scheduler.start()
    await db.add_event('app:start', {'message': 'LeadRadarSafe started'})
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await db.add_event('app:stop', {'message': 'LeadRadarSafe stopped'})


class SessionGuardMiddleware:
    """Session auth for /api/* routes (skips /api/auth/*). Dashboard token still accepted."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and settings.auth_enabled and scope['path'].startswith('/api/') \
                and not scope['path'].startswith('/api/auth/'):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
            token = headers.get('x-session-token', '')
            session = await db.get_session(token)
            dash_ok = settings.dashboard_token and headers.get('x-dashboard-token') == settings.dashboard_token
            if not session and not dash_ok:
                response = JSONResponse({'detail': 'Not authenticated'}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = FastAPI(title=settings.brand_name, version='1.0.0', lifespan=lifespan)
app.add_middleware(SessionGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")


@app.get('/health')
async def health():
    return {
        'status': 'ok',
        'db': settings.database_path,
        'telegram_configured': telegram_configured(),
        'smtp_configured': email_configured(),
        'groq_configured': bool(settings.groq_api_key),
        'search_configured': bool(settings.brave_search_api_key),
        'auth_required': bool(settings.auth_enabled),
        'cooldown_days': settings.contact_cooldown_days,
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.post('/api/auth/login')
async def api_auth_login(request: Request):
    body = await request.json()
    result = await auth.login(db, (body.get('email') or '').strip(), body.get('password') or '')
    if not result:
        raise HTTPException(401, 'Wrong email or password')
    await db.add_event('auth:login', {'email': result['email']})
    return {'ok': True, **result}


@app.post('/api/auth/logout')
async def api_auth_logout(request: Request):
    token = request.headers.get('x-session-token', '')
    if token:
        await db.delete_session(token)
    return {'ok': True}


@app.get('/api/auth/me')
async def api_auth_me(request: Request):
    session = await db.get_session(request.headers.get('x-session-token', ''))
    if not session:
        raise HTTPException(401, 'Not authenticated')
    return {'email': session['email'], 'expires': session['expires_at']}


@app.post('/api/auth/forgot')
async def api_auth_forgot(request: Request):
    body = await request.json()
    email_addr = (body.get('email') or '').strip()
    try:
        code = await auth.send_recovery_draft(db, email_addr)
        await db.add_event('auth:otp_created', {'email': email_addr})
        return {'ok': True, 'message': f'Recovery draft saved in {email_addr} — open Gmail drafts for the 6-digit code.', 'code_hint': code[:1] + '•••••'}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@app.post('/api/auth/reset')
async def api_auth_reset(request: Request):
    body = await request.json()
    email_addr = (body.get('email') or '').strip()
    new_password = body.get('new_password') or ''
    if len(new_password) < 6:
        raise HTTPException(400, 'New password must be at least 6 characters')
    ok = await auth.reset_password(db, email_addr, (body.get('otp') or '').strip(), new_password)
    if not ok:
        raise HTTPException(400, 'Invalid or expired recovery code')
    await db.add_event('auth:password_reset', {'email': email_addr})
    return {'ok': True, 'message': 'Password updated — sign in with your new password.'}


# ---------------------------------------------------------------------------
# Inbox mirror
# ---------------------------------------------------------------------------


@app.post('/api/inbox/sync')
async def api_inbox_sync():
    try:
        result = await inbox_sync.sync_inbox(db, limit=60)
        return {'ok': True, **result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f'IMAP sync failed: {exc}')


@app.get('/api/inbox')
async def api_inbox_list(limit: int = 100):
    messages = await db.list_inbox(limit=min(limit, 300))
    return {'messages': messages}


@app.get('/api/inbox/{msg_id}')
async def api_inbox_detail(msg_id: int):
    msg = await db.get_inbox_message(msg_id)
    if not msg:
        raise HTTPException(404, 'Message not found')
    if not msg.get('body'):
        try:
            body = await asyncio.to_thread(inbox_sync.fetch_full_body, msg['message_id'])
            if body:
                await db.set_inbox_body(msg_id, body)
                msg['body'] = body
        except Exception:  # noqa: BLE001
            pass
    return {'message': msg}


@app.post('/api/inbox/{msg_id}/flags')
async def api_inbox_flags(msg_id: int, request: Request):
    body = await request.json()
    msg = await db.get_inbox_message(msg_id)
    if not msg:
        raise HTTPException(404, 'Message not found')
    read = body.get('read')
    starred = body.get('starred')
    if read is not None:
        await db.set_inbox_flag(msg_id, 'is_read', 1 if read else 0)
        try:
            await asyncio.to_thread(inbox_sync.imap_set_read, msg['message_id'], bool(read))
        except Exception:  # noqa: BLE001
            pass
    if starred is not None:
        await db.set_inbox_flag(msg_id, 'starred', 1 if starred else 0)
    return {'ok': True}


@app.post('/api/inbox/read-all')
async def api_inbox_read_all():
    n = await db.mark_all_inbox_read()
    return {'ok': True, 'marked': n}


@app.post('/api/inbox/{msg_id}/trash')
async def api_inbox_trash(msg_id: int):
    msg = await db.get_inbox_message(msg_id)
    if not msg:
        raise HTTPException(404, 'Message not found')
    try:
        await asyncio.to_thread(inbox_sync.imap_trash, msg['message_id'])
    except Exception:  # noqa: BLE001
        pass
    await db.delete_inbox_message(msg_id)
    return {'ok': True}


@app.get('/api/notifications')
async def api_notifications():
    unread = await db.unread_inbox_count()
    recent_mail = [m for m in await db.list_inbox(limit=30) if not m.get('is_read')][:8]
    interested = [r for r in (await db.list_replies(limit=50)) if r['keyword'] == 'interested'][:5]
    return {
        'unread_inbox': unread,
        'items': [
            {'type': 'mail', 'title': m.get('from_name') or m.get('from_email'),
             'detail': m.get('subject'), 'at': m.get('date_utc'),
             'lead_id': m.get('lead_id'), 'inbox_id': m.get('id')}
            for m in recent_mail
        ] + [
            {'type': 'interested', 'title': 'Interested reply',
             'detail': r.get('from_email') or r.get('to_email'), 'at': r.get('received_at') or r.get('sent_at')}
            for r in interested
        ],
    }


@app.post('/api/drafts/compose')
async def api_compose_to_gmail(request: Request):
    """Save a composed email into the admin's Gmail Drafts folder."""
    body = await request.json()
    to_addr = (body.get('to') or '').strip()
    subject = (body.get('subject') or '(no subject)').strip()
    text = body.get('body') or ''
    if not to_addr:
        raise HTTPException(400, 'Recipient required')
    try:
        await asyncio.to_thread(auth.append_draft, to_addr, subject, text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f'Could not save draft: {exc}')
    await db.add_event('draft:composed', {'to': to_addr, 'subject': subject[:80]})
    return {'ok': True, 'message': 'Draft saved in Gmail'}


@app.get('/api/outreach-drafts')
async def api_outreach_drafts(status: str | None = None):
    drafts = await db.list_outreach_drafts(status=status)
    return {'drafts': drafts}


@app.post('/api/outreach-drafts/run-discovery')
async def api_run_discovery_drafts(request: Request):
    """Create outreach emails for qualified leads and drop them in Gmail Drafts.
    Never contacts an address twice within contact_cooldown_days."""
    body = await request.json() if await request.body() else {}
    limit = min(int(body.get('limit') or 10), 25)
    cooldown = settings.contact_cooldown_days

    created, skipped_cooldown, skipped_pending, skipped_other = 0, 0, 0, 0
    for row in await db.list_leads(limit=2000):
        if created >= limit:
            break
        lead = dict(row)
        email_addr = lead.get('email')
        if not email_addr:
            skipped_other += 1
            continue
        if lead.get('status') == 'opted_out' or await db.is_opted_out(email_addr):
            skipped_other += 1
            continue
        if await db.was_recently_contacted(email_addr, cooldown):
            skipped_cooldown += 1
            continue
        if await db.has_pending_draft_for(email_addr):
            skipped_pending += 1
            continue

        name = lead.get('name') or 'there'
        event = lead.get('event_name')
        subject = f'{name} — partnership on your next event' if event else f'{name} — quick question'
        draft_body = lead.get('draft_message') or (
            f"Hi {name} team,\n\n"
            "I'm Imran, founder of Taptap (https://taptap.africa) — QR code and wristband "
            "ticketing with built-in payments for events.\n\n"
            + (f"I saw you're behind {event}. " if event else '')
            + "We're onboarding a small group of organizers ahead of the next season and I'd "
              "love to show you how entry and payments look on Taptap.\n\n"
            "Worth a short call this week?\n\nImran\nhttps://taptap.africa")
        try:
            await asyncio.to_thread(auth.append_draft, email_addr, subject, draft_body)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f'Gmail draft failed at "{name}": {exc}')
        await db.create_outreach_draft(int(lead['id']), email_addr, subject, draft_body)
        await db.touch_contact(email_addr, 'draft')
        created += 1

    await db.add_event('discovery:drafted', {
        'created': created, 'skipped_cooldown': skipped_cooldown,
        'skipped_pending': skipped_pending})
    return {'ok': True, 'created': created, 'skipped_cooldown': skipped_cooldown,
            'skipped_pending': skipped_pending, 'cooldown_days': cooldown,
            'message': f'{created} outreach drafts saved in Gmail — review then approve here.'}


@app.post('/api/outreach-drafts/{draft_id}/approve')
async def api_approve_draft(draft_id: int):
    d = await db.get_outreach_draft(draft_id)
    if not d:
        raise HTTPException(404, 'Draft not found')
    if d['status'] != 'pending':
        raise HTTPException(400, f'Draft already {d["status"]}')
    from app.outreach.emailer import send_email_with_id
    mid = await send_email_with_id(d['to_email'], d['subject'], d['body'])
    await db.set_outreach_draft_status(draft_id, 'sent')
    await db.touch_contact(d['to_email'], 'sent')
    if d.get('lead_id'):
        await db.log_activity(int(d['lead_id']), None, 'outreach_sent', d['subject'][:100])
    await db.add_event('outreach:sent', {'draft_id': draft_id, 'to': d['to_email'], 'message_id': mid})
    return {'ok': True, 'message_id': mid}


@app.post('/api/outreach-drafts/{draft_id}/discard')
async def api_discard_draft(draft_id: int):
    d = await db.get_outreach_draft(draft_id)
    if not d:
        raise HTTPException(404, 'Draft not found')
    await db.set_outreach_draft_status(draft_id, 'discarded')
    return {'ok': True}


# ---------------------------------------------------------------------------
# Host discovery at scale + SMTP deep verification + BCC blast
# ---------------------------------------------------------------------------

_BCC_RECIPIENT_CAP = 450  # stay under Gmail's 500 To+Cc+Bcc limit per message


@app.post('/api/discovery/find-hosts')
async def api_find_hosts(request: Request):
    body = await request.json() if await request.body() else {}
    target = min(int(body.get('target') or 300), 2000)
    if host_finder.status()['running']:
        return {'ok': True, **host_finder.status()}
    asyncio.create_task(host_finder.run(db, target))
    await db.add_event('discovery:find_hosts', {'target': target})
    return {'ok': True, **host_finder.status()}


@app.post('/api/discovery/harvest')
async def api_discovery_harvest(request: Request):
    """Extract organizer emails from pasted text/HTML or crawled URLs.

    Powers both the paste box and the bookmarklet (which sends document text
    from the admin's own browser — bypasses datacenter-IP blocks entirely).
    """
    body = await request.json()
    text = body.get('text') or ''
    urls = [u for u in (body.get('urls') or []) if isinstance(u, str) and len(u) > 6][:20]
    source_url = (body.get('url') or 'paste')[:80]

    if not text and not urls:
        raise HTTPException(400, 'Provide text or urls')

    added = scanned = 0
    existing = {(dict(r).get('email') or '').lower() for r in await db.list_leads(limit=10000)}

    if text:
        result = await host_finder.harvest_text(db, existing, text, source_url)
        added += result['added']
        scanned += result['pages']

    if urls:
        result = await host_finder.harvest_urls(db, urls)
        added += result['added']
        scanned += result['scanned']

    return {'ok': True, 'added': added, 'scanned': scanned,
            'leads_total': await db.count_leads()}


@app.get('/api/discovery/status')
async def api_discovery_status():
    return {'ok': True, **host_finder.status()}


def _chunk_bcc(emails: list[str], cap: int = _BCC_RECIPIENT_CAP) -> list[list[str]]:
    return [emails[i:i + cap] for i in range(0, len(emails), cap)]


@app.post('/api/leads/rescore')
async def api_leads_rescore():
    """Recompute host-relevance for every lead. >=5 = confident host."""
    from app.relevance import relevance_score
    rows = [dict(r) for r in await db.list_leads(limit=10000)]
    dist = {0: 0}
    for r in rows:
        score = relevance_score(r.get('email'), r.get('name'), r.get('raw_text'),
                                r.get('website_url'), r.get('event_name'))
        await db.set_relevance(int(r['id']), score)
        dist[score] = dist.get(score, 0) + 1
    hosts = sum(n for s, n in dist.items() if s >= 5)
    return {'ok': True, 'rescored': len(rows), 'confident_hosts': hosts, 'distribution': dist}


@app.post('/api/leads/purge-non-hosts')
async def api_purge_non_hosts():
    """Mark everything below relevance 5 as rejected/non-host (kept for audit)."""
    rows = [dict(r) for r in await db.list_leads(limit=10000)]
    purged = 0
    for r in rows:
        if (r.get('relevance') or 0) < 5 and r.get('status') not in ('sent',):
            await db.mark_irrelevant(int(r['id']))
            purged += 1
    counts = await db.count_by_status()
    return {'ok': True, 'purged': purged, 'remaining_by_status': counts}


@app.get('/api/outreach/bcc-report')
async def api_bcc_report():
    """Who received past BCC blasts, with their relevance classification now."""
    recipients = await db.recent_bcc_recipients()
    from app.relevance import relevance_score
    for r in recipients:
        r['relevance_now'] = relevance_score(r.get('email'), r.get('name'), '', '', '')
    non_hosts = [r for r in recipients if (r['relevance_now'] or 0) < 5]
    return {'ok': True, 'total_recipients': len(recipients),
            'non_hosts': len(non_hosts),
            'recipients': recipients[:500]}


@app.post('/api/outreach/bcc-draft')
async def api_bcc_draft(request: Request):
    """Build ONE Gmail draft: To taptapafrica@gmail.com, BCC verified event hosts.

    Two-step by design: call with confirm=false (default) for a preview of exactly
    who would receive it; only confirm=true writes a draft.
    """
    body = await request.json() if await request.body() else {}
    to_addr = (body.get('to') or 'taptapafrica@gmail.com').strip()
    subject = (body.get('subject') or '').strip()
    text = body.get('body') or ''
    require_smtp_valid = bool(body.get('require_smtp_valid', True))
    confirm = bool(body.get('confirm', False))
    max_hosts = min(int(body.get('max_hosts') or 450), _BCC_RECIPIENT_CAP)

    from app.relevance import is_blast_eligible, relevance_score

    rows = [dict(r) for r in await db.list_leads(limit=10000)]
    eligible: list[dict] = []
    stats = {'no_email': 0, 'opted_out': 0, 'cooldown_or_pending': 0,
             'smtp_failed': 0, 'not_relevant': 0}
    for lead in rows:
        e = (lead.get('email') or '').strip()
        if not e:
            stats['no_email'] += 1
            continue
        if lead.get('status') == 'opted_out' or lead.get('status') == 'rejected' \
                or await db.is_opted_out(e):
            stats['opted_out'] += 1
            continue
        if await db.has_pending_draft_for(e) or await db.was_recently_contacted(e, settings.contact_cooldown_days):
            stats['cooldown_or_pending'] += 1
            continue
        smtp_res = await db.get_smtp_result(e)
        if smtp_res and smtp_res['status'] == 'invalid':
            stats['smtp_failed'] += 1
            continue
        if require_smtp_valid:
            if not smtp_res:
                stats['smtp_failed'] += 1
                continue
            if smtp_res['status'] != 'valid':
                stats['smtp_failed'] += 1
                continue
        score = lead.get('relevance')
        if score is None:
            score = relevance_score(e, lead.get('name'), lead.get('raw_text'),
                                    lead.get('website_url'), lead.get('event_name'))
        if not is_blast_eligible(score):
            stats['not_relevant'] += 1
            continue
        lead['relevance'] = score
        eligible.append(lead)

    preview = {
        'eligible': len(eligible), **stats,
        'sample': [{'email': l['email'], 'name': l['name'],
                    'relevance': l['relevance']} for l in eligible[:20]],
    }
    if not confirm:
        return {'ok': True, 'preview': True, **preview}

    if not eligible:
        raise HTTPException(400, 'No eligible hosts after relevance filtering.')

    if not subject or not text:
        camps = await db.list_campaigns()
        subj_tpl = (dict(camps[0])['subject_template'] if camps else 'Taptap — event ticketing & payments')
        body_tpl = (dict(camps[0])['body_template'] if camps else '')
        subject = re.sub(r'\{\{\s*\w+\s*\}\}', '', subj_tpl).strip()[:180] if not subject else subject
        text = re.sub(r'\{\{\s*\w+\s*\}\}', 'there', body_tpl) if not text else text

    emails = [l['email'] for l in eligible][:max_hosts]
    chunks = _chunk_bcc(emails)
    results = []
    for i, chunk in enumerate(chunks, 1):
        subj = subject + (f' ({i}/{len(chunks)})' if len(chunks) > 1 else '')
        try:
            await asyncio.to_thread(auth.append_draft_with_bcc, to_addr, subj, text, chunk)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f'Gmail draft failed for chunk {i}: {exc}')
        await db.create_outreach_draft(None, to_addr, subj,
                                       text + f'\n\n[BCC x{len(chunk)}]', channel='bcc_blast')
        results.append({'chunk': i, 'bcc_count': len(chunk), 'subject': subj})
        for e in chunk:
            await db.touch_contact(e, 'draft')

    await db.add_event('outreach:bcc_draft', {
        'to': to_addr, 'chunks': len(results), 'total_bcc': sum(r['bcc_count'] for r in results)})
    return {'ok': True, 'to': to_addr, 'eligible': len(eligible), **stats,
            'chunks': results, 'message':
            f'{len(results)} Gmail draft(s) created — {sum(r["bcc_count"] for r in results)} verified hosts BCC\'d.'}


@app.post('/api/verify-smtp-batch')
async def api_verify_smtp_batch(request: Request):
    """SMTP-probe the next batch of unverified lead addresses."""
    body = await request.json() if await request.body() else {}
    batch = min(int(body.get('batch_size') or 20), 50)

    rows = [dict(r) for r in await db.list_leads(limit=10000)]
    todo = []
    checked_set = set()
    for r in await db.all_smtp_checks():
        checked_set.add(r['email'])
    for lead in rows:
        e = (lead.get('email') or '').strip().lower()
        if not e or e in checked_set:
            continue
        todo.append(e)
        if len(todo) >= batch:
            break

    results = {k: 0 for k in ('valid', 'catch_all', 'invalid', 'risky', 'unreachable')}
    sem = asyncio.Semaphore(8)

    async def probe(e: str):
        async with sem:
            res = await asyncio.to_thread(deepverify.smtp_probe, e)
            results[res['status']] = results.get(res['status'], 0) + 1
            await db.set_smtp_result(e, res['status'])

    if todo:
        await asyncio.gather(*(probe(e) for e in todo))

    remaining = len(todo) - len(todo)  # computed after gather via stats
    stats = await db.smtp_stats()
    stats['remaining'] = stats['emails_with_address'] - stats['checked']
    stats['probed_this_run'] = len(todo)
    return {'ok': True, **stats, **{f'n_{k}': v for k, v in results.items()}}


@app.get('/api/verify-smtp-stats')
async def api_verify_smtp_stats():
    stats = await db.smtp_stats()
    stats['remaining'] = stats['emails_with_address'] - stats['checked']
    return {'ok': True, **stats}


@app.get('/', response_class=HTMLResponse)
async def home(request: Request, status: str | None = None):
    rows = await db.list_leads(status=status, limit=120)
    return templates.TemplateResponse('dashboard.html', {'request': request, 'leads': [dict(r) for r in rows], 'status': status})


@app.get('/api/leads')
async def api_leads(status: str | None = None, limit: int = 100):
    rows = await db.list_leads(status=status, limit=min(limit, 500))
    return {'leads': [dict(r) for r in rows]}


@app.post('/api/run/discovery')
async def api_run_discovery():
    results = await run_discovery_once(db)
    return {'results': [r.model_dump() for r in results]}


@app.get('/lead/{lead_id}', response_class=HTMLResponse)
async def lead_detail(request: Request, lead_id: int):
    row = await db.get_lead(lead_id)
    if not row:
        raise HTTPException(404, 'Lead not found')
    return templates.TemplateResponse('lead.html', {'request': request, 'lead': dict(row)})


@app.post('/lead/{lead_id}/approve')
async def approve_lead(lead_id: int):
    await db.set_status(lead_id, LeadStatus.APPROVED, 'approved from dashboard')
    return RedirectResponse(url=f'/lead/{lead_id}', status_code=303)


@app.post('/lead/{lead_id}/reject')
async def reject_lead(lead_id: int):
    await db.set_status(lead_id, LeadStatus.REJECTED, 'rejected from dashboard')
    return RedirectResponse(url='/', status_code=303)


@app.post('/lead/{lead_id}/send')
async def send_lead_email(lead_id: int):
    row = await db.get_lead(lead_id)
    if not row:
        raise HTTPException(404, 'Lead not found')
    lead = dict(row)
    if lead.get('status') != LeadStatus.APPROVED.value:
        raise HTTPException(400, 'Lead must be approved before sending')
    if not email_configured():
        raise HTTPException(400, 'SMTP is not configured')
    if not lead.get('email'):
        raise HTTPException(400, 'Lead has no email address')
    if await db.is_opted_out(lead.get('email')):
        raise HTTPException(400, 'Recipient opted out')
    await send_email(lead['email'], build_subject(lead), build_body(lead))
    await db.add_event('email:sent', {'to': lead['email'], 'subject': build_subject(lead)}, lead_id)
    await db.set_status(lead_id, LeadStatus.SENT, 'manual send from dashboard')
    return RedirectResponse(url=f'/lead/{lead_id}', status_code=303)


@app.post('/opt-out')
async def opt_out(email: str, reason: str | None = None):
    await db.add_opt_out(email, reason)
    return {'ok': True}


# ---------------------------------------------------------------------------
# Campaigns & sequences
# ---------------------------------------------------------------------------


@app.get('/campaigns', response_class=HTMLResponse)
async def campaigns_page(request: Request):
    rows = [dict(r) for r in await db.list_campaigns()]
    enriched = []
    for c in rows:
        c['analytics'] = await db.campaign_analytics(int(c['id']))
        enriched.append(c)
    return templates.TemplateResponse('campaigns.html', {'request': request, 'campaigns': enriched})


@app.get('/api/campaigns')
async def api_campaigns():
    out = []
    for row in await db.list_campaigns():
        campaign = dict(row)
        campaign['analytics'] = await db.campaign_analytics(int(campaign['id']))
        out.append(campaign)
    return {'campaigns': out}


@app.post('/api/campaigns')
async def api_create_campaign(
    name: str = Form(...),
    subject_template: str = Form(...),
    body_template: str = Form(...),
    daily_limit: int = Form(25),
):
    existing = await db.get_campaign_by_name(name)
    if existing:
        raise HTTPException(400, 'Campaign name already exists')
    cid = await db.create_campaign(name, subject_template, body_template, daily_limit=daily_limit)
    return {'ok': True, 'campaign_id': cid}


@app.get('/api/campaigns/{campaign_id}/steps')
async def api_list_steps(campaign_id: int):
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, 'Campaign not found')
    steps = [dict(r) for r in await db.list_sequence_steps(campaign_id)]
    return {'steps': steps}


@app.post('/api/campaigns/{campaign_id}/steps')
async def api_add_step(
    campaign_id: int,
    step_order: int = Form(...),
    delay_days: int = Form(3),
    body_template: str = Form(...),
    subject_template: str | None = Form(None),
):
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, 'Campaign not found')
    await db.add_sequence_step(campaign_id, step_order, delay_days, body_template, subject_template)
    return {'ok': True}


@app.post('/api/campaigns/{campaign_id}/attach')
async def api_attach_contacts(
    campaign_id: int,
    limit: int | None = Form(None),
    priority: str | None = Form(None),
):
    attached = await campaign_engine.attach_contacts(db, campaign_id, limit=limit, priority=priority)
    return {'ok': True, 'attached': attached}


@app.post('/api/campaigns/{campaign_id}/status')
async def api_campaign_status(campaign_id: int, status: str = Form(...)):
    if status not in ('draft', 'active', 'paused', 'done'):
        raise HTTPException(400, 'status must be draft|active|paused|done')
    await db.set_campaign_status(campaign_id, status)
    return {'ok': True}


@app.post('/api/campaigns/{campaign_id}/run-once')
async def api_run_campaign_once(campaign_id: int):
    """Process due sends right now (rate limits still apply)."""
    result = await campaign_engine.process_due_sends(db)
    return {'ok': True, **result}


# ---------------------------------------------------------------------------
# Contact import (emails.txt style lists, CSV, TSV, pipe tables)
# ---------------------------------------------------------------------------


@app.get('/import', response_class=HTMLResponse)
async def import_page(request: Request):
    return templates.TemplateResponse('import.html', {'request': request})


@app.post('/api/import')
async def api_import(request: Request, file: UploadFile | None = File(None), text: str | None = Form(None)):
    raw_text = text or ''
    if file is not None:
        raw_bytes = await file.read()
        raw_text = raw_bytes.decode('utf-8', errors='replace')
    if not raw_text.strip():
        raise HTTPException(400, 'Provide a file or text content')

    contacts = parse_contacts(raw_text)
    imported = duplicates = missing = 0
    from app.safety import fingerprint as make_fingerprint  # local import

    for contact in contacts:
        fingerprint = make_fingerprint(contact['email'])
        lead_row, created = await _upsert_contact(contact, fingerprint)
        if created:
            imported += 1
        else:
            duplicates += 1
        if not contact.get('email'):
            missing += 1

    await db.count_import_batch(file.filename if file else 'inline-text',
                                len(contacts), imported, duplicates, missing)
    await db.add_event('contacts:imported', {
        'total_rows': len(contacts), 'imported': imported,
        'duplicates': duplicates, 'missing_email': missing})
    return {'ok': True, 'parsed': len(contacts), 'imported': imported,
            'duplicates': duplicates, 'missing_email': missing}


async def _upsert_contact(contact: dict, fingerprint: str) -> tuple[int | None, bool]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    event_name = event_date = None
    async with db.connect() as conn:
        conn.row_factory = __import__('aiosqlite').Row
        cur = await conn.execute('SELECT id FROM leads WHERE fingerprint=?', (fingerprint,))
        existing = await cur.fetchone()
        if existing:
            return int(existing['id']), False
        cur = await conn.execute(
            '''INSERT INTO leads(source, name, email, phone, priority,
                                 opportunity_type, pipeline_stage, event_name, event_date,
                                 fingerprint, created_at, updated_at)
               VALUES ('manual_import', ?, ?, ?, ?, 'event_organizer', 'new', ?, ?, ?, ?, ?)''',
            (contact['name'], contact['email'], contact.get('phone'), contact.get('priority'),
             event_name, event_date, fingerprint, now, now),
        )
        await conn.commit()
        return int(cur.lastrowid), True


@app.post('/api/import/enrich-events')
async def api_enrich_events():
    """Run event-intel extraction over imported leads that lack it."""
    rows = await db.list_leads(limit=10000)
    enriched = 0
    for row in rows:
        lead = dict(row)
        if lead.get('event_name'):
            continue
        source_text = f"{lead.get('name') or ''} {lead.get('raw_text') or ''}"
        name, date_str = extract_event_intel(source_text)
        if name and date_str:
            await db.update_event_intel(int(lead['id']), name, date_str)
            enriched += 1
    return {'ok': True, 'enriched': enriched}


# ---------------------------------------------------------------------------
# Pipeline board + replies + analytics
# ---------------------------------------------------------------------------


@app.get('/pipeline', response_class=HTMLResponse)
async def pipeline_page(request: Request):
    rows = await db.list_leads(limit=1000)
    stages: dict[str, list[dict]] = {stage: [] for stage in PIPELINE_STAGES}
    for row in rows:
        lead = dict(row)
        stages.setdefault(lead.get('pipeline_stage') or 'new', []).append(lead)
    return templates.TemplateResponse('pipeline.html', {'request': request, 'stages': stages})


@app.post('/api/leads/{lead_id}/stage')
async def api_set_stage(lead_id: int, stage: str = Form(...)):
    if stage not in PIPELINE_STAGES:
        raise HTTPException(400, f'stage must be one of {PIPELINE_STAGES}')
    await db.set_pipeline_stage(lead_id, stage, 'set from dashboard')
    return RedirectResponse(url='/pipeline', status_code=303)


@app.get('/replies', response_class=HTMLResponse)
async def replies_page(request: Request):
    rows = [dict(r) for r in await db.list_replies(limit=200)]
    return templates.TemplateResponse('replies.html', {'request': request, 'replies': rows})


@app.post('/api/inbox/poll')
async def api_inbox_poll():
    async def tg_notify(text: str):  # optional Telegram alert on hot replies
        from app.outreach.telegram import send_telegram_message
        await send_telegram_message(text)

    try:
        result = await inbox_module.poll_replies(db, telegram_notify=tg_notify)
        return {'ok': True, **result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f'IMAP poll failed: {exc}')


@app.get('/api/replies')
async def api_replies():
    rows = [dict(r) for r in await db.list_replies(limit=200)]
    return {'replies': rows}


@app.get('/api/analytics')
async def api_analytics():
    campaigns = []
    for row in await db.list_campaigns():
        campaign = await db.campaign_analytics(int(row['id']))
        campaign['name'] = dict(row)['name']
        campaigns.append(campaign)
    total_leads = len(await db.list_leads(limit=10000))
    total_sent = sum(c.get('messages_sent', 0) for c in campaigns)
    total_replies = sum(c.get('stopped_replied', 0) for c in campaigns)
    total_interested = sum(c.get('reply_breakdown', {}).get('interested', 0) for c in campaigns)
    return {
        'campaigns': campaigns,
        'daily_sends': await db.daily_send_stats(14),
        'total_leads': total_leads,
        'total_sent': total_sent,
        'total_replies': total_replies,
        'total_interested': total_interested,
    }


@app.get('/analytics', response_class=HTMLResponse)
async def analytics_page(request: Request):
    data = await api_analytics()
    return templates.TemplateResponse('analytics.html', {'request': request, 'data': data})


@app.get('/campaigns/new', response_class=HTMLResponse)
async def campaign_new_page(request: Request):
    return templates.TemplateResponse('campaign_new.html', {'request': request})


@app.post('/api/campaigns/create')
async def api_create_campaign_form(
    name: str = Form(...),
    subject_template: str = Form(...),
    body_template: str = Form(...),
    follow_up_1_body: str = Form(''),
    follow_up_1_days: int = Form(3),
    follow_up_2_body: str = Form(''),
    follow_up_2_days: int = Form(7),
):
    existing = await db.get_campaign_by_name(name)
    if existing:
        raise HTTPException(400, 'Campaign name already exists')
    cid = await db.create_campaign(name, subject_template, body_template)
    step = 1
    if follow_up_1_body.strip():
        await db.add_sequence_step(cid, step, follow_up_1_days, follow_up_1_body)
        step += 1
    if follow_up_2_body.strip():
        await db.add_sequence_step(cid, step, follow_up_2_days, follow_up_2_body)
    return RedirectResponse(url='/campaigns', status_code=303)


@app.post('/lead/{lead_id}/send-test')
async def send_test_to_self(lead_id: int):
    """Send the drafted message to the configured test address instead of the lead."""
    row = await db.get_lead(lead_id)
    if not row:
        raise HTTPException(404, 'Lead not found')
    lead = dict(row)
    target = settings.smtp_from_email or settings.smtp_username
    if not email_configured() or not target:
        raise HTTPException(400, 'SMTP is not configured')
    mid = await send_email_with_id(target, f'[TEST] {build_subject(lead)}', build_body(lead))
    await db.add_event('email:test_sent', {'to': target}, lead_id)
    return {'ok': True, 'to': target, 'message_id': mid}


# ---------------------------------------------------------------------------
# Email threads
# ---------------------------------------------------------------------------


@app.get('/api/threads')
async def api_threads(lead_id: int | None = None):
    threads = [dict(r) for r in await db.list_threads(lead_id=lead_id)]
    return {'threads': threads}


# ---------------------------------------------------------------------------
# Lead notes
# ---------------------------------------------------------------------------


@app.post('/api/leads/{lead_id}/notes')
async def api_add_note(lead_id: int, note: str = Form(...), category: str = Form('general')):
    nid = await db.add_note(lead_id, note, category)
    await db.log_activity(lead_id, None, 'note', note[:100])
    return {'ok': True, 'note_id': nid}


@app.get('/api/leads/{lead_id}/notes')
async def api_list_notes(lead_id: int):
    notes = [dict(r) for r in await db.list_notes(lead_id)]
    return {'notes': notes}


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


@app.get('/api/leads/{lead_id}/activity')
async def api_activity(lead_id: int):
    activity = [dict(r) for r in await db.list_activity(lead_id)]
    return {'activity': activity}


# ---------------------------------------------------------------------------
# A/B testing
# ---------------------------------------------------------------------------


@app.post('/api/campaigns/{campaign_id}/ab-variants')
async def api_create_ab_variant(
    campaign_id: int,
    variant_name: str = Form(...),
    subject_template: str = Form(''),
    body_template: str = Form(...),
):
    vid = await db.create_ab_variant(campaign_id, variant_name, subject_template, body_template)
    return {'ok': True, 'variant_id': vid}


@app.get('/api/campaigns/{campaign_id}/ab-variants')
async def api_list_ab_variants(campaign_id: int):
    variants = [dict(r) for r in await db.list_ab_variants(campaign_id)]
    return {'variants': variants}


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


@app.post('/api/verify-email')
async def api_verify_email(email: str = Form(...)):
    from app.verification import verify_email
    result = verify_email(email)
    await db.cache_verification(email, result['status'], result['mx_valid'],
                                result['disposable'], result['free_provider'],
                                result['role_account'])
    return result


@app.post('/api/verify-batch')
async def api_verify_batch(request: Request):
    from app.verification import verify_email
    body = await request.json()
    emails = body.get('emails', [])
    results = []
    for email in emails[:100]:
        result = verify_email(email)
        await db.cache_verification(email, result['status'], result['mx_valid'],
                                    result['disposable'], result['free_provider'],
                                    result['role_account'])
        results.append({'email': email, **result})
    return {'results': results}


# ---------------------------------------------------------------------------
# Lead enrichment
# ---------------------------------------------------------------------------


@app.post('/api/leads/{lead_id}/enrich')
async def api_enrich_lead(lead_id: int):
    from app.enrichment import enrich_lead
    result = await enrich_lead(db, lead_id)
    return {'ok': True, 'extracted': result}


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


@app.post('/api/webhooks')
async def api_create_webhook(name: str = Form(...), url: str = Form(...),
                             events: str = Form('reply,interested')):
    wid = await db.add_webhook(name, url, events)
    return {'ok': True, 'webhook_id': wid}


@app.get('/api/webhooks')
async def api_list_webhooks():
    webhooks = [dict(r) for r in await db.list_webhooks(active_only=False)]
    return {'webhooks': webhooks}


# ---------------------------------------------------------------------------
# Send-time optimization
# ---------------------------------------------------------------------------


@app.get('/api/leads/{lead_id}/best-times')
async def api_best_send_times(lead_id: int):
    times = await db.best_send_times(lead_id)
    return {'best_times': times}


# ---------------------------------------------------------------------------
# Bulk verification for campaign
# ---------------------------------------------------------------------------


@app.post('/api/campaigns/{campaign_id}/verify')
async def api_verify_campaign(campaign_id: int):
    from app.verification import verify_email
    campaign = await db.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(404, 'Campaign not found')
    leads = await db.list_leads(limit=10000)
    results = {'valid': 0, 'risky': 0, 'invalid': 0, 'flagged': 0}
    for row in leads:
        lead = dict(row)
        email = lead.get('email')
        if not email:
            continue
        cached = await db.get_verification(email)
        if cached:
            status = dict(cached)['status']
        else:
            result = verify_email(email)
            status = result['status']
            await db.cache_verification(email, status, result['mx_valid'],
                                        result['disposable'], result['free_provider'],
                                        result['role_account'])
        results[status] = results.get(status, 0) + 1
    return results
