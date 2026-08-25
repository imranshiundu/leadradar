"""Daily VPS automation: mine Gmail -> rescore -> SMTP-verify new addresses.
Runs entirely on the server via systemd timer. Sources blocked on datacenter
IPs (Bing/Eventbrite) are skipped here — the laptop crawler covers those."""
import asyncio
import sys

sys.path.insert(0, '/opt/leadradar')

from app.config import get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.deepverify import smtp_probe  # noqa: E402
from app.host_finder import mine_gmail  # noqa: E402
from app.relevance import relevance_score  # noqa: E402


async def main(verify_cap: int = 60) -> None:
    db = Database(get_settings().database_path)
    await db.init()

    rows = [dict(r) for r in await db.list_leads(limit=10000)]
    existing = {(r.get('email') or '').lower() for r in rows}

    mined = await mine_gmail(db, existing)
    print(f'[1/3] gmail mining: {mined} new hosts')

    for r in await db.list_leads(limit=10000):
        d = dict(r)
        s = relevance_score(d.get('email'), d.get('name'), d.get('raw_text'),
                            d.get('website_url'), d.get('event_name'))
        await db.set_relevance(int(d['id']), s)
    confident = sum(1 for r in await db.list_leads(limit=10000)
                    if (dict(r).get('relevance') or 0) >= 5)
    print(f'[2/3] rescored — {confident} confident hosts')

    checked = {c['email'] for c in await db.all_smtp_checks()}
    probed = valid = 0
    sem = asyncio.Semaphore(8)

    async def probe(e: str):
        nonlocal valid
        async with sem:
            res = await asyncio.to_thread(smtp_probe, e)
            if res['status'] == 'valid':
                valid += 1
            await db.set_smtp_result(e, res['status'])

    todo = []
    for r in await db.list_leads(limit=10000):
        e = (dict(r).get('email') or '').strip().lower()
        if e and e not in checked:
            todo.append(e)
        if len(todo) >= verify_cap:
            break
    await asyncio.gather(*(probe(e) for e in todo))
    stats = await db.smtp_stats()
    print(f'[3/3] smtp: probed {len(todo)} today ({valid} valid) — total checked '
          f'{stats["checked"]}/{stats["emails_with_address"]}, valid={stats.get("valid", 0)}')


if __name__ == '__main__':
    asyncio.run(main())
