from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.campaigns import attach_contacts, process_due_sends
from app.config import get_settings
from app.db import Database


def iso(dt):
    return dt.isoformat(timespec='seconds')


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / 'test.db'))
    await database.init()
    yield database


@pytest_asyncio.fixture(autouse=True)
def fast_sends():
    """Zero-out send delay so tests don't hang."""
    s = get_settings()
    original_delay = s.smtp_min_seconds_between_sends
    original_limit = s.smtp_daily_limit
    s.smtp_min_seconds_between_sends = 0
    s.smtp_daily_limit = 9999
    yield
    s.smtp_min_seconds_between_sends = original_delay
    s.smtp_daily_limit = original_limit


async def make_lead(db: Database, email: str, name: str = 'Acme Events', stage: str = 'new') -> int:
    from datetime import datetime as dt
    now = dt.now(timezone.utc).isoformat(timespec='seconds')
    async with db.connect() as conn:
        cur = await conn.execute(
            '''INSERT INTO leads(source, name, email, opportunity_type, pipeline_stage,
                                 fingerprint, created_at, updated_at)
               VALUES ('manual_import', ?, ?, 'event_organizer', ?, ?, ?, ?)''',
            (name, email, stage, f'fp-{email}', now, now),
        )
        await conn.commit()
        return int(cur.lastrowid)


@pytest.mark.asyncio
async def test_campaign_full_sequence(db):
    lead_id = await make_lead(db, 'one@example.com')
    cid = await db.create_campaign('Taptap Oct-Nov',
                                   'Taptap for {{name}}', 'Hi {{name}}, we help events…')
    assert cid > 0
    # follow-up after 3 days
    await db.add_sequence_step(cid, 1, delay_days=3,
                               body_template='Following up with {{name}}…')
    attached = await attach_contacts(db, cid)
    assert attached == 1

    sent_log = []

    async def fake_sender(to_email, subject, body):
        sent_log.append((to_email, subject, body))
        return f'<msg-{len(sent_log)}@example.com>'

    # Must activate the campaign before it sends
    await db.set_campaign_status(cid, 'active')

    summary = await process_due_sends(db, sender=fake_sender)
    assert summary['sent'] == 1
    assert len(sent_log) == 1
    assert 'Acme Events' in sent_log[0][2]

    lead = dict(await db.get_lead(lead_id))
    assert lead['status'] == 'sent'
    assert lead['pipeline_stage'] == 'contacted'

    cl_rows = await db.due_campaign_leads(cid, limit=10)
    assert not cl_rows  # next send is 3 days out

    # simulate time passing: force next_send_at into the past
    past = iso(datetime.now(timezone.utc) - timedelta(days=4))
    async with db.connect() as conn:
        await conn.execute('UPDATE campaign_leads SET next_send_at=?', (past,))
        await conn.commit()

    summary2 = await process_due_sends(db, sender=fake_sender)
    assert summary2['sent'] == 1
    assert len(sent_log) == 2
    assert 'Following up' in sent_log[1][2]

    # sequence finished — nothing more due
    cl_rows = await db.due_campaign_leads(cid, limit=10)
    assert not cl_rows


@pytest.mark.asyncio
async def test_reply_stops_sequence(db):
    lead_id = await make_lead(db, 'two@example.com')
    cid = await db.create_campaign('Camp2', 'Subj {{name}}', 'Body {{name}}')
    await db.attach_lead_to_campaign(cid, lead_id)

    stopped = await db.stop_campaign_leads_for_reply(lead_id)
    assert stopped == 1
    rows = await db.due_campaign_leads(cid)
    assert rows == []


@pytest.mark.asyncio
async def test_opt_out_skips_send(db):
    lead_id = await make_lead(db, 'three@example.com')
    await db.add_opt_out('three@example.com', 'asked to stop')
    cid = await db.create_campaign('Camp3', 'S', 'B')

    calls = []

    async def fake_sender(to_email, subject, body):
        calls.append(to_email)
        return '<x@y>'

    # attach_contacts must skip opted-out leads entirely
    attached = await attach_contacts(db, cid)
    assert attached == 0
    summary = await process_due_sends(db, sender=fake_sender)
    assert summary['sent'] == 0 and not calls


@pytest.mark.asyncio
async def test_message_matching_by_rfc_id(db):
    lead_id = await make_lead(db, 'four@example.com')
    await db.record_message(lead_id, None, 1, 'four@example.com', 'Hi', 'Body',
                            message_id='<abc123@example.com>')
    row = await db.find_message_by_rfc_ids(['<other@x.com>', '<abc123@example.com>'])
    assert row is not None and row['to_email'] == 'four@example.com'
