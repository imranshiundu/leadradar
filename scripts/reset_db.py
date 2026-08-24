import asyncio
from app.db import Database

async def reset_db():
    db = Database()
    async with db.connect() as conn:
        await conn.execute('DELETE FROM leads')
        await conn.execute('DELETE FROM events_log')
        await conn.execute('DELETE FROM source_state')
        await conn.commit()
    print("Database reset successfully.")

if __name__ == "__main__":
    asyncio.run(reset_db())
