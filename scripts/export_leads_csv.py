import asyncio
import csv
import sys
from app.db import Database

async def main(path='leads_export.csv'):
    db = Database()
    await db.init()
    rows = await db.list_leads(limit=10000)
    fieldnames = list(rows[0].keys()) if rows else ['id','name','email','status','need_score']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    print(f'Exported {len(rows)} leads to {path}')

if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'leads_export.csv'))
