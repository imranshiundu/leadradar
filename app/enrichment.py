"""Lead enrichment — auto-fetch company info, social profiles, and web presence.

Fetches the lead's website (if any) and extracts:
- Company description (meta description, og:description)
- Social links (Twitter, LinkedIn, Instagram, Facebook)
- Phone numbers not yet captured
- Event names/dates from website content
- Technology stack hints (frameworks, CMS)
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.db import Database


SOCIAL_PATTERNS = {
    'twitter': re.compile(r'https?://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]+)'),
    'linkedin': re.compile(r'https?://(?:www\.)?linkedin\.com/(?:company|in)/([A-Za-z0-9_-]+)'),
    'instagram': re.compile(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)'),
    'facebook': re.compile(r'https?://(?:www\.)?facebook\.com/([A-Za-z0-9_.]+)'),
}

PHONE_RE = re.compile(r'(?:\+?\d[\d\s()./-]{7,}\d)')
MONTHS = 'January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec'
EVENT_RE = re.compile(rf'({MONTHS})\s+\d{{1,2}}(?:\s*[–\-—]\s*\d{{1,2}})?(?:,?\s*\d{{4}})?', re.IGNORECASE)
EVENT_HINTS = ('summit', 'conference', 'festival', 'market', 'expo', 'cup', 'awards', 'show', 'fair', 'concert', 'marathon', 'gala')


async def enrich_lead(db: Database, lead_id: int, website_url: str | None = None) -> dict:
    """Fetch and extract info from a lead's website. Returns extracted data dict."""
    lead_row = await db.get_lead(lead_id)
    if not lead_row:
        return {}
    lead = dict(lead_row)
    url = website_url or lead.get('website_url')
    if not url or not url.startswith('http'):
        return {}

    extracted: dict = {}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={'User-Agent': 'LeadRadarSafe/1.0'})
            if resp.status_code >= 400:
                return {}
            html = resp.text[:50000]
    except Exception:  # noqa: BLE001
        return {}

    soup = BeautifulSoup(html, 'html.parser')

    # Meta description
    desc_tag = soup.find('meta', attrs={'name': 'description'})
    if not desc_tag:
        desc_tag = soup.find('meta', attrs={'property': 'og:description'})
    if desc_tag and desc_tag.get('content'):
        extracted['description'] = desc_tag['content'][:500]

    # Title
    if soup.title and soup.title.string:
        extracted['title'] = soup.title.string.strip()[:200]

    # Social links
    text = soup.get_text(' ', strip=True)[:30000]
    for platform, pattern in SOCIAL_PATTERNS.items():
        match = pattern.search(html)
        if match:
            extracted[f'{platform}_url'] = match.group(0)

    # Phone numbers not yet captured
    phones = set(PHONE_RE.findall(text))
    if phones and not lead.get('phone'):
        extracted['phones'] = list(phones)[:3]

    # Event intel
    event_matches = EVENT_RE.findall(text)
    if event_matches and not lead.get('event_name'):
        for em in event_matches:
            date_str = em.strip() if isinstance(em, str) else em[0]
            ctx_start = max(0, text.lower().find(date_str.lower()) - 100)
            ctx = text[ctx_start:text.lower().find(date_str.lower()) + len(date_str)]
            words = re.findall(r"[A-Za-z&][A-Za-z&'’\-]+", ctx)
            name_words = ' '.join(words[-6:]).strip()
            if name_words and any(h in name_words.lower() for h in EVENT_HINTS):
                extracted['event_name'] = name_words
                extracted['event_date'] = date_str
                break

    # Tech stack hints
    techs = []
    html_lower = html.lower()
    tech_hints = {
        'WordPress': 'wp-content', 'React': 'react', 'Next.js': 'next',
        'Vue': 'vue', 'Angular': 'angular', 'Shopify': 'shopify',
        'Wix': 'wix.com', 'Squarespace': 'squarespace',
        'Laravel': 'laravel', 'Django': 'django', 'FastAPI': 'fastapi',
        'Vercel': 'vercel', 'Netlify': 'netlify', 'Cloudflare': 'cloudflare',
    }
    for tech, hint in tech_hints.items():
        if hint in html_lower:
            techs.append(tech)
    if techs:
        extracted['tech_stack'] = techs

    # Update DB
    updates = {}
    if extracted.get('description') and not lead.get('ai_summary'):
        updates['ai_summary'] = extracted['description']
    if extracted.get('event_name'):
        await db.update_event_intel(lead_id, extracted['event_name'], extracted.get('event_date'))
    if extracted.get('phones'):
        async with db.connect() as conn:
            await conn.execute('UPDATE leads SET phone=COALESCE(?, phone) WHERE id=?',
                               (extracted['phones'][0], lead_id))
            await conn.commit()

    await db.log_activity(lead_id, None, 'enrichment',
                          f'Enriched from {url}',
                          metadata=extracted)

    return extracted
