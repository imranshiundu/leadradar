from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import campaigns as campaign_engine
from app import inbox as inbox_module
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
    supplied = request.headers.get('X-Dashboard-Token') or \
        (await request.body() and json.loads(request.body() or b'{}').get('token')) or ''
    if supplied != settings.dashboard_token:
        raise HTTPException(401, 'Invalid or missing dashboard token')


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    await db.init()
    scheduler = build_scheduler(db)
    scheduler.start()
    await db.add_event('app:start', {'message': 'LeadRadarSafe started'})
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)
        await db.add_event('app:stop', {'message': 'LeadRadarSafe stopped'})


app = FastAPI(title='LeadRadarSafe', version='1.0.0', lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")


@app.get('/health', response_model=AppHealth)
async def health():
    return AppHealth(
        status='ok',
        db=settings.database_path,
        telegram_configured=telegram_configured(),
        smtp_configured=email_configured(),
        groq_configured=bool(settings.groq_api_key),
        search_configured=bool(settings.brave_search_api_key),
    )


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


@app.post('/api/campaigns', dependencies=[Depends(require_token)])
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


@app.post('/api/campaigns/{campaign_id}/steps', dependencies=[Depends(require_token)])
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


@app.post('/api/campaigns/{campaign_id}/attach', dependencies=[Depends(require_token)])
async def api_attach_contacts(
    campaign_id: int,
    limit: int | None = Form(None),
    priority: str | None = Form(None),
):
    attached = await campaign_engine.attach_contacts(db, campaign_id, limit=limit, priority=priority)
    return {'ok': True, 'attached': attached}


@app.post('/api/campaigns/{campaign_id}/status', dependencies=[Depends(require_token)])
async def api_campaign_status(campaign_id: int, status: str = Form(...)):
    if status not in ('draft', 'active', 'paused', 'done'):
        raise HTTPException(400, 'status must be draft|active|paused|done')
    await db.set_campaign_status(campaign_id, status)
    return {'ok': True}


@app.post('/api/campaigns/{campaign_id}/run-once', dependencies=[Depends(require_token)])
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


@app.post('/api/import', dependencies=[Depends(require_token)])
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


@app.post('/api/import/enrich-events', dependencies=[Depends(require_token)])
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


@app.post('/api/leads/{lead_id}/stage', dependencies=[Depends(require_token)])
async def api_set_stage(lead_id: int, stage: str = Form(...)):
    if stage not in PIPELINE_STAGES:
        raise HTTPException(400, f'stage must be one of {PIPELINE_STAGES}')
    await db.set_pipeline_stage(lead_id, stage, 'set from dashboard')
    return RedirectResponse(url='/pipeline', status_code=303)


@app.get('/replies', response_class=HTMLResponse)
async def replies_page(request: Request):
    rows = [dict(r) for r in await db.list_replies(limit=200)]
    return templates.TemplateResponse('replies.html', {'request': request, 'replies': rows})


@app.post('/api/inbox/poll', dependencies=[Depends(require_token)])
async def api_inbox_poll():
    async def tg_notify(text: str):  # optional Telegram alert on hot replies
        from app.outreach.telegram import send_telegram_message
        await send_telegram_message(text)

    try:
        result = await inbox_module.poll_replies(db, telegram_notify=tg_notify)
        return {'ok': True, **result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f'IMAP poll failed: {exc}')


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


@app.post('/api/campaigns/create', dependencies=[Depends(require_token)])
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


@app.post('/lead/{lead_id}/send-test', dependencies=[Depends(require_token)])
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
