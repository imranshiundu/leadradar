from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
import aiosqlite
from app.models import LeadCreate, LeadOut, LeadStatus
from app.config import get_settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or get_settings().database_path

    def connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.path)

    async def init(self) -> None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(
                '''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_url TEXT,
                    name TEXT NOT NULL,
                    business_type TEXT,
                    city TEXT,
                    website_url TEXT,
                    email TEXT,
                    phone TEXT,
                    social_url TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    need_score INTEGER NOT NULL DEFAULT 0,
                    opportunity_type TEXT NOT NULL DEFAULT 'website_lead',
                    pipeline_stage TEXT NOT NULL DEFAULT 'new',
                    event_name TEXT,
                    event_date TEXT,
                    priority TEXT,
                    tags TEXT,
                    ai_summary TEXT,
                    ai_reason TEXT,
                    draft_message TEXT,
                    raw_text TEXT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_leads_status_score ON leads(status, need_score DESC);
                CREATE INDEX IF NOT EXISTS idx_leads_opportunity_type ON leads(opportunity_type);
                CREATE INDEX IF NOT EXISTS idx_leads_pipeline ON leads(pipeline_stage);

                CREATE TABLE IF NOT EXISTS events_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    lead_id INTEGER,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events_log(created_at DESC);

                CREATE TABLE IF NOT EXISTS source_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS opt_outs (
                    email TEXT PRIMARY KEY,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    subject_template TEXT NOT NULL,
                    body_template TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    daily_limit INTEGER NOT NULL DEFAULT 25,
                    min_seconds_between_sends INTEGER NOT NULL DEFAULT 300,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sequence_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                    step_order INTEGER NOT NULL,
                    delay_days INTEGER NOT NULL DEFAULT 3,
                    subject_template TEXT,
                    body_template TEXT NOT NULL,
                    UNIQUE(campaign_id, step_order)
                );

                CREATE TABLE IF NOT EXISTS campaign_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                    lead_id INTEGER NOT NULL REFERENCES leads(id),
                    current_step INTEGER NOT NULL DEFAULT 0,
                    next_send_at TEXT,
                    finished INTEGER NOT NULL DEFAULT 0,
                    stop_reason TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(campaign_id, lead_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cl_due ON campaign_leads(finished, next_send_at);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    campaign_id INTEGER REFERENCES campaigns(id),
                    step_order INTEGER NOT NULL DEFAULT 1,
                    to_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'sent',
                    error TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_mid ON messages(message_id);
                CREATE INDEX IF NOT EXISTS idx_messages_campaign ON messages(campaign_id, sent_at);

                CREATE TABLE IF NOT EXISTS replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    message_row_id INTEGER REFERENCES messages(id),
                    from_email TEXT,
                    subject TEXT,
                    snippet TEXT,
                    classification TEXT NOT NULL DEFAULT 'unknown',
                    received_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT,
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    imported INTEGER NOT NULL DEFAULT 0,
                    duplicates INTEGER NOT NULL DEFAULT 0,
                    missing_email INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS email_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    thread_key TEXT NOT NULL,
                    subject TEXT,
                    last_message_at TEXT,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    direction TEXT NOT NULL DEFAULT 'unknown',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_threads_lead ON email_threads(lead_id);
                CREATE INDEX IF NOT EXISTS idx_threads_key ON email_threads(thread_key);

                CREATE TABLE IF NOT EXISTS lead_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL REFERENCES leads(id),
                    note TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notes_lead ON lead_notes(lead_id);

                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    campaign_id INTEGER REFERENCES campaigns(id),
                    action TEXT NOT NULL,
                    detail TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_lead ON activity_log(lead_id);
                CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at DESC);

                CREATE TABLE IF NOT EXISTS ab_variants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
                    variant_name TEXT NOT NULL,
                    subject_template TEXT,
                    body_template TEXT NOT NULL,
                    send_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    open_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS email_verification (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mx_valid INTEGER NOT NULL DEFAULT 0,
                    disposable INTEGER NOT NULL DEFAULT 0,
                    free_provider INTEGER NOT NULL DEFAULT 0,
                    role_account INTEGER NOT NULL DEFAULT 0,
                    last_checked TEXT NOT NULL,
                    UNIQUE(email)
                );

                CREATE TABLE IF NOT EXISTS send_time_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER REFERENCES leads(id),
                    hour_utc INTEGER NOT NULL,
                    day_of_week INTEGER NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0,
                    replied INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(lead_id, hour_utc, day_of_week)
                );

                CREATE TABLE IF NOT EXISTS webhooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    events TEXT NOT NULL DEFAULT 'reply,interested',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                '''
            )
            await db.commit()
            await self._migrate_legacy(db)

    async def _migrate_legacy(self, db: aiosqlite.Connection) -> None:
        # Older installs used table name `events`; rename if present and columns missing.
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        row = await cur.fetchone()
        if row:
            cur2 = await db.execute("SELECT COUNT(*) AS c FROM migrations WHERE name='001_rename_events'")
            already = await cur2.fetchone()
            if not already or not int(already['c']):
                await db.execute('ALTER TABLE events RENAME TO events_log')
                await db.execute("INSERT OR REPLACE INTO migrations(name, applied_at) VALUES ('001_rename_events', ?)", (utc_now(),))
                await db.commit()
        # Add v2 columns to existing leads tables.
        existing_cols = set()
        cur = await db.execute('PRAGMA table_info(leads)')
        for col in await cur.fetchall():
            existing_cols.add(col['name'])
        additions = {
            'pipeline_stage': "TEXT NOT NULL DEFAULT 'new'",
            'event_name': 'TEXT',
            'event_date': 'TEXT',
            'priority': 'TEXT',
            'tags': 'TEXT',
        }
        for col, decl in additions.items():
            if col not in existing_cols:
                await db.execute(f'ALTER TABLE leads ADD COLUMN {col} {decl}')
        await db.commit()

    async def insert_lead(self, lead: LeadCreate) -> tuple[int | None, bool]:
        now = utc_now()
        try:
            async with self.connect() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    '''
                    INSERT INTO leads (
                        source, source_url, name, business_type, city, website_url,
                        email, phone, social_url, opportunity_type, raw_text,
                        fingerprint, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        lead.source, lead.source_url, lead.name, lead.business_type, lead.city,
                        lead.website_url, lead.email, lead.phone, lead.social_url,
                        lead.opportunity_type, lead.raw_text, lead.fingerprint, now, now,
                    ),
                )
                await db.commit()
                return int(cur.lastrowid), True
        except aiosqlite.IntegrityError:
            existing = await self.get_lead_by_fingerprint(lead.fingerprint)
            return (existing['id'] if existing else None), False

    async def get_lead_by_fingerprint(self, fingerprint: str) -> aiosqlite.Row | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM leads WHERE fingerprint = ?', (fingerprint,))
            return await cur.fetchone()

    async def get_lead(self, lead_id: int) -> aiosqlite.Row | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM leads WHERE id = ?', (lead_id,))
            return await cur.fetchone()

    async def list_leads(self, status: str | None = None, limit: int = 100, offset: int = 0) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            if status:
                cur = await db.execute(
                    'SELECT * FROM leads WHERE status = ? ORDER BY need_score DESC, created_at DESC LIMIT ? OFFSET ?',
                    (status, limit, offset),
                )
            else:
                cur = await db.execute(
                    'SELECT * FROM leads ORDER BY created_at DESC LIMIT ? OFFSET ?',
                    (limit, offset),
                )
            return await cur.fetchall()

    async def update_classification(self, lead_id: int, score: int, summary: str, reason: str, draft: str | None = None) -> None:
        now = utc_now()
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                'UPDATE leads SET need_score=?, ai_summary=?, ai_reason=?, draft_message=COALESCE(?, draft_message), updated_at=? WHERE id=?',
                (score, summary, reason, draft, now, lead_id),
            )
            await db.commit()

    async def set_status(self, lead_id: int, status: LeadStatus | str, detail: str | None = None) -> None:
        now = utc_now()
        status_value = status.value if isinstance(status, LeadStatus) else status
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('UPDATE leads SET status=?, updated_at=? WHERE id=?', (status_value, now, lead_id))
            await db.execute(
                'INSERT INTO events_log(event_type, lead_id, detail, created_at) VALUES (?, ?, ?, ?)',
                (f'status:{status_value}', lead_id, detail, now),
            )
            await db.commit()

    async def add_event(self, event_type: str, detail: str | dict[str, Any] | None = None, lead_id: int | None = None) -> None:
        if isinstance(detail, dict):
            detail = json.dumps(detail, ensure_ascii=False)
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                'INSERT INTO events_log(event_type, lead_id, detail, created_at) VALUES (?, ?, ?, ?)',
                (event_type, lead_id, detail, utc_now()),
            )
            await db.commit()

    async def set_state(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                '''INSERT INTO source_state(key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at''',
                (key, payload, utc_now()),
            )
            await db.commit()

    async def get_state(self, key: str, default: Any = None) -> Any:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT value FROM source_state WHERE key = ?', (key,))
            row = await cur.fetchone()
            if not row:
                return default
            try:
                return json.loads(row['value'])
            except json.JSONDecodeError:
                return default

    async def is_opted_out(self, email: str | None) -> bool:
        if not email:
            return False
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT 1 FROM opt_outs WHERE email = ?', (email.lower(),))
            return await cur.fetchone() is not None

    async def add_opt_out(self, email: str, reason: str | None = None) -> None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                'INSERT OR REPLACE INTO opt_outs(email, reason, created_at) VALUES (?, ?, ?)',
                (email.lower(), reason, utc_now()),
            )
            await db.commit()

    async def sent_count_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM events_log WHERE event_type='email:sent' AND created_at LIKE ?",
                (f'{today}%',),
            )
            row = await cur.fetchone()
            return int(row['c'] if row else 0)

    # ------------------------------------------------------------------
    # Campaigns & sequences
    # ------------------------------------------------------------------

    async def create_campaign(self, name: str, subject_template: str, body_template: str,
                              daily_limit: int = 25, min_seconds_between_sends: int = 300) -> int:
        now = utc_now()
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO campaigns(name, subject_template, body_template, status,
                                         daily_limit, min_seconds_between_sends, created_at, updated_at)
                   VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)''',
                (name, subject_template, body_template, daily_limit, min_seconds_between_sends, now, now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_campaign(self, campaign_id: int) -> aiosqlite.Row | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM campaigns WHERE id=?', (campaign_id,))
            return await cur.fetchone()

    async def get_campaign_by_name(self, name: str) -> aiosqlite.Row | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM campaigns WHERE name=?', (name,))
            return await cur.fetchone()

    async def list_campaigns(self) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM campaigns ORDER BY created_at DESC')
            return await cur.fetchall()

    async def set_campaign_status(self, campaign_id: int, status: str) -> None:
        async with self.connect() as db:
            await db.execute('UPDATE campaigns SET status=?, updated_at=? WHERE id=?',
                             (status, utc_now(), campaign_id))
            await db.commit()

    async def add_sequence_step(self, campaign_id: int, step_order: int, delay_days: int,
                                body_template: str, subject_template: str | None = None) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO sequence_steps(campaign_id, step_order, delay_days, subject_template, body_template)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(campaign_id, step_order) DO UPDATE SET
                     delay_days=excluded.delay_days,
                     subject_template=excluded.subject_template,
                     body_template=excluded.body_template''',
                (campaign_id, step_order, delay_days, subject_template, body_template),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def list_sequence_steps(self, campaign_id: int) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                'SELECT * FROM sequence_steps WHERE campaign_id=? ORDER BY step_order', (campaign_id,))
            return await cur.fetchall()

    async def attach_lead_to_campaign(self, campaign_id: int, lead_id: int,
                                      first_send_at: str | None = None) -> bool:
        try:
            async with self.connect() as db:
                await db.execute(
                    '''INSERT INTO campaign_leads(campaign_id, lead_id, current_step, next_send_at, finished, created_at)
                       VALUES (?, ?, 0, ?, 0, ?)''',
                    (campaign_id, lead_id, first_send_at or utc_now(), utc_now()),
                )
                await db.commit()
                return True
        except aiosqlite.IntegrityError:
            return False

    async def due_campaign_leads(self, campaign_id: int, limit: int = 50) -> list[dict]:
        now = utc_now()
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                '''SELECT cl.*, l.email, l.name, l.city, l.business_type, l.event_name, l.event_date,
                          l.status AS lead_status, l.id AS lead_row_id
                   FROM campaign_leads cl JOIN leads l ON l.id = cl.lead_id
                   WHERE cl.campaign_id=? AND cl.finished=0 AND cl.next_send_at <= ?
                   ORDER BY cl.next_send_at LIMIT ?''',
                (campaign_id, now, limit),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def advance_campaign_lead(self, cl_id: int, next_step: int, next_send_at: str | None,
                                    finished: bool, stop_reason: str | None = None) -> None:
        async with self.connect() as db:
            await db.execute(
                '''UPDATE campaign_leads SET current_step=?, next_send_at=?, finished=?, stop_reason=? WHERE id=?''',
                (next_step, next_send_at, 1 if finished else 0, stop_reason, cl_id),
            )
            await db.commit()

    async def stop_campaign_leads_for_reply(self, lead_id: int) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                '''UPDATE campaign_leads SET finished=1, stop_reason='replied' WHERE lead_id=? AND finished=0''',
                (lead_id,),
            )
            await db.commit()
            return cur.rowcount

    async def record_message(self, lead_id: int, campaign_id: int, step_order: int,
                             to_email: str, subject: str, body: str,
                             message_id: str | None = None, status: str = 'sent',
                             error: str | None = None) -> int:
        now = utc_now()
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO messages(lead_id, campaign_id, step_order, to_email, subject, body,
                                        message_id, status, error, sent_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (lead_id, campaign_id, step_order, to_email, subject, body,
                 message_id, status, error, now if status == 'sent' else None, now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def find_message_by_rfc_ids(self, candidate_ids: list[str]) -> aiosqlite.Row | None:
        if not candidate_ids:
            return None
        placeholders = ','.join('?' for _ in candidate_ids)
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f'SELECT * FROM messages WHERE message_id IN ({placeholders}) ORDER BY id DESC LIMIT 1',
                candidate_ids,
            )
            return await cur.fetchone()

    async def record_reply(self, lead_id: int | None, message_row_id: int | None,
                           from_email: str | None, subject: str | None, snippet: str,
                           classification: str, received_at: str | None) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO replies(lead_id, message_row_id, from_email, subject, snippet,
                                       classification, received_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (lead_id, message_row_id, from_email, subject, snippet[:2000],
                 classification, received_at, utc_now()),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def set_pipeline_stage(self, lead_id: int, stage: str, detail: str | None = None) -> None:
        now = utc_now()
        async with self.connect() as db:
            await db.execute('UPDATE leads SET pipeline_stage=?, updated_at=? WHERE id=?', (stage, now, lead_id))
            await db.execute(
                'INSERT INTO events_log(event_type, lead_id, detail, created_at) VALUES (?, ?, ?, ?)',
                ('pipeline:' + stage, lead_id, detail, now),
            )
            await db.commit()

    async def update_event_intel(self, lead_id: int, event_name: str | None, event_date: str | None) -> None:
        async with self.connect() as db:
            await db.execute(
                'UPDATE leads SET event_name=COALESCE(?, event_name), event_date=COALESCE(?, event_date), updated_at=? WHERE id=?',
                (event_name, event_date, utc_now(), lead_id),
            )
            await db.commit()

    async def campaign_analytics(self, campaign_id: int) -> dict:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row

            out: dict[str, Any] = {'campaign_id': campaign_id}

            cur = await db.execute('SELECT COUNT(*) AS c FROM campaign_leads WHERE campaign_id=?', (campaign_id,))
            out['targets'] = int((await cur.fetchone())['c'])
            cur = await db.execute('SELECT COUNT(*) AS c FROM campaign_leads WHERE campaign_id=? AND finished=1', (campaign_id,))
            out['finished'] = int((await cur.fetchone())['c'])
            cur = await db.execute(
                "SELECT COUNT(*) AS c FROM campaign_leads WHERE campaign_id=? AND stop_reason='replied'", (campaign_id,))
            out['stopped_replied'] = int((await cur.fetchone())['c'])
            cur = await db.execute(
                "SELECT COUNT(*) AS c, COALESCE(MAX(step_order),0) AS maxstep FROM messages WHERE campaign_id=? AND status='sent'",
                (campaign_id,))
            row = await cur.fetchone()
            out['messages_sent'] = int(row['c'])
            cur = await db.execute("SELECT COUNT(*) AS c FROM messages WHERE campaign_id=? AND status='failed'", (campaign_id,))
            out['messages_failed'] = int((await cur.fetchone())['c'])

            stages: dict[str, int] = {}
            cur = await db.execute(
                '''SELECT l.pipeline_stage AS stage, COUNT(*) AS c
                   FROM campaign_leads cl JOIN leads l ON l.id=cl.lead_id
                   WHERE cl.campaign_id=? GROUP BY l.pipeline_stage''',
                (campaign_id,))
            for r in await cur.fetchall():
                stages[r['stage']] = int(r['c'])
            out['pipeline'] = stages

            classifications: dict[str, int] = {}
            cur = await db.execute(
                '''SELECT r.classification AS cls, COUNT(*) AS c
                   FROM replies r JOIN messages m ON m.id=r.message_row_id
                   WHERE m.campaign_id=? GROUP BY r.classification''',
                (campaign_id,))
            for r in await cur.fetchall():
                classifications[r['cls']] = int(r['c'])
            out['reply_breakdown'] = classifications
            return out

    async def daily_send_stats(self, days: int = 14) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                '''SELECT substr(sent_at, 1, 10) AS day, COUNT(*) AS sent
                   FROM messages WHERE status='sent' AND sent_at >= ?
                   GROUP BY day ORDER BY day DESC''',
                (since,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def list_replies(self, limit: int = 100) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM replies ORDER BY created_at DESC LIMIT ?', (limit,))
            return await cur.fetchall()

    async def count_import_batch(self, filename: str, total_rows: int, imported: int,
                                 duplicates: int, missing_email: int) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO import_batches(filename, total_rows, imported, duplicates, missing_email, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (filename, total_rows, imported, duplicates, missing_email, utc_now()),
            )
            await db.commit()
            return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Email threads
    # ------------------------------------------------------------------

    async def upsert_thread(self, lead_id: int, thread_key: str, subject: str,
                            direction: str, message_at: str | None = None) -> int:
        now = utc_now()
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                'SELECT id FROM email_threads WHERE thread_key=?', (thread_key,))
            existing = await cur.fetchone()
            if existing:
                await db.execute(
                    '''UPDATE email_threads SET last_message_at=?, message_count=message_count+1,
                       subject=COALESCE(?, subject) WHERE id=?''',
                    (message_at or now, subject, int(existing['id'])))
                await db.commit()
                return int(existing['id'])
            cur = await db.execute(
                '''INSERT INTO email_threads(lead_id, thread_key, subject, last_message_at,
                                             message_count, direction, status, created_at)
                   VALUES (?, ?, ?, ?, 1, ?, 'active', ?)''',
                (lead_id, thread_key, subject, message_at or now, direction, now))
            await db.commit()
            return int(cur.lastrowid)

    async def list_threads(self, lead_id: int | None = None, limit: int = 50) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            if lead_id:
                cur = await db.execute(
                    'SELECT * FROM email_threads WHERE lead_id=? ORDER BY last_message_at DESC LIMIT ?',
                    (lead_id, limit))
            else:
                cur = await db.execute(
                    'SELECT * FROM email_threads ORDER BY last_message_at DESC LIMIT ?', (limit,))
            return await cur.fetchall()

    # ------------------------------------------------------------------
    # Lead notes
    # ------------------------------------------------------------------

    async def add_note(self, lead_id: int, note: str, category: str = 'general') -> int:
        async with self.connect() as db:
            cur = await db.execute(
                'INSERT INTO lead_notes(lead_id, note, category, created_at) VALUES (?, ?, ?, ?)',
                (lead_id, note, category, utc_now()))
            await db.commit()
            return int(cur.lastrowid)

    async def list_notes(self, lead_id: int, limit: int = 50) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                'SELECT * FROM lead_notes WHERE lead_id=? ORDER BY created_at DESC LIMIT ?',
                (lead_id, limit))
            return await cur.fetchall()

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------

    async def log_activity(self, lead_id: int | None, campaign_id: int | None,
                           action: str, detail: str | None = None,
                           metadata: dict | None = None) -> int:
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO activity_log(lead_id, campaign_id, action, detail, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (lead_id, campaign_id, action, detail, meta_json, utc_now()))
            await db.commit()
            return int(cur.lastrowid)

    async def list_activity(self, lead_id: int, limit: int = 100) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                'SELECT * FROM activity_log WHERE lead_id=? ORDER BY created_at DESC LIMIT ?',
                (lead_id, limit))
            return await cur.fetchall()

    # ------------------------------------------------------------------
    # A/B testing
    # ------------------------------------------------------------------

    async def create_ab_variant(self, campaign_id: int, variant_name: str,
                                subject_template: str | None, body_template: str) -> int:
        async with self.connect() as db:
            cur = await db.execute(
                '''INSERT INTO ab_variants(campaign_id, variant_name, subject_template, body_template, created_at)
                   VALUES (?, ?, ?, ?, ?)''',
                (campaign_id, variant_name, subject_template, body_template, utc_now()))
            await db.commit()
            return int(cur.lastrowid)

    async def list_ab_variants(self, campaign_id: int) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                'SELECT * FROM ab_variants WHERE campaign_id=? ORDER BY variant_name', (campaign_id,))
            return await cur.fetchall()

    async def increment_ab_stat(self, variant_id: int, field: str) -> None:
        if field not in ('send_count', 'reply_count', 'open_count'):
            return
        async with self.connect() as db:
            await db.execute(
                f'UPDATE ab_variants SET {field}={field}+1 WHERE id=?', (variant_id,))
            await db.commit()

    async def best_ab_variant(self, campaign_id: int) -> aiosqlite.Row | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                '''SELECT * FROM ab_variants WHERE campaign_id=? AND send_count > 0
                   ORDER BY (CAST(reply_count AS FLOAT) / MAX(send_count, 1)) DESC LIMIT 1''',
                (campaign_id,))
            return await cur.fetchone()

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------

    async def cache_verification(self, email: str, status: str, mx_valid: bool,
                                 disposable: bool, free_provider: bool, role_account: bool) -> None:
        async with self.connect() as db:
            await db.execute(
                '''INSERT OR REPLACE INTO email_verification(email, status, mx_valid, disposable,
                                                            free_provider, role_account, last_checked)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (email, status, 1 if mx_valid else 0, 1 if disposable else 0,
                 1 if free_provider else 0, 1 if role_account else 0, utc_now()))
            await db.commit()

    async def get_verification(self, email: str) -> aiosqlite.Row | None:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute('SELECT * FROM email_verification WHERE email=?', (email,))
            return await cur.fetchone()

    # ------------------------------------------------------------------
    # Send-time optimization
    # ------------------------------------------------------------------

    async def record_send_time(self, lead_id: int, hour_utc: int, day_of_week: int,
                               replied: bool = False) -> None:
        async with self.connect() as db:
            await db.execute(
                '''INSERT INTO send_time_stats(lead_id, hour_utc, day_of_week, sent, replied)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(lead_id, hour_utc, day_of_week) DO UPDATE SET
                     sent=sent+1, replied=replied+?''',
                (lead_id, hour_utc, day_of_week, 1 if replied else 0, 1 if replied else 0))
            await db.commit()

    async def best_send_times(self, lead_id: int, limit: int = 3) -> list[dict]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                '''SELECT hour_utc, day_of_week, sent, replied,
                   CAST(replied AS FLOAT) / MAX(sent, 1) AS reply_rate
                   FROM send_time_stats WHERE lead_id=? AND sent >= 2
                   ORDER BY reply_rate DESC, sent DESC LIMIT ?''',
                (lead_id, limit))
            return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    async def add_webhook(self, name: str, url: str, events: str = 'reply,interested') -> int:
        async with self.connect() as db:
            cur = await db.execute(
                'INSERT INTO webhooks(name, url, events, active, created_at) VALUES (?, ?, ?, 1, ?)',
                (name, url, events, utc_now()))
            await db.commit()
            return int(cur.lastrowid)

    async def list_webhooks(self, active_only: bool = True) -> list[aiosqlite.Row]:
        async with self.connect() as db:
            db.row_factory = aiosqlite.Row
            q = 'SELECT * FROM webhooks' + (' WHERE active=1' if active_only else '')
            cur = await db.execute(q + ' ORDER BY created_at DESC')
            return await cur.fetchall()

    async def trigger_webhooks(self, event_type: str, payload: dict) -> None:
        import asyncio
        import httpx
        webhooks = await self.list_webhooks(active_only=True)
        for wh in webhooks:
            wh = dict(wh)
            if event_type not in wh.get('events', '').split(','):
                continue
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(wh['url'], json={
                        'event': event_type,
                        'timestamp': utc_now(),
                        **payload,
                    })
                await self.add_event('webhook:sent', {'webhook_id': wh['id'], 'event': event_type})
            except Exception as exc:  # noqa: BLE001
                await self.add_event('webhook:error', {'webhook_id': wh['id'], 'error': str(exc)})
