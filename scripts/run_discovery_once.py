import asyncio
from app.db import Database
from app.services.discovery import run_discovery_once

async def main():
    db = Database()
    await db.init()
    results = await run_discovery_once(db)
    for result in results:
        print(result.model_dump())

if __name__ == '__main__':
    asyncio.run(main())
