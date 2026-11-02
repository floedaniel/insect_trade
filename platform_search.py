#!/usr/bin/env python3
"""
platform_search.py
==================
Searches trading platforms directly for live insect species from the EPPO database.
No API keys, no paid services — scrapes platform search pages with requests + lxml.

Platforms: eBay (8 country stores), MorphMarket, Marktplaats, Gumtree, Etsy,
           AliExpress, JD.com, Taobao, Xianyu/Goofish, 1688.com.

Chinese platforms (Taobao, Xianyu, AliExpress, 1688) require Playwright:
    pip install playwright && playwright install chromium
"""

# ==============================================================================
# SETTINGS
# ==============================================================================

from life_state import (TABLES_DIR, EPPO_DB as PROJECT_EPPO_DB,
                        classify_life_state, append_csv as _append_csv)

EPPO_DB = str(PROJECT_EPPO_DB)

OUTPUT_CSV  = TABLES_DIR / "platform_results.csv"
OUTPUT_XLSX = TABLES_DIR / "platform_results.xlsx"

TEST_SPECIES = [ ]
TEST_SAMPLE  = None   # used if TEST_SPECIES is empty; None = full EPPO run

DELAY = 3.0   # seconds between requests

INSECT_ORDER_IDS = [
    59211, 57850, 61290, 58830, 58226,
    59001, 61179, 61095, 58185, 61506,
]

# ==============================================================================

import re
import csv
import time
import random
import sqlite3
import atexit
import requests
import pandas as pd
from lxml import html as lx
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_PRICE_RE = re.compile(
    r'[€$£¥₩฿]\s*[\d\.,]+|[\d\.,]+\s*(?:USD|EUR|GBP|JPY|AUD|CAD)\b',
    re.IGNORECASE,
)


# ── EPPO loader ───────────────────────────────────────────────────────────────

def load_eppo_species(db_path: str) -> list[str]:
    placeholders = ",".join("?" * len(INSECT_ORDER_IDS))
    sql = f"""
    WITH RECURSIVE descendants(codeid) AS (
        SELECT codeid FROM t_codes WHERE codeid IN ({placeholders})
        UNION ALL
        SELECT l.codeid FROM t_links l
        JOIN descendants d ON l.codeid_parent = d.codeid
        WHERE l.status = 'A'
    )
    SELECT DISTINCT n.fullname
    FROM descendants d
    JOIN t_codes c ON d.codeid = c.codeid
    JOIN t_names n ON c.codeid = n.codeid
    WHERE c.dtcode='GAI' AND n.codelang='la' AND n.preferred=1 AND n.status='A'
    ORDER BY n.fullname
    """
    con = sqlite3.connect(db_path)
    rows = con.execute(sql, INSECT_ORDER_IDS).fetchall()
    con.close()
    return [n for (n,) in rows
            if not n.endswith((" sp.", " spp.")) and len(n.split()) >= 2]


# ── Platform scrapers ─────────────────────────────────────────────────────────

def _get(url, params=None) -> lx.HtmlElement | None:
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        return lx.fromstring(r.content)
    except Exception as e:
        print(f"    [error] {e}")
        return None


def _relevant(title: str, url: str, species: str) -> bool:
    """Return True only if the genus name appears in the title or URL."""
    genus = species.split()[0].lower()
    return genus in (title + " " + url).lower()


