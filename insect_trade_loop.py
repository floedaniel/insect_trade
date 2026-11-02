#!/usr/bin/env python3
"""
insect_trade_loop.py  --  Phase 1: Shop Discovery
==================================================
Searches the web with generic multilingual queries to discover websites
that sell live insects. Uses both Tavily AND DuckDuckGo (different indexes,
double the coverage at zero cost). Saves unique shop domains to
discovered_shops.csv / discovered_shops.xlsx.

Phase 2 (scraping each discovered shop for species) comes later.
"""

# ==============================================================================
# SETTINGS
# ==============================================================================

from life_state import (TABLES_DIR, EPPO_DB as PROJECT_EPPO_DB,
                        classify_life_state, append_csv as _append_csv)

OUTPUT_CSV  = TABLES_DIR / "discovered_shops.csv"
OUTPUT_XLSX = TABLES_DIR / "discovered_shops.xlsx"

DELAY_TAVILY = 1.5   # seconds between Tavily queries
DELAY_DDG    = 4.0   # seconds between DuckDuckGo queries (stricter rate limit)
MAX_RESULTS  = 50    # results per query per engine

DISCOVERY_QUERIES = [
    # English - generic shop
    "live insects for sale online shop",
    "live insects buy online store",
    "exotic live insects breeder shop",
    "insect breeder shop online",
    "live invertebrates insects for sale",
    # English - life stages
    "live insect larvae for sale",
    "live insect pupae for sale",
    "live insect eggs oothecae for sale",
    "live insect nymphs for sale",
    "live insect imago adults for sale",
    "live caterpillars chrysalis for sale",
    "live beetle larvae pupae for sale",
    "live butterfly pupae cocoons for sale",
    # German - generic shop
    "lebende Insekten kaufen Online-Shop",
    "Insektenzucht kaufen online",
    "Insekten Zuchter Versand online",
    # German - life stages
    "Insekten Larven lebend kaufen",
    "Insekten Puppen lebend kaufen",
    "Insekten Eier lebend kaufen",
    "Insekten Nymphen lebend kaufen",
    "Insekten Imago lebend kaufen",
    "Raupen Kokons lebend kaufen",
    # French - generic shop
    "insectes vivants achat boutique en ligne",
    "elevage insectes vente en ligne",
    # French - life stages
    "larves insectes vivants vente",
    "pupes nymphes insectes vivants achat",
    "oeufs insectes vivants vente",
    "chrysalides chenilles vivants achat",
    "imago insectes vivants vente",

    # Italian - generic shop
    "insetti vivi vendita online negozio",
    "allevamento insetti vendita online",
    # Italian - life stages
    "larve insetti vivi vendita",
    "pupe ninfe insetti vivi acquisto",
    "uova insetti vivi vendita",
    "bruchi crisalidi vivi vendita",
    # Spanish - generic shop
    "insectos vivos comprar tienda online",
    "criador insectos venta online",
    # Spanish - life stages
    "larvas insectos vivos venta",
    "pupas ninfas insectos vivos comprar",
    "huevos insectos vivos venta",
    "orugas crisalidas vivos comprar",

    # Japanese - generic shop
    "昆虫 生体 販売 通販 ショップ",
    "外国産昆虫 生体 販売 通販",
    "昆虫専門店 生体 通販",
    # Japanese - life stages
    "昆虫 幼虫 販売 通販",
    "昆虫 蛹 販売 通販",
    "昆虫 卵 販売 通販",
    "昆虫 成虫 販売 通販",
    # Chinese simplified - generic shop
    "昆虫 活体 出售 网店",
    "活体昆虫 出售 商店",
    # Chinese simplified - life stages
    "昆虫 幼虫 活体 出售",
    "昆虫 蛹 活体 购买",
    "昆虫 卵 活体 出售",
    "昆虫 成虫 活体 购买",
    # Traditional Chinese
    "昆蟲 活體 販售 網路商店",
    "昆蟲 幼蟲 蛹 活體 購買",
    # Korean - generic + stages
    "살아있는 곤충 판매 온라인",
    "곤충 생체 쇼핑몰",
    "곤충 유충 번데기 생체 판매",
    "곤충 알 성충 생체 구매",

    # Thai
    "แมลงมีชีวิต ขาย ออนไลน์",
    "ตัวอ่อน ดักแด้ แมลง ขาย",

    # Indonesian / Malay
    "serangga hidup jual beli online",
    "larva pupa serangga hidup dijual",

]

