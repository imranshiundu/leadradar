from __future__ import annotations

import pytest

from app.importer import extract_event_intel, parse_contacts, render_template
from app.inbox import classify_reply

SAMPLE = """emails

Start with these first

#\tHost / Organizer\tPhone\tEmail\tPriority
1\tBigmiitch Events\t+254 724 214 461\tinfo@bigmiitchevents.co.ke\t🔥🔥🔥
2\tNairobi Play\t+254 708 208 767\tnairobiplay254@gmail.com\t🔥🔥🔥
3\tKenstar Events\t+254 729 582 355\tkenstareventsandadventures@gmail.com\t🔥🔥🔥

More people to contact (no emails — must be skipped)
21\tEvent House Limited\t+254 733 440 035\t—
22\tEventage Kenya\t+254 720 325 200\t—

Established organizers
41\tBig 5 Construct Kenya\tsales line +971 4 445 3639\tsales@big5constructkenya.com
42\tVintage Concepts\t+254 721 586 671\tinfo@vintageconcepts.biz
"""


def test_parse_contacts_extracts_and_skips():
    contacts = parse_contacts(SAMPLE)
    emails = [c['email'] for c in contacts]
    assert 'info@bigmiitchevents.co.ke' in emails
    assert 'sales@big5constructkenya.com' in emails
    # rows without an email never become contacts
    assert len(contacts) == 5

    bigmiitch = next(c for c in contacts if c['email'] == 'info@bigmiitchevents.co.ke')
    assert bigmiitch['name'] == 'Bigmiitch Events'
    assert bigmiitch['phone'].startswith('+254')
    assert bigmiitch['priority'] == 'high'

    big5 = next(c for c in contacts if c['email'] == 'sales@big5constructkenya.com')
    assert big5['name'] == 'Big 5 Construct Kenya'


def test_parse_contacts_dedupes():
    dup = SAMPLE + '\n99\tBigmiitch Again\t+254 700 000 000\tinfo@bigmiitchevents.co.ke\n'
    contacts = parse_contacts(dup)
    assert sum(1 for c in contacts if c['email'] == 'info@bigmiitchevents.co.ke') == 1


def test_render_template():
    out = render_template('Hi {{name}}, re {{event_name}} on {{event_date}}. From {{missing_var}}.',
                          {'name': 'Bigmiitch', 'event_name': 'Realtors Summit', 'event_date': 'October 9'})
    assert out == 'Hi Bigmiitch, re Realtors Summit on October 9. From .'


def test_extract_event_intel():
    text = ('Nairobi Realtors Summit has an October 9 event and another November 2026 event; '
            "Smart Farmer Africa's East Africa Coffee Markets & Conference is October 28–30.")
    name, date_str = extract_event_intel(text)
    assert date_str is not None
    assert 'October' in date_str or 'November' in date_str
    assert name is not None


def test_classify_reply():
    assert classify_reply('Re: Taptap', 'We are interested, let us talk') == 'interested'
    assert classify_reply('Re: hi', 'not interested right now') == 'not_interested'
    assert classify_reply('Delivery Status Notification', 'address not found', 'mailer-daemon@x.com') == 'bounce'
    assert classify_reply('Automatic reply', 'I am out of office until Monday') == 'ooo'
    assert classify_reply('Re: hello', 'thanks for the note') == 'unknown'


def test_parse_pipe_table():
    pipe = '| Acme Ltd | +254 111 222 333 | hello@acme.co.ke | 🔥 |'
    contacts = parse_contacts(pipe)
    assert len(contacts) == 1
    assert contacts[0]['name'] == 'Acme Ltd'
