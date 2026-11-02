"""Polite async HTTP fetching with robots.txt, per-host delay and a disk cache."""

from __future__ import annotations

import asyncio
import sqlite3
import time
import zlib
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import config


@dataclass
class Response:
    url: str
    status: int
    text: str | None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.text is not None


class Cache:
    """SQLite cache: url -> (status, fetched_at, zlib(body))."""

    def __init__(self, path=config.CACHE_DB):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, "
                        "status INTEGER, fetched REAL, body BLOB)")
        self.db.commit()

    def get(self, url: str, max_age_days: float) -> Response | None:
        row = self.db.execute("SELECT status, fetched, body FROM pages WHERE url=?",
                              (url,)).fetchone()
        if not row or time.time() - row[1] > max_age_days * 86400:
            return None
        body = zlib.decompress(row[2]).decode("utf-8", "replace") if row[2] else None
        return Response(url, row[0], body, from_cache=True)

    def put(self, r: Response) -> None:
        body = zlib.compress(r.text.encode("utf-8")) if r.text is not None else None
        self.db.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?)",
                        (r.url, r.status, time.time(), body))
        self.db.commit()


class Fetcher:
    def __init__(self, cache: Cache | None = None,
                 max_age_days: float = config.CACHE_MAX_AGE_DAYS):
        self.cache = cache or Cache()
        self.max_age = max_age_days
        self.client = httpx.AsyncClient(
            http2=True, follow_redirects=True, timeout=config.TIMEOUT,
            headers={"User-Agent": config.USER_AGENT,
                     "Accept-Language": "en,nb;q=0.9,de;q=0.8,*;q=0.5"},
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._delay: dict[str, float] = {}
        self.requests = 0

    async def close(self):
        await self.client.aclose()

    # -- robots ---------------------------------------------------------------
    async def robots(self, host: str) -> RobotFileParser | None:
        if host in self._robots:
            return self._robots[host]
        r = await self._get(f"https://{host}/robots.txt", check_robots=False)
        rp = None
        if r.status == 200 and r.text:
            rp = RobotFileParser()
            rp.parse(r.text.splitlines())
            cd = rp.crawl_delay(config.UA_TOKEN) or rp.crawl_delay("*")
            if cd:
                self._delay[host] = max(config.MIN_DELAY,
                                        min(float(cd), config.MAX_CRAWL_DELAY))
        self._robots[host] = rp
        return rp

    async def allowed(self, url: str) -> bool:
        host = urlsplit(url).netloc
        rp = await self.robots(host)
        if rp is None:
            return True
        return rp.can_fetch(config.UA_TOKEN, url)

    def sitemaps_from_robots(self, host: str) -> list[str]:
        rp = self._robots.get(host)
        return list(rp.site_maps() or []) if rp else []

    # -- fetching -----------------------------------------------------------
    async def get(self, url: str) -> Response:
        if not await self.allowed(url):
            return Response(url, -1, None)          # -1 = disallowed by robots
        return await self._get(url)

    async def _get(self, url: str, check_robots: bool = True) -> Response:
        cached = self.cache.get(url, self.max_age)
        if cached:
            return cached
        host = urlsplit(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._delay.get(host, config.MIN_DELAY)
            wait = self._last.get(host, 0) + delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            resp = Response(url, 0, None)
            for attempt in range(3):
                try:
                    r = await self.client.get(url)
                    self.requests += 1
                    text = r.text if r.status_code == 200 else None
                    resp = Response(url, r.status_code, text)
                    if r.status_code in (429, 503) and attempt < 2:
                        ra = r.headers.get("retry-after", "")
                        await asyncio.sleep(min(float(ra) if ra.isdigit() else 10 * (attempt + 1), 60))
                        continue
                    break
                except (httpx.HTTPError, UnicodeDecodeError):
                    resp = Response(url, 0, None)
                    await asyncio.sleep(3 * (attempt + 1))
            self._last[host] = time.monotonic()
        if resp.status != 0:                         # don't cache network failures
            self.cache.put(resp)
        return resp
