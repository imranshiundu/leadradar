from __future__ import annotations

from app.ai import classify_lead
from app.config import get_settings
from app.db import Database
from app.models import LeadCreate, LeadStatus, SourceRunResult
from app.outreach.telegram import alert_lead
from app.sources.base import load_sources_config
from app.sources.rss import discover_from_rss
from app.sources.search import discover_businesses_from_search
from app.sources.seed_pages import discover_from_seed_page


async def _insert_classify_alert(db: Database, lead: LeadCreate) -> tuple[bool, int | None]:
    settings = get_settings()
    lead_id, inserted = await db.insert_lead(lead)
    if not inserted or not lead_id:
        return False, lead_id
    classification = await classify_lead(lead.model_dump())
    await db.update_classification(
        lead_id,
        classification.score,
        classification.summary,
        classification.reason,
        classification.draft_message,
    )
    row = await db.get_lead(lead_id)
    if row:
        threshold = settings.min_opportunity_score_for_alert if lead.opportunity_type in {'job', 'service_opportunity'} else settings.min_need_score_for_alert
        if int(row['need_score'] or 0) >= threshold:
            await alert_lead(dict(row))
            await db.set_status(lead_id, LeadStatus.ALERTED, 'alerted on Telegram')
    return True, lead_id


async def run_discovery_once(db: Database) -> list[SourceRunResult]:
    settings = get_settings()
    cfg = load_sources_config()
    results: list[SourceRunResult] = []
    total_inserted = 0
    blocked_domains = cfg.get('blocked_domains', [])

    for query_cfg in cfg.get('business_search_queries', []):
        if total_inserted >= settings.max_leads_per_run:
            break
        try:
            result, leads = await discover_businesses_from_search(query_cfg, blocked_domains)
            for lead in leads:
                if total_inserted >= settings.max_leads_per_run:
                    break
                inserted, _ = await _insert_classify_alert(db, lead)
                if inserted:
                    result.inserted += 1
                    total_inserted += 1
                else:
                    result.skipped += 1
            results.append(result)
        except Exception as exc:
            results.append(SourceRunResult(source=f"brave:{query_cfg.get('name', query_cfg.get('query', 'unknown'))}", errors=[str(exc)]))

    for page_cfg in cfg.get('seed_pages', []):
        if total_inserted >= settings.max_leads_per_run:
            break
        try:
            result, leads = await discover_from_seed_page(page_cfg)
            for lead in leads:
                inserted, _ = await _insert_classify_alert(db, lead)
                if inserted:
                    result.inserted += 1
                    total_inserted += 1
                else:
                    result.skipped += 1
            results.append(result)
        except Exception as exc:
            results.append(SourceRunResult(source=f"seed:{page_cfg.get('name', 'unknown')}", errors=[str(exc)]))

    for feed_cfg in cfg.get('rss_feeds', []):
        if total_inserted >= settings.max_leads_per_run:
            break
        try:
            result, leads = await discover_from_rss(feed_cfg)
            for lead in leads:
                if total_inserted >= settings.max_leads_per_run:
                    break
                inserted, _ = await _insert_classify_alert(db, lead)
                if inserted:
                    result.inserted += 1
                    total_inserted += 1
                else:
                    result.skipped += 1
            results.append(result)
        except Exception as exc:
            results.append(SourceRunResult(source=f"rss:{feed_cfg.get('name', 'unknown')}", errors=[str(exc)]))

    await db.add_event('discovery:run', {'results': [r.model_dump() for r in results]})
    return results