# ==============================================================================

import re
import csv
import time
import random
import atexit
import os
import pandas as pd
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()
_tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

try:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    _DDG_AVAILABLE = False
    print("Note: ddgs not installed -- run: pip install ddgs")

# Domains to always skip — generic marketplaces, social media, encyclopedias
_NOISE_DOMAINS = {
    # encyclopedias / databases
    "wikipedia.org", "wikimedia.org", "britannica.com", "iucnredlist.org",
    "inaturalist.org", "gbif.org", "bugguide.net", "researchgate.net",
    "jstor.org", "sciencedirect.com", "springer.com", "nature.com",
    # social / media
    "facebook.com", "instagram.com", "tiktok.com", "twitter.com",
    "x.com", "youtube.com", "reddit.com", "pinterest.com", "threads.net",
    "stock.adobe.com", "shutterstock.com", "alamy.com", "gettyimages.com",
    "blogspot.com", "wordpress.com",
    # large generic marketplaces — not specialist shops
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
    "amazon.co.jp", "amazon.es", "amazon.it", "amazon.com.au",
    "alibaba.com", "aliexpress.com", "1688.com",
    "taobao.com", "tmall.com", "jd.com", "goofish.com", "pinduoduo.com",
    "rakuten.co.jp", "rakuten.com", "mercari.com", "mercari.jp",
    "ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr", "ebay.it",
    "ebay.es", "ebay.nl", "ebay.com.au", "ebay.co.jp",
    "etsy.com", "wish.com", "lazada.com", "shopee.com",
    "marktplaats.nl", "gumtree.com", "olx.com", "allegro.pl",
    "morphmarket.com",
}

_COMMERCIAL_RE = re.compile(
    r'[€$£¥₩฿]'
    r'|\b\d+\s*(USD|EUR|GBP|JPY|CNY|KRW|AUD|CAD)\b'
    r'|\b(buy|shop|store|cart|order|checkout|kaufen|kopen|acheter|comprar'
    r'|acquistare|for sale|in stock|add to cart'
    r'|breeder|zuechter|eleveur|allevatore|criador'
    r'|versand|livraison|shipping|envio|spedizione'
    r'|販売|出售|판매)\b',
    re.IGNORECASE,
)

_INSECT_RE = re.compile(
    r'\b(insect|beetle|butterfly|moth|mantis|stick insect|walking stick'
    r'|grasshopper|cricket|dragonfly|cockroach|firefly|scarab|longhorn'
    r'|dynastes|hercules|atlas|goliath|stag beetle|rhinoceros beetle'
    r'|larva|larvae|pupa|pupae|chrysalis|cocoon|nymph|caterpillar'
    r'|phasmid|phasmatodea|lepidoptera|coleoptera|orthoptera|dictyoptera'
    r'|kafer|schmetterling|heuschrecke|stabheuschrecke|gottesanbeterin'
    r'|nashornkafer|hirschkafer|puppe|larve'
    r'|insecte|coleoptere|papillon|mante|phasme|chrysalide'
    r'|kever|vlinder|wandelende tak|sprinkhaan'
    r'|insetto|coleottero|farfalla|mantide'
    r'|escarabajo|mariposa|bicho palo'
    r'|カブトムシ|クワガタ|チョウ|甲虫|独角仙|锹甲|사슴벌레|장수풍뎅이'
    r'|昆虫|幼虫|蛹|ด้วง|แมลง|kumbang|serangga)\b',
    re.IGNORECASE,
)


