"""Tests for email verification, enrichment, and new DB features."""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.db import Database
from app.verification import verify_email


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / 'test.db'))
    await database.init()
    yield database


# ── Email verification ──────────────────────────────────────────


def test_verify_valid_gmail():
    result = verify_email('test@gmail.com')
    assert result['status'] in ('valid', 'flagged')
    assert result['free_provider'] is True
    assert result['disposable'] is False


def test_verify_disposable():
    result = verify_email('user@mailinator.com')
    assert result['disposable'] is True
    assert result['status'] == 'risky'


def test_verify_invalid_format():
    result = verify_email('not-an-email')
    assert result['status'] == 'invalid'


def test_verify_role_account():
    result = verify_email('info@example.com')
    assert result['role_account'] is True


def test_verify_free_provider():
    for domain in ('gmail.com', 'yahoo.com', 'outlook.com', 'protonmail.com'):
        result = verify_email(f'x@{domain}')
        assert result['free_provider'] is True, f'{domain} should be free'


# ── DB: notes, threads, activity ────────────────────────────────


@pytest.mark.asyncio
async def test_add_and_list_notes(db):
    # Create a lead first
    async with db.connect() as conn:
        cur = await conn.execute(
            '''INSERT INTO leads(source, name, email, fingerprint, created_at, updated_at)
               VALUES ('test', 'Acme', 'acme@test.com', 'fp-acme', '2026-01-01', '2026-01-01')''')
        await conn.commit()
        lead_id = cur.lastrowid

    nid = await db.add_note(lead_id, 'Called them, interested in Taptap', 'call')
    assert nid > 0

    notes = await db.list_notes(lead_id)
    assert len(notes) == 1
    assert dict(notes[0])['note'] == 'Called them, interested in Taptap'
    assert dict(notes[0])['category'] == 'call'


@pytest.mark.asyncio
async def test_upsert_thread(db):
    async with db.connect() as conn:
        cur = await conn.execute(
            '''INSERT INTO leads(source, name, email, fingerprint, created_at, updated_at)
               VALUES ('test', 'Acme', 'acme@test.com', 'fp-acme2', '2026-01-01', '2026-01-01')''')
        await conn.commit()
        lead_id = cur.lastrowid

    tid1 = await db.upsert_thread(lead_id, 'acme@test.com', 'Re: Taptap', 'inbound', '2026-08-20')
    tid2 = await db.upsert_thread(lead_id, 'acme@test.com', 'Re: Taptap', 'outbound', '2026-08-21')
    assert tid1 == tid2  # same thread_key = same thread

    threads = await db.list_threads(lead_id)
    assert len(threads) == 1
    assert dict(threads[0])['message_count'] == 2
    assert dict(threads[0])['last_message_at'] == '2026-08-21'


@pytest.mark.asyncio
async def test_activity_log(db):
    async with db.connect() as conn:
        cur = await conn.execute(
            '''INSERT INTO leads(source, name, email, fingerprint, created_at, updated_at)
               VALUES ('test', 'Acme', 'acme@test.com', 'fp-acme3', '2026-01-01', '2026-01-01')''')
        await conn.commit()
        lead_id = cur.lastrowid

    await db.add_note(lead_id, 'Called them, interested in Taptap', 'call')
    await db.log_activity(lead_id, None, 'email_sent', 'Initial outreach')

    activity = await db.list_activity(lead_id)
    assert len(activity) >= 1
    assert dict(activity[0])['action'] in ('email_sent', 'note')  # recent first


@pytest.mark.asyncio
async def test_ab_variants(db):
    async with db.connect() as conn:
        cur = await conn.execute(
            '''INSERT INTO campaigns(name, subject_template, body_template, created_at, updated_at)
               VALUES ('Test Campaign', 'Subj', 'Body', '2026-01-01', '2026-01-01')''')
        await conn.commit()
        cid = cur.lastrowid

    v1 = await db.create_ab_variant(cid, 'A', 'Subject A', 'Body A')
    v2 = await db.create_ab_variant(cid, 'B', 'Subject B', 'Body B')
    assert v1 > 0 and v2 > 0

    variants = await db.list_ab_variants(cid)
    assert len(variants) == 2

    await db.increment_ab_stat(v1, 'send_count')
    await db.increment_ab_stat(v1, 'send_count')
    await db.increment_ab_stat(v1, 'reply_count')
    await db.increment_ab_stat(v2, 'send_count')

    best = await db.best_ab_variant(cid)
    assert dict(best)['variant_name'] == 'A'  # 50% reply rate vs 0%


@pytest.mark.asyncio
async def test_verification_cache(db):
    await db.cache_verification('test@gmail.com', 'valid', True, False, True, False)
    cached = await db.get_verification('test@gmail.com')
    assert cached is not None
    assert dict(cached)['status'] == 'valid'
    assert dict(cached)['mx_valid'] == 1
    assert dict(cached)['free_provider'] == 1


@pytest.mark.asyncio
async def test_webhooks(db):
    wid = await db.add_webhook('Slack', 'https://hooks.slack.com/test', 'reply,interested')
    assert wid > 0
    webhooks = await db.list_webhooks()
    assert len(webhooks) == 1
