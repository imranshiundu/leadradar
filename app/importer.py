"""Contact list importer.

Parses messy human-format contact lists: markdown tables, TSV, pipe tables,
CSV, and plain lines. Extracts name, phone, email, and priority markers
(fire emoji counts) from each row. Built for lists like emails.txt where rows
look like:

    1	Bigmiitch Events	+254 724 214 461	info@bigmiitchevents.co.ke	🔥🔥🔥
    | Bigmiitch Events | +254... | info@example.com | 🔥🔥🔥 |
"""
from __future__ import annotations

import csv
import io
import re

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
PHONE_RE = re.compile(r'(?:\+?\d[\d\s()./-]{7,}\d)')
FIRE_RE = re.compile(r'🔥')
DASHES = {'—', '-', '–', '—', '—', ''}

MONTHS = (
    'January|February|March|April|May|June|July|August|September|October|November|December'
    '|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec'
)
EVENT_DATE_RE = re.compile(
    rf'({MONTHS})\s+\d{{1,2}}(?:\s*[–\-—]\s*\d{{1,2}})?(?:,?\s*\d{{4}})?', re.IGNORECASE)
EVENT_NAME_HINTS = (
    'summit', 'conference', 'festival', 'market', 'expo', 'cup', 'awards',
    'show', 'fair', 'concert', 'marathon', 'gala', 'meetup',
)


def split_row(line: str) -> list[str]:
    """Split a row into cells handling pipes, tabs, commas-in-CSV and wide spacing."""
    stripped = line.strip()
    if stripped.startswith('|'):
        return [c.strip() for c in stripped.strip('|').split('|')]
    if '\t' in stripped:
        return [c.strip() for c in stripped.split('\t')]
    # comma-separated with at least one comma outside of a phone number
    if ',' in stripped:
        try:
            cells = next(csv.reader(io.StringIO(stripped)))
            if len(cells) >= 2:
                return [c.strip() for c in cells]
        except csv.Error:
            pass
    parts = re.split(r'\s{2,}', stripped)
    if len(parts) >= 3:
        return [p.strip() for p in parts]
    return [stripped]


def looks_like_noise(cell: str) -> bool:
    c = cell.strip()
    return (not c) or c in DASHES or c.isdigit() or bool(FIRE_RE.search(c)) \
        or PHONE_RE.fullmatch(re.sub(r'\s+', ' ', c)) is not None


def parse_contacts(text: str) -> list[dict]:
    """Return deduped-by-email contact dicts: name, email, phone, priority."""
    contacts: dict[str, dict] = {}
    order: list[str] = []
    total_rows = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or EMAIL_RE.search(line) is None:
            continue
        if line.lower().startswith(('subject:', 'cc:', 'email:')):
            continue
        total_rows += 1

        ematch = EMAIL_RE.search(line)
        assert ematch is not None
        email = ematch.group(0).lower()
        before_email = line[:ematch.start()]
        # Only strip leading pipe/space; preserve internal pipes for split_row.
        before_email = before_email.lstrip(' |\t#')
        after_email = line[ematch.end():].strip(' |\t')

        pm = PHONE_RE.search(before_email)
        phone = None
        if pm:
            candidate = re.sub(r'\s{2,}', ' ', pm.group(0)).strip()
            digits = sum(ch.isdigit() for ch in candidate)
            if digits >= 9:
                phone = candidate

        fires = len(FIRE_RE.findall(line))
        priority = 'high' if fires >= 3 else ('medium' if fires == 2 else None)

        # For pipe/table rows, use the pipe-split cells; otherwise space-split.
        name = None
        is_pipe = '|' in line
        cells = split_row(line if is_pipe else (before_email or line))
        for cell in cells:
            cell_clean = re.sub(r'^\d+[.)]\s*', '', cell).strip()
            if looks_like_noise(cell_clean):
                continue
            if '@' in cell_clean:
                continue
            name = cell_clean.rstrip('/ ').strip()
            break
        if not name:
            local = email.split('@')[0].replace('.', ' ').replace('_', ' ')
            name = local.title()

        if email in contacts:
            existing = contacts[email]
            if priority and not existing.get('priority'):
                existing['priority'] = priority
            continue

        contact = {
            'name': name,
            'email': email,
            'phone': phone,
            'priority': priority,
        }
        contacts[email] = contact
        order.append(email)

    return [contacts[e] for e in order]


def extract_event_intel(text: str) -> tuple[str | None, str | None]:
    """Heuristically find an upcoming event name + date inside text."""
    best_name: str | None = None
    best_date: str | None = None
    lines = re.split(r'(?<=[.!?])\s+|\n', text)
    for line in lines:
        dm = EVENT_DATE_RE.search(line)
        if not dm:
            continue
        date_str = dm.group(0).strip()
        words = re.findall(r"[A-Za-z&][A-Za-z&'’\-]+", line[:dm.start()])
        name_words = ' '.join(words[-6:]).strip()
        lowered = name_words.lower()
        has_hint = any(h in lowered for h in EVENT_NAME_HINTS)
        if name_words and (has_hint or best_name is None):
            if has_hint or best_name is None:
                best_name = name_words if has_hint else (best_name or name_words)
                best_date = date_str
                if has_hint:
                    return best_name, best_date
    return best_name, best_date


def render_template(template: str, context: dict) -> str:
    """Render {{var}} placeholders; unknown vars become empty strings."""
    def _sub(match: re.Match) -> str:
        key = match.group(1).strip()
        value = context.get(key)
        return str(value) if value is not None else ''

    return re.sub(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', _sub, template)


def lead_template_context(lead: dict) -> dict:
    name = lead.get('name') or ''
    first_word = name.split()[0] if name.split() else ''
    return {
        'name': name,
        'first_name': first_word,
        'email': lead.get('email') or '',
        'city': lead.get('city') or '',
        'business_type': lead.get('business_type') or '',
        'event_name': lead.get('event_name') or '',
        'event_date': lead.get('event_date') or '',
        'phone': lead.get('phone') or '',
    }