def _pw_get(url: str, wait_selector: str = None, locale: str = "en-US") -> lx.HtmlElement | None:
    """Fetch page via headless Chromium for JavaScript-rendered sites."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("    [skip] playwright not installed — run: pip install playwright && playwright install chromium")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(user_agent=_HEADERS["User-Agent"], locale=locale)
            page = ctx.new_page()
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            content = page.content()
            browser.close()
            return lx.fromstring(content.encode())
    except Exception as e:
        print(f"    [error playwright] {e}")
        return None


def scrape_ebay(species: str, domain: str = "ebay.com",
                extra: str = "live") -> list[dict]:
    from urllib.parse import urlencode
    url = (f"https://www.{domain}/sch/i.html?"
           + urlencode({"_nkw": f'"{species}" {extra}', "_sacat": 0}))
    tree = _pw_get(url, wait_selector=".s-item__title")
    if tree is None:
        return []
    results = []
    for item in tree.xpath('//li[contains(@class,"s-item")]'):
        title = "".join(item.xpath('.//span[@class="s-item__title"]//text()')).strip()
        url   = "".join(item.xpath('.//a[@class="s-item__link"]/@href')).split("?")[0]
        price = "".join(item.xpath('.//*[contains(@class,"s-item__price")]//text()')).strip()
        loc   = "".join(item.xpath('.//*[contains(@class,"s-item__location")]//text()')).strip()
        if not url or "ebay.com/itm" not in url and "ebay." not in url:
            continue
        if title.lower() in ("shop on ebay", ""):
            continue
        if not _relevant(title, url, species):
            continue
        results.append({
            "title":   title,
            "url":     url,
            "price":   price,
            "snippet": loc,
        })
    return results


def scrape_morphmarket(species: str) -> list[dict]:
    tree = _get(
        "https://www.morphmarket.com/us/c/invertebrates/",
        params={"q": f'"{species}"'},
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@class,"animal") or contains(@href,"/us/c/")]'):
        url   = a.get("href", "")
        if not url.startswith("http"):
            url = "https://www.morphmarket.com" + url
        title = "".join(a.xpath('.//text()')).strip()
        price = "".join(a.xpath('.//*[contains(@class,"price")]//text()')).strip()
        if species.split()[0].lower() not in title.lower() and species.lower() not in url.lower():
            continue
        if title:
            results.append({"title": title, "url": url, "price": price, "snippet": ""})
    return results


def scrape_marktplaats(species: str) -> list[dict]:
    tree = _get(
        f"https://www.marktplaats.nl/q/%22{species.replace(' ', '+')}%22/")
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@class,"mp-listing")]'):
        url   = a.get("href", "")
        if not url.startswith("http"):
            url = "https://www.marktplaats.nl" + url
        title = "".join(a.xpath('.//h3//text() | .//span[contains(@class,"description")]//text()')).strip()
        price = "".join(a.xpath('.//*[contains(@class,"price")]//text()')).strip()
        if title and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": price, "snippet": ""})
    return results[:10]


def scrape_gumtree(species: str) -> list[dict]:
    tree = _get(
        "https://www.gumtree.com/search",
        params={"search_query": f'"{species}" live', "search_category": "all"},
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@class,"listing-link")]'):
        url   = a.get("href", "")
        if not url.startswith("http"):
            url = "https://www.gumtree.com" + url
        title = "".join(a.xpath('.//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": "", "snippet": ""})
    return results[:10]


def scrape_etsy(species: str) -> list[dict]:
    tree = _get(
        "https://www.etsy.com/search",
        params={"q": f'"{species}" live insect'},
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@href,"/listing/")]'):
        url   = a.get("href", "").split("?")[0]
        title = "".join(a.xpath('.//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": "", "snippet": ""})
    # dedup by url
    seen = set()
    out  = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out[:10]


def scrape_aliexpress(species: str) -> list[dict]:
    from urllib.parse import quote as _q
    q = _q(f'"{species}" live insect')
    tree = _pw_get(
        f"https://www.aliexpress.com/wholesale?SearchText={q}",
        wait_selector="[class*='product-snippet']",
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@href,"/item/") or contains(@href,"aliexpress.com/i/")]'):
        url   = a.get("href", "").split("?")[0]
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = "https://www.aliexpress.com" + url
        title = "".join(a.xpath('.//text()')).strip()
        price = "".join(a.xpath('./ancestor-or-self::*[1]//*[contains(@class,"price")]//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": price, "snippet": ""})
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"]); out.append(r)
    return out[:10]


def scrape_jd(species: str) -> list[dict]:
    """JD.com (京东) — partially server-rendered, try plain requests."""
    tree = _get("https://search.jd.com/Search", params={"keyword": f'"{species}" 活体', "enc": "utf-8"})
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@href,"item.jd.com") or contains(@href,"/product/")]'):
        url = a.get("href", "")
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = "https://www.jd.com" + url
        title = "".join(a.xpath('.//text()')).strip()
        price = "".join(a.xpath('./ancestor::li[1]//*[contains(@class,"price")]//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": price, "snippet": ""})
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"]); out.append(r)
    return out[:10]


def scrape_taobao(species: str) -> list[dict]:
    """Taobao (淘宝) — requires Playwright."""
    from urllib.parse import quote as _q
    q = _q(f'"{species}" 活体')
    tree = _pw_get(
        f"https://s.taobao.com/search?q={q}",
        wait_selector=".items",
        locale="zh-CN",
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@href,"item.taobao.com") or contains(@href,"taobao.com/item")]'):
        url   = a.get("href", "").split("?")[0]
        if url.startswith("//"):
            url = "https:" + url
        title = "".join(a.xpath('.//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": "", "snippet": ""})
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"]); out.append(r)
    return out[:10]


def scrape_xianyu(species: str) -> list[dict]:
    """Xianyu / Goofish (闲鱼) — second-hand, requires Playwright."""
    from urllib.parse import quote as _q
    q = _q(f'"{species}" 活体')
    tree = _pw_get(
        f"https://www.goofish.com/search?q={q}",
        wait_selector="[class*='item']",
        locale="zh-CN",
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@href,"/item/") or contains(@href,"goofish.com")]'):
        url = a.get("href", "")
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith("http"):
            url = "https://www.goofish.com" + url
        title = "".join(a.xpath('.//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": "", "snippet": ""})
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"]); out.append(r)
    return out[:10]


def scrape_1688(species: str) -> list[dict]:
    """1688.com (B2B wholesale) — requires Playwright."""
    from urllib.parse import quote as _q
    q = _q(f'"{species}" 活虫')
    tree = _pw_get(
        f"https://s.1688.com/selloffer/offerlist.htm?keywords={q}",
        wait_selector=".offer-list",
        locale="zh-CN",
    )
    if tree is None:
        return []
    results = []
    for a in tree.xpath('//a[contains(@href,"1688.com/offer") or contains(@href,"detail.1688.com")]'):
        url   = a.get("href", "").split("?")[0]
        if url.startswith("//"):
            url = "https:" + url
        title = "".join(a.xpath('.//text()')).strip()
        price = "".join(a.xpath('./ancestor::*[contains(@class,"offer")][1]//*[contains(@class,"price")]//text()')).strip()
        if len(title) > 5 and _relevant(title, url, species):
            results.append({"title": title, "url": url, "price": price, "snippet": ""})
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"]); out.append(r)
    return out[:10]


# Platform dispatch table
PLATFORMS = [
    {"name": "eBay.com",    "fn": lambda s: scrape_ebay(s, "ebay.com",    "live")},
    {"name": "eBay.co.uk",  "fn": lambda s: scrape_ebay(s, "ebay.co.uk",  "live")},
    {"name": "eBay.de",     "fn": lambda s: scrape_ebay(s, "ebay.de",     "kaufen lebend")},
    {"name": "eBay.fr",     "fn": lambda s: scrape_ebay(s, "ebay.fr",     "vivant")},
    {"name": "eBay.it",     "fn": lambda s: scrape_ebay(s, "ebay.it",     "vivo")},
    {"name": "eBay.es",     "fn": lambda s: scrape_ebay(s, "ebay.es",     "vivo")},
    {"name": "eBay.nl",     "fn": lambda s: scrape_ebay(s, "ebay.nl",     "levend")},
    {"name": "eBay.com.au", "fn": lambda s: scrape_ebay(s, "ebay.com.au", "live")},
    {"name": "MorphMarket", "fn": scrape_morphmarket},
    {"name": "Marktplaats", "fn": scrape_marktplaats},
    {"name": "Gumtree",     "fn": scrape_gumtree},
    {"name": "Etsy",        "fn": scrape_etsy},
    {"name": "AliExpress",  "fn": scrape_aliexpress},
    {"name": "JD.com",      "fn": scrape_jd},
    {"name": "Taobao",      "fn": scrape_taobao},
    {"name": "Xianyu",      "fn": scrape_xianyu},
    {"name": "1688.com",    "fn": scrape_1688},
]

# ── Output ────────────────────────────────────────────────────────────────────

CSV_FIELDS = ["species", "platform", "url", "title", "price", "snippet", "date", "run_id", "life_state", "life_terms"]


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
    df = pd.DataFrame(all_rows).drop_duplicates(subset=["species", "url"])
    for c in CSV_FIELDS:
        if c not in df.columns:
            df[c] = ""
    df = df[CSV_FIELDS]

    wb = Workbook()
    ws = wb.active
    ws.title = "Platform Results"

    hfill = PatternFill("solid", start_color="1F4E79")
    hfont = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    for ci, h in enumerate(
        ["Species", "Platform", "URL", "Title", "Price", "Snippet", "Date", "Run ID"], 1
    ):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = hfont; cell.fill = hfill
        cell.alignment = Alignment(horizontal="center")

    rfont = Font(name="Arial", size=10)
    for ri, row in enumerate(df.itertuples(index=False), 2):
        for ci, val in enumerate(row, 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = rfont
            if ri % 2 == 0:
                cell.fill = PatternFill("solid", start_color="D6E4F0")

    for ci, w in enumerate([28, 14, 55, 45, 10, 40, 12, 14], 1):
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = w

    ws.freeze_panes = "A2"
    try:
        wb.save(OUTPUT_XLSX)
        print(f"  [saved {len(df)} unique records → {OUTPUT_XLSX}]")
    except PermissionError:
        print(f"  [skipped save — close {OUTPUT_XLSX} first]")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    run_id   = datetime.now().strftime("%Y%m%d_%H%M")
    all_rows = []
    seen     = set()

    atexit.register(lambda: save_xlsx(all_rows))

    if TEST_SPECIES:
        species_list = TEST_SPECIES
        print(f"Species  : {len(species_list)} manually specified")
    else:
        print("Loading species from EPPO database...")
        species_list = load_eppo_species(EPPO_DB)
        random.shuffle(species_list)
        if TEST_SAMPLE is not None:
            species_list = species_list[:TEST_SAMPLE]
        print(f"Species  : {len(species_list):,} loaded")

    print(f"Platforms: {len(PLATFORMS)}")
    print(f"Queries  : {len(species_list) * len(PLATFORMS):,}")
    print(f"Output   : {OUTPUT_XLSX}")
    print(f"Run ID   : {run_id}\n{'─'*60}")

    total = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for si, species in enumerate(species_list, 1):
        print(f"\n[{si}/{len(species_list)}] {species}")
        hits = 0

        for p in PLATFORMS:
            results = p["fn"](species)
            new = []
            for r in results:
                key = (species, r["url"])
                if key in seen or not r["url"]:
                    continue
                seen.add(key)
                new.append({
                    "species":  species,
                    "platform": p["name"],
                    "url":      r["url"],
                    "title":    r["title"],
                    "price":    r["price"],
                    "snippet":  r["snippet"],
                    "date":     today,
                    "run_id":   run_id,
                })

            if new:
                all_rows.extend(new)
                hits  += len(new)
                total += len(new)
                append_csv(new)
                for r in new:
                    price_str = f"  {r['price']}" if r['price'] else ""
                    print(f"  + [{p['name']}]{price_str}  {r['title'][:55]}")
                    print(f"    {r['url']}")

            time.sleep(DELAY + random.uniform(0, 1.5))

        if hits:
            save_xlsx(all_rows)
            print(f"  [{hits} listings this species, {total} total]")
        else:
            print("  — no listings found")

    print(f"\n{'═'*60}")
    print(f"Done. {total} listings across {len(species_list)} species.")
    save_xlsx(all_rows)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nStopped by user.")