def domain_of(url: str) -> str:
    try:
        d = url.split("/")[2].lower()
        return d[4:] if d.startswith("www.") else d
    except IndexError:
        return ""


def is_noise(domain: str) -> bool:
    return any(nd in domain for nd in _NOISE_DOMAINS)


def is_commercial(result: dict) -> bool:
    text = result.get("title", "") + " " + result.get("content", "")
    return bool(_COMMERCIAL_RE.search(text))


def is_insect_relevant(result: dict) -> bool:
    text = result.get("title", "") + " " + result.get("content", "")
    return bool(_INSECT_RE.search(text))


# ── Search engines ────────────────────────────────────────────────────────────

def search_tavily(query: str) -> list[dict]:
    try:
        resp = _tavily.search(
            query        = query,
            max_results  = MAX_RESULTS,
            search_depth = "basic",
        )
        return [{"title":   r.get("title", ""),
                 "content": r.get("content", ""),
                 "url":     r.get("url", ""),
                 "engine":  "tavily"}
                for r in resp.get("results", [])]
    except Exception as e:
        print(f"    [tavily error] {e}")
        return []


def search_ddg(query: str) -> list[dict]:
    try:
        with DDGS() as ddgs:
            hits = ddgs.text(query, max_results=MAX_RESULTS)
        return [{"title":   h.get("title", ""),
                 "content": h.get("body", ""),
                 "url":     h.get("href", ""),
                 "engine":  "ddg"}
                for h in (hits or [])]
    except Exception as e:
        print(f"    [ddg error] {e}")
        return []


# ── Output ────────────────────────────────────────────────────────────────────

CSV_FIELDS = ["domain", "url", "title", "snippet", "query", "engine", "date", "run_id", "life_state", "life_terms"]


def append_csv(rows: list):
    # label live / dead / check / unknown before writing (rows are shared with
    # all_rows, so the label also reaches the xlsx)
    for r in rows:
        if not r.get("life_state"):
            r["life_state"], r["life_terms"] = classify_life_state(
                r.get("title", ""), r.get("snippet", ""))
    _append_csv(OUTPUT_CSV, rows, CSV_FIELDS)


