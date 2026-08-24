from __future__ import annotations

from bs4 import BeautifulSoup
from app.http_client import fetch_text
from app.models import LeadCreate, SourceRunResult
from app.safety import extract_emails, extract_phones, extract_urls, fingerprint, is_social_url, normalize_text


async def discover_from_seed_page(page_config: dict) -> tuple[SourceRunResult, list[LeadCreate]]:
    name = page_config.get('name', page_config['url'])
    url = page_config['url']
    result = SourceRunResult(source=f'seed:{name}')
    leads: list[LeadCreate] = []
    try:
        html = await fetch_text(url)
    except Exception as exc:
        result.errors.append(str(exc))
        return result, leads
    if not html:
        return result, leads
    soup = BeautifulSoup(html, 'html.parser')
    title = normalize_text(soup.title.text if soup.title else name)
    text = normalize_text(soup.get_text(' ', strip=True))[:5000]
    urls = extract_urls(html)
    social = next((u for u in urls if is_social_url(u)), None)
    emails = extract_emails(text + ' ' + html)
    phones = extract_phones(text)
    lead = LeadCreate(
        source=result.source,
        source_url=url,
        name=title[:180],
        business_type=page_config.get('business_type'),
        city=page_config.get('city'),
        website_url=url,
        email=emails[0] if emails else None,
        phone=phones[0] if phones else None,
        social_url=social,
        opportunity_type='website_lead',
        raw_text=text,
        fingerprint=fingerprint(title, url),
    )
    result.found = 1
    leads.append(lead)
    return result, leads
