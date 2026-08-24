from __future__ import annotations

import httpx
from app.config import get_settings
from app.models import LeadCreate, SourceRunResult
from app.safety import extract_emails, extract_phones, fingerprint, has_blocked_domain, is_social_url, normalize_text


def _infer_website_and_social(result_url: str) -> tuple[str | None, str | None]:
    if is_social_url(result_url):
        return None, result_url
    return result_url, None


async def brave_search(query: str, count: int | None = None) -> list[dict]:
    settings = get_settings()
    if not settings.brave_search_api_key:
        return []
    params = {'q': query, 'count': count or settings.search_max_results, 'safesearch': 'moderate'}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            'https://api.search.brave.com/res/v1/web/search',
            params=params,
            headers={'Accept': 'application/json', 'X-Subscription-Token': settings.brave_search_api_key},
        )
        resp.raise_for_status()
        payload = resp.json()
    return payload.get('web', {}).get('results', [])


async def discover_businesses_from_search(query_config: dict, blocked_domains: list[str]) -> tuple[SourceRunResult, list[LeadCreate]]:
    query = query_config['query']
    source_name = query_config.get('name', query)
    result = SourceRunResult(source=f'brave:{source_name}')
    leads: list[LeadCreate] = []
    rows = await brave_search(query)
    for item in rows:
        result.found += 1
        url = item.get('url')
        if not url or has_blocked_domain(url, blocked_domains):
            result.skipped += 1
            continue
        title = normalize_text(item.get('title')) or 'Unnamed business'
        description = normalize_text(item.get('description'))
        website_url, social_url = _infer_website_and_social(url)
        raw_text = f'{title}\n{description}\n{url}'
        emails = extract_emails(raw_text)
        phones = extract_phones(raw_text)
        lead = LeadCreate(
            source=result.source,
            source_url=url,
            name=title[:180],
            business_type=query_config.get('business_type'),
            city=query_config.get('city'),
            website_url=website_url,
            email=emails[0] if emails else None,
            phone=phones[0] if phones else None,
            social_url=social_url,
            opportunity_type='website_lead',
            raw_text=raw_text[:5000],
            fingerprint=fingerprint(title, url, query_config.get('city')),
        )
        leads.append(lead)
    return result, leads
