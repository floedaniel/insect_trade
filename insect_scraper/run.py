"""Command line: discover shops, crawl them, write the xlsx report.

  py -3 -m insect_scraper discover [--max-queries N]
  py -3 -m insect_scraper crawl [--states live,unknown,check] [--workers 12] [--max-shops N] [--refresh]
  py -3 -m insect_scraper report
  py -3 -m insect_scraper all          (discover + crawl + report)
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
    shops["domain"] = shops["domain"].str.lower().str.removeprefix("www.")
    shops = shops.drop_duplicates("domain")
    if states:
        shops = shops[shops["life_state"].isin(states) | (shops["life_state"] == "")]
    return shops.reset_index(drop=True)


async def crawl_all(shops: pd.DataFrame, workers: int, refresh: bool) -> None:
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
            try:
                res = await crawl_shop(f, idx, r.domain, getattr(r, "url", "") or "")
                out = res.to_json()
            except Exception as e:  # keep going, record the error
                out = {"domain": r.domain, "status": f"error: {type(e).__name__}: {e}"[:300],
                       "products": [], "new_names": [], "shop_info": {}}
            (RESULTS_DIR / f"{r.domain}.json").write_text(json.dumps(out), encoding="utf-8")
            done += 1
            n = len(out.get("products", []))
            reg = len({p["species"] for p in out.get("products", []) if p.get("regulated")})
            print(f"[{done}/{len(todo)}] {r.domain:<38} {out.get('status',''):<12.12} "
                  f"{out.get('method',''):<20.20} hits {n:>4}  regulated {reg}  "
                  f"({(time.time()-t0)/60:.0f} min, {f.requests} requests)", flush=True)

    await asyncio.gather(*(one(r) for r in todo))
    await f.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="insect_scraper")
    ap.add_argument("command", choices=["discover", "crawl", "report", "all"])
    ap.add_argument("--states", default="live,unknown,check",
                    help="shop life_state values to crawl (comma list)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-shops", type=int, default=0)
    ap.add_argument("--max-queries", type=int, default=400)
    ap.add_argument("--refresh", action="store_true", help="re-crawl shops already done")
    a = ap.parse_args(argv)

    if a.command in ("discover", "all"):
        from .discover import discover
        discover(max_queries=a.max_queries)
    if a.command in ("crawl", "all"):
        shops = load_shops([s for s in a.states.split(",") if s])
        if a.max_shops:
            shops = shops.head(a.max_shops)
        asyncio.run(crawl_all(shops, a.workers, a.refresh))
    if a.command in ("report", "crawl", "all"):
        from .report import write_report
        write_report(load_shops([]))


if __name__ == "__main__":
    main()
