import pytest
from app.auth import hash_password, verify_hash, generate_otp
from app.db import Database


async def test_password_roundtrip(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    await db.init()
    h = hash_password('Imranshiundu@gmail.com')
    assert verify_hash('Imranshiundu@gmail.com', h)
    assert not verify_hash('wrong', h)


async def test_session_expiry(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    await db.init()
    uid = await db.create_user('imran@taptap.africa', hash_password('secret1'))
    token = 'tok123'
    await db.create_session(uid, token, days=30)
    assert (await db.get_session(token))['email'] == 'imran@taptap.africa'
    expired = 'tok456'
    await db.create_session(uid, expired, days=-1)
    assert await db.get_session(expired) is None


async def test_otp_flow(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    await db.init()
    code, code_hash = generate_otp()
    await db.set_otp('imran@taptap.africa', code_hash, minutes=15)
    wrong, wrong_hash = generate_otp()
    assert not await db.consume_otp('imran@taptap.africa', wrong)
    assert not await db.consume_otp('imran@taptap.africa', '000000')
    assert await db.consume_otp('imran@taptap.africa', code)
    assert not await db.consume_otp('imran@taptap.africa', code)  # single use


async def test_inbox_upsert_and_match(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    await db.init()
    lead_id, created = await db.insert_lead(_mk_lead())
    msg = {'message_id': '<a@b>', 'from_name': 'Bigmiitch', 'from_email': 'info@bigmiitchevents.co.ke',
           'subject': 'Re: Taptap', 'date_utc': '2026-08-24T10:00:00+00:00', 'snippet': 'we are interested'}
    assert await db.upsert_inbox_message(msg) is True
    assert await db.upsert_inbox_message(msg) is False  # dedupe
    matched = await db.match_lead_by_email('INFO@bigmiitchevents.co.ke')
    assert matched == lead_id
    rows = await db.list_inbox(10)
    assert rows[0]['subject'] == 'Re: Taptap'


def _mk_lead():
    from app.models import LeadCreate
    return LeadCreate(source='test', name='Bigmiitch Events', email='info@bigmiitchevents.co.ke',
                      fingerprint='fp-inbox-test-1')


async def test_cooldown_and_drafts(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    await db.init()
    assert not await db.was_recently_contacted('a@x.co', 30)
    await db.touch_contact('a@x.co', 'draft')
    assert await db.was_recently_contacted('A@X.CO', 30)
    assert not await db.was_recently_contacted('a@x.co', 0)

    lead_id, _ = await db.insert_lead(_mk_lead())
    did = await db.create_outreach_draft(lead_id, 'info@bigmiitchevents.co.ke', 'Sub', 'Body')
    assert await db.has_pending_draft_for('INFO@bigmiitchevents.co.ke')
    drafts = await db.list_outreach_drafts(status='pending')
    assert drafts[0]['lead_name'] == 'Bigmiitch Events'
    await db.set_outreach_draft_status(did, 'sent')
    assert not await db.has_pending_draft_for('info@bigmiitchevents.co.ke')


async def test_inbox_flags_and_body(tmp_path):
    db = Database(str(tmp_path / 't.db'))
    await db.init()
    msg = {'message_id': '<z@z>', 'from_email': 'a@b.c', 'subject': 'Re: Hello',
           'date_utc': '2026-08-24T10:00:00+00:00', 'snippet': 's', 'group_key': 'a@b.c|hello'}
    await db.upsert_inbox_message(msg)
    row = (await db.list_inbox(5))[0]
    assert row['group_key'] == 'a@b.c|hello' and row['is_read'] == 0
    await db.set_inbox_body(row['id'], 'full body text')
    got = await db.get_inbox_message(row['id'])
    assert got['body'] == 'full body text'
    await db.set_inbox_flag(row['id'], 'starred', 1)
    await db.set_inbox_flag(row['id'], 'is_read', 1)
    got = await db.get_inbox_message(row['id'])
    assert got['starred'] == 1 and got['is_read'] == 1
    n = await db.mark_all_inbox_read()
    assert n == 0