def save_xlsx(all_rows: list):
    if not all_rows:
        return
    df = pd.DataFrame(all_rows).drop_duplicates(subset=["domain"], keep="first")
    for c in CSV_FIELDS:
        if c not in df.columns:
            df[c] = ""
    df = df[CSV_FIELDS].sort_values("domain")

    wb = Workbook()
    ws = wb.active
    ws.title = "Discovered Shops"

    hfill = PatternFill("solid", start_color="1F4E79")
    hfont = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    for ci, h in enumerate(
        ["Domain", "URL", "Title", "Snippet", "Query", "Engine", "Date", "Run ID",
         "Life state", "Life terms"], 1
    ):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hfont
        cell.fill = hfill
        cell.alignment = Alignment(horizontal="center")

    rfont = Font(name="Arial", size=10)
    for ri, row in enumerate(df.itertuples(index=False), 2):
        for ci, val in enumerate(row, 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = rfont
            if ri % 2 == 0:
                cell.fill = PatternFill("solid", start_color="D6E4F0")

    for ci, w in enumerate([28, 55, 45, 60, 40, 10, 12, 14, 11, 30], 1):
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = w

    ws.freeze_panes = "A2"
    try:
        wb.save(OUTPUT_XLSX)
        print(f"  [saved {len(df)} unique URLs "
              f"({df['domain'].nunique()} domains) -> {OUTPUT_XLSX}]")
    except PermissionError:
        print(f"  [skipped save - close {OUTPUT_XLSX} first]")


# ── Dedup loader ──────────────────────────────────────────────────────────────

def load_existing() -> tuple[list, set]:
    path = Path(OUTPUT_CSV)
    if not path.exists():
        return [], set()
    df = pd.read_csv(path, dtype=str, on_bad_lines="skip").fillna("")
    for c in CSV_FIELDS:
        if c not in df.columns:
            df[c] = ""
    df = df[CSV_FIELDS]
    # drop blocked domains and keep only one row per domain
    df = df[~df["domain"].apply(is_noise)]
    df = df.drop_duplicates(subset=["domain"], keep="first")
    rows    = df.to_dict("records")
    for r in rows:   # label rows saved before life_state existed
        if not r.get("life_state"):
            r["life_state"], r["life_terms"] = classify_life_state(
                r.get("title", ""), r.get("snippet", ""))
    domains = set(df["domain"].tolist())
    print(f"Loaded   : {len(domains)} existing domains")
    return rows, domains


# ── Result processor ──────────────────────────────────────────────────────────

def process_results(results, query, seen_domains,
                    all_rows, today, run_id) -> int:
    new_this = 0
    for r in results:
        url    = r.get("url", "")
        domain = domain_of(url)
        if not url or not domain:
            continue
        if is_noise(domain):
            continue
        if domain in seen_domains:
            continue
        if not is_commercial(r):
            continue
        if not is_insect_relevant(r):
            continue
        seen_domains.add(domain)

        row = {
            "domain":  domain,
            "url":     url,
            "title":   r.get("title",   "")[:120],
            "snippet": r.get("content", "")[:200],
            "query":   query,
            "engine":  r.get("engine", ""),
            "date":    today,
            "run_id":  run_id,
        }
        all_rows.append(row)
        new_this += 1
        append_csv([row])
        print(f"  + [{r.get('engine','?')}] {domain}")
        print(f"    {r.get('title', '')[:70]}")
        print(f"    {url}")
    return new_this


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    run_id                    = datetime.now().strftime("%Y%m%d_%H%M")
    all_rows, seen_domains = load_existing()

    atexit.register(lambda: save_xlsx(all_rows))

    engines = ["tavily"] + (["ddg"] if _DDG_AVAILABLE else [])
    queries = DISCOVERY_QUERIES[:]
    random.shuffle(queries)
    qi        = 0
    total     = 0
    iteration = 0

    print(f"Queries  : {len(queries)}")
    print(f"Engines  : {', '.join(engines)}  (~{len(queries)*MAX_RESULTS*len(engines)} results/cycle)")
    print(f"Output   : {OUTPUT_XLSX}")
    print(f"Run ID   : {run_id}")
    print("Running forever -- press Stop to save and exit")
    print("-" * 60)

    while True:
        if qi >= len(queries):
            random.shuffle(queries)
            qi = 0
            print(f"\n--- full cycle done, reshuffling ---")
            print(f"--- {len(seen_domains)} unique domains so far ---\n")

        query     = queries[qi]
        qi       += 1
        iteration += 1
        today     = datetime.now().strftime("%Y-%m-%d")

        print(f"\n[{iteration}] {query}")

        t_results = search_tavily(query)
        new_t = process_results(t_results, query, seen_domains,
                                 all_rows, today, run_id)
        total += new_t
        print(f"  tavily: {len(t_results)} results -> {new_t} new")
        time.sleep(DELAY_TAVILY + random.uniform(0, 1))

        if _DDG_AVAILABLE:
            d_results = search_ddg(query)
            new_d = process_results(d_results, query, seen_domains,
                                     all_rows, today, run_id)
            total += new_d
            print(f"  ddg   : {len(d_results)} results -> {new_d} new")
            time.sleep(DELAY_DDG + random.uniform(0, 2))

        if total > 0:
            save_xlsx(all_rows)
        print(f"  [{total} total URLs | {len(seen_domains)} unique domains]")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nStopped by user.")
