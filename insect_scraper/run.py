"""Command line: discover shops, crawl them, write the xlsx report.

  py -3 -m insect_scraper discover [--max-queries N]
  py -3 -m insect_scraper crawl [--states live,unknown,check] [--workers 12] [--max-shops N] [--refresh]
  py -3 -m insect_scraper crawl --retry "ok (time limit),unreachable" --budget-min 240 --max-pages 10000
      re-crawl shops whose saved status starts with one of these (old results kept in
      2_processed_data/scrape_results/_previous/); cached pages make restarts fast
  py -3 -m insect_scraper report
  py -3 -m insect_scraper newnames     (resolve new Latin names found on shop pages)
  py -3 -m insect_scraper all          (discover + crawl + report)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time

import pandas as pd

from . import config
from .crawl import crawl_shop
from .fetch import Fetcher
from .names import build_index

RESULTS_DIR = config.DATA_DIR / "scrape_results"
RESULTS_DIR.mkdir(exist_ok=True)


def load_shops(states: list[str]) -> pd.DataFrame:
    frames = []
    if config.DISCOVERED_SHOPS.exists():
        d = pd.read_csv(config.DISCOVERED_SHOPS, dtype=str).fillna("")
        d["source"] = "shop discovery (insect_trade_loop.py)"
        frames.append(d)
    if config.SHOPS_MASTER.exists():
        frames.append(pd.read_csv(config.SHOPS_MASTER, dtype=str).fillna(""))
    shops = pd.concat(frames, ignore_index=True)
    # bare host only: no query strings, paths or ports (they also break file names)
    shops["domain"] = (shops["domain"].str.lower().str.strip()
                       .str.replace(r"^https?://", "", regex=True)
                       .str.replace(r"[/?#:].*$", "", regex=True)
                       .str.removeprefix("www."))
    shops = shops[shops["domain"].str.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}")]
    shops = shops.drop_duplicates("domain")
    if states:
        shops = shops[shops["life_state"].isin(states) | (shops["life_state"] == "")]
    return shops.reset_index(drop=True)


async def crawl_all(shops: pd.DataFrame, workers: int, refresh: bool,
                    budget_s: float = config.SHOP_TIME_BUDGET) -> None:
    idx = build_index()
    f = Fetcher()
    sem = asyncio.Semaphore(workers)
    todo = [r for r in shops.itertuples()
            if refresh or not (RESULTS_DIR / f"{r.domain}.json").exists()]
    print(f"Shops: {len(shops)} | to crawl: {len(todo)} | workers: {workers}")
    t0, done = time.time(), 0

    async def one(r):
        nonlocal done
        async with sem:
            # several crawl processes may run at once: a lock file per shop
            # makes sure one shop is never crawled by two processes together
            lock = RESULTS_DIR / f"{r.domain}.lock"
            if (RESULTS_DIR / f"{r.domain}.json").exists() and not refresh:
                return
            if lock.exists() and time.time() - lock.stat().st_mtime < 6 * 3600:
                return
            lock.write_text(str(time.time()), encoding="utf-8")
            try:
                res = await crawl_shop(f, idx, r.domain, getattr(r, "url", "") or "", budget_s=budget_s)
                out = res.to_json()
            except Exception as e:  # keep going, record the error
                out = {"domain": r.domain, "status": f"error: {type(e).__name__}: {e}"[:300],
                       "products": [], "new_names": [], "shop_info": {}}
            (RESULTS_DIR / f"{r.domain}.json").write_text(json.dumps(out), encoding="utf-8")
            lock.unlink(missing_ok=True)
            done += 1
            # refresh the xlsx every 50 shops so results can be followed live
            if done % 50 == 0:
                try:
                    from .report import write_report
                    write_report(load_shops([]))
                except Exception as e:
                    print(f"  (interim report failed: {e})")
            n = len(out.get("products", []))
            reg = len({p["species"] for p in out.get("products", []) if p.get("regulated")})
            print(f"[{done}/{len(todo)}] {r.domain:<38} {out.get('status',''):<12.12} "
                  f"{out.get('method',''):<20.20} hits {n:>4}  regulated {reg}  "
                  f"({(time.time()-t0)/60:.0f} min, {f.requests} requests)", flush=True)

    async def heartbeat():
        # one line a minute, so a stall is visible in the log
        while True:
            await asyncio.sleep(60)
            active = len(list(RESULTS_DIR.glob("*.lock")))
            print(f"  .. {time.strftime('%H:%M')} done {done}/{len(todo)} | {f.requests} requests | "
                  f"{active} shops in progress (all jobs)", flush=True)

    hb = asyncio.create_task(heartbeat())
    await asyncio.gather(*(one(r) for r in todo))
    hb.cancel()
    await f.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="insect_scraper")
    ap.add_argument("command", choices=["discover", "crawl", "report", "all", "newnames"])
    ap.add_argument("--states", default="live,unknown,check",
                    help="shop life_state values to crawl (comma list)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-shops", type=int, default=0)
    ap.add_argument("--max-queries", type=int, default=400)
    ap.add_argument("--refresh", action="store_true", help="re-crawl shops already done")
    ap.add_argument("--retry", default="", help="comma list of status prefixes to re-crawl, e.g. \"ok (time limit),unreachable\"")
    ap.add_argument("--budget-min", type=float, default=config.SHOP_TIME_BUDGET / 60,
                    help="time limit per shop in minutes")
    ap.add_argument("--max-pages", type=int, default=config.MAX_PRODUCT_PAGES,
                    help="product pages per shop (shops without a catalogue API)")
    a = ap.parse_args(argv)

    if a.command == "newnames":
        from .newnames import resolve
        resolve()
        return
    if a.command in ("discover", "all"):
        from .discover import discover
        discover(max_queries=a.max_queries)
    if a.command in ("crawl", "all"):
        config.MAX_PRODUCT_PAGES = a.max_pages
        if a.retry:
            prefixes = tuple(x.strip() for x in a.retry.split(",") if x.strip())
            prev = RESULTS_DIR / "_previous"; prev.mkdir(exist_ok=True)
            again = []
            for f in RESULTS_DIR.glob("*.json"):
                if json.loads(f.read_text(encoding="utf-8")).get("status", "").startswith(prefixes):
                    again.append(f.stem)
                    shutil.move(str(f), prev / f.name)
            shops = load_shops([])
            shops = shops[shops["domain"].isin(again)].reset_index(drop=True)
            print(f"Retry: {len(again)} shops with status {prefixes}")
        else:
            shops = load_shops([s for s in a.states.split(",") if s])
        if a.max_shops:
            shops = shops.head(a.max_shops)
        asyncio.run(crawl_all(shops, a.workers, a.refresh, a.budget_min * 60))
    if a.command in ("report", "crawl", "all"):
        from .report import write_report
        write_report(load_shops([]))


if __name__ == "__main__":
    main()
