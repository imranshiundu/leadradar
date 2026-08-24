"""Seed the database from the real emails.txt + taptap.txt in the Email/ workspace.

Usage (from the leadradar repo root):
    python scripts/seed_taptap.py /home/imran/Workspace/Email/emails.txt /home/imran/Workspace/Email/taptap.txt

Or without arguments (uses the Email/ files relative to home):
    python scripts/seed_taptap.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(parent))

from app.db import Database
from app.importer import parse_contacts, extract_event_intel
from app.safety import fingerprint

DEFAULT_EMAILS = str(Path.home() / 'Workspace/Email/emails.txt')
DEFAULT_TAPTAP = str(Path.home() / 'Workspace/Email/taptap.txt')


def parse_taptap_template(path: str) -> tuple[str, str]:
    text = Path(path).read_text(encoding='utf-8')
    subj_m = re.search(r'Subject:\s*(.+)', text)
    subject = subj_m.group(1).strip() if subj_m else 'Taptap — October & November Events'
    body_start = text.find('email:')
    body = text[body_start + 6:].strip() if body_start != -1 else text
    # Replace generic "Hi," with personalizable form for campaign template
    body = body.replace('Hi,', 'Hi {{first_name}},')
    return subject, body


async def main():
    emails_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EMAILS
    taptap_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TAPTAP

    print(f'Reading contacts from {emails_path}...')
    raw = Path(emails_path).read_text(encoding='utf-8')
    contacts = parse_contacts(raw)
    print(f'  Parsed {len(contacts)} contacts')

    db = Database()
    await db.init()

    # Import contacts as leads
    imported = 0
    for contact in contacts:
        fp = fingerprint(contact['email'])
        existing = await db.get_lead_by_fingerprint(fp)
        if existing:
            print(f'  skip (exists): {contact["email"]}')
            continue
        name = contact['name']
        email = contact['email']
        phone = contact.get('phone')
        priority = contact.get('priority') or 'medium'
        now = __import__('datetime').datetime.now(
            __import__('datetime').timezone.utc).isoformat(timespec='seconds')
        async with db.connect() as conn:
            cur = await conn.execute(
                '''INSERT INTO leads(source, name, email, phone, priority,
                                     opportunity_type, pipeline_stage,
                                     fingerprint, created_at, updated_at)
                   VALUES ('manual_import', ?, ?, ?, ?, 'event_organizer', 'new', ?, ?, ?)''',
                (name, email, phone, priority, fp, now, now),
            )
            await conn.commit()
            lead_id = int(cur.lastrowid)

        # Extract event intel from name (rough heuristic)
        ev_name, ev_date = extract_event_intel(name)
        if ev_name and ev_date:
            await db.update_event_intel(lead_id, ev_name, ev_date)
        imported += 1
        print(f'  + {name} <{email}>')

    print(f'\nImported {imported} new leads')

    # Create Taptap campaign
    subject_tpl, body_tpl = parse_taptap_template(taptap_path)
    print(f'\nCreating Taptap campaign from {taptap_path}...')
    existing = await db.get_campaign_by_name('Taptap — Oct/Nov Outreach')
    if existing:
        cid = int(existing['id'])
        print(f'  Campaign already exists (id={cid}), updating templates...')
        async with db.connect() as conn:
            await conn.execute(
                'UPDATE campaigns SET subject_template=?, body_template=?, updated_at=? WHERE id=?',
                (subject_tpl, body_tpl, __import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc).isoformat(timespec='seconds'), cid))
            await conn.commit()
    else:
        cid = await db.create_campaign('Taptap — Oct/Nov Outreach', subject_tpl, body_tpl)
        print(f'  Created campaign id={cid}')

    # Add follow-up step (day 3)
    await db.add_sequence_step(cid, 1, delay_days=3,
                               body_template='Hi {{first_name}},\n\nJust following up on my earlier email about Taptap for {{name}}. We\'re opening Taptap to a small number of events in October and November — if you have anything coming up, I\'d love to hear about it.\n\nImran\nhttps://taptap.africa',
                               subject_template='Re: {{name}} — Taptap follow-up')

    # Add follow-up step (day 7)
    await db.add_sequence_step(cid, 2, delay_days=7,
                               body_template='Hi {{first_name}},\n\nOne last note — Taptap handles event entry and payments via QR codes and wristbands, giving organizers a simple dashboard. If you\'re running an event and want to try it, reply and I\'ll set you up.\n\nImran\nhttps://taptap.africa',
                               subject_template='Last note: Taptap for {{name}}')

    # Attach all event-organizer leads to campaign
    rows = await db.list_leads(limit=10000)
    attached = 0
    for row in rows:
        lead = dict(row)
        if lead.get('email') and await db.attach_lead_to_campaign(cid, int(lead['id'])):
            attached += 1
    print(f'  Attached {attached} contacts to campaign')
    print(f'\nDone. Campaign is in draft status — activate it from the dashboard when ready.')


if __name__ == '__main__':
    asyncio.run(main())
