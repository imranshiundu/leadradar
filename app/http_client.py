from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import httpx
from app.config import get_settings
from app.safety import RobotsCache

_settings = get_settings()
_robots = RobotsCache(user_agent=_settings.user_agent)
_last_fetch: dict[str, float] = {}


def _origin(url: str) -> str:
    try:
        parsed = httpx.URL(url)
        return f'{parsed.scheme}://{parsed.host}'
    except Exception:
        return 'global'


async def polite_delay(url: str) -> None:
    delay = get_settings().crawl_delay_seconds
    origin = _origin(url)
    now = asyncio.get_running_loop().time()
    last = _last_fetch.get(origin, 0)
    wait = max(0, delay - (now - last))
    if wait:
        await asyncio.sleep(wait)
    _last_fetch[origin] = asyncio.get_running_loop().time()


@asynccontextmanager
async def client(timeout: float = 20.0):
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={'User-Agent': settings.user_agent, 'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8'},
    ) as c:
        yield c


async def fetch_text(url: str, respect_robots: bool = True, max_bytes: int = 500_000) -> str | None:
    settings = get_settings()
    if respect_robots and settings.respect_robots_txt and not _robots.allowed(url):
        return None
    await polite_delay(url)
    async with client() as c:
        resp = await c.get(url)
        resp.raise_for_status()
        data = resp.content[:max_bytes]
        encoding = resp.encoding or 'utf-8'
        return data.decode(encoding, errors='replace')
