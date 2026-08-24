from __future__ import annotations

import feedparser
from app.http_client import fetch_text
from app.models import LeadCreate, SourceRunResult
from app.safety import extract_emails, fingerprint, normalize_text


async def discover_from_rss(feed_config: dict) -> tuple[SourceRunResult, list[LeadCreate]]:
    name = feed_config.get('name', feed_config['url'])
    url = feed_config['url']
    result = SourceRunResult(source=f'rss:{name}')
    leads: list[LeadCreate] = []
    try:
        xml = await fetch_text(url, respect_robots=False, max_bytes=1_000_000)
    except Exception as exc:
        result.errors.append(str(exc))
        return result, leads
    if not xml:
        return result, leads
    parsed = feedparser.parse(xml)
    for entry in parsed.entries:
        result.found += 1
        title = normalize_text(getattr(entry, 'title', 'Untitled opportunity'))
        link = getattr(entry, 'link', None)
        summary = normalize_text(getattr(entry, 'summary', ''))
        emails = extract_emails(summary)
        lead = LeadCreate(
            source=result.source,
            source_url=link,
            name=title[:180],
            business_type='job/opportunity',
            city=None,
            website_url=link,
            email=emails[0] if emails else None,
            opportunity_type=feed_config.get('opportunity_type', 'job'),
            raw_text=f'{title}\n{summary}'[:5000],
            fingerprint=fingerprint(title, link, name),
        )
        leads.append(lead)
    return result, leads
