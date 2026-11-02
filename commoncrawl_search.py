#!/usr/bin/env python3
"""
commoncrawl_search.py
=====================
Searches Common Crawl's CDX index for insect species URLs, then fetches
the actual page content from WARC archives to find trade/sale pages.

How it works:
  1. For each species, query the CDX API for any crawled URL that contains
     the genus name (e.g. *pachnoda* finds pachnoda-marginata-for-sale.html)
  2. Filter CDX hits to commercial-looking URLs
  3. Fetch the compressed WARC slice from S3 (a Range request — no full download)
  4. Parse HTML, extract title + price, apply relevance check
  5. Save to CSV + XLSX (same format as platform_results)

No API keys. No paid services. WARC fetches are HTTP Range requests (~5-50 KB each).
"""

# ==============================================================================
# SETTINGS
# ==============================================================================

from life_state import (TABLES_DIR, DATA_DIR, EPPO_DB as PROJECT_EPPO_DB,
                        classify_life_state, append_csv as _append_csv)

EPPO_DB = str(PROJECT_EPPO_DB)

OUTPUT_CSV  = DATA_DIR / "commoncrawl_results.csv"
OUTPUT_XLSX = TABLES_DIR / "commoncrawl_results.xlsx"

# How many CDX results to fetch per species per crawl (max 100 recommended)
CDX_LIMIT = 100

# How many recent CC crawls to search — fetched dynamically at startup,
# or hardcode fallbacks here. Latest: https://index.commoncrawl.org/collinfo.json
CRAWL_IDS = []          # empty = auto-fetch the N most recent crawls
N_CRAWLS  = 4           # how many recent crawls to use when auto-fetching

TEST_SPECIES = [
    "Pachnoda marginata",
    "Dynastes hercules",
    "Eudicella morgani",
    "Papilio machaon",
]
TEST_SAMPLE = None   # None = full EPPO run (when TEST_SPECIES is empty)

DELAY = 2.0   # seconds between CDX queries

INSECT_ORDER_IDS = [
    59211, 57850, 61290, 58830, 58226,
    59001, 61179, 61095, 58185, 61506,
]

# ==============================================================================

import re
import csv
import json
import gzip
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

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (compatible; InsectTradeResearch/1.0; "
        "+https://www.fhi.no)"
    ),
    "Accept": "*/*",
})

# Domains already covered by platform_search.py — skip to avoid overlap
_PLATFORM_DOMAINS = {
    "ebay.com", "ebay.co.uk", "ebay.de", "ebay.fr", "ebay.it",
    "ebay.es", "ebay.nl", "ebay.com.au",
    "morphmarket.com", "marktplaats.nl", "gumtree.com", "etsy.com",
    "aliexpress.com", "jd.com", "taobao.com", "goofish.com", "1688.com",
}

# Domains that are never trade sites
_NOISE_DOMAINS = {
    "wikipedia.org", "wikimedia.org", "britannica.com", "iucnredlist.org",
    "inaturalist.org", "gbif.org", "bugguide.net", "researchgate.net",
    "jstor.org", "sciencedirect.com", "springer.com", "nature.com",
    "facebook.com", "instagram.com", "tiktok.com", "twitter.com",
    "x.com", "youtube.com", "reddit.com", "pinterest.com",
    "stock.adobe.com", "shutterstock.com", "alamy.com", "gettyimages.com",
    "blogspot.com", "wordpress.com",
}

_COMMERCIAL_URL_RE = re.compile(
    r'/(buy|shop|sale|kaufen|vente|vendita|kopen|tienda|'
    r'product|listing|item|order|cart|'
    r'insect|beetle|butterfly|larva|larve|pupa|'
    r'lebend|vivant|levend|vivo|vivi)[s/_-]',
    re.IGNORECASE,
)

_PRICE_RE = re.compile(
    r'[€$£¥₩฿]\s*[\d.,]+|[\d.,]+\s*(?:USD|EUR|GBP|JPY|AUD|CAD)\b',
    re.IGNORECASE,
)

_BUY_RE = re.compile(
    r'\b(buy|for sale|kaufen|kopen|acheter|comprar|acquistare|'
    r'add to cart|in stock|order now|出售|販売|판매)\b',
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


# ── Crawl ID loader ───────────────────────────────────────────────────────────

def get_crawl_ids(n: int = 4) -> list[str]:
    """Fetch the N most recent Common Crawl IDs from the official index."""
    try:
        r = _SESSION.get("https://index.commoncrawl.org/collinfo.json", timeout=15)
        r.raise_for_status()
        ids = [c["id"] for c in r.json()]
        return ids[:n]
    except Exception as e:
        print(f"  [collinfo error] {e} — using fallback IDs")
        return ["CC-MAIN-2026-17", "CC-MAIN-2026-12",
                "CC-MAIN-2026-08", "CC-MAIN-2026-04"]


# ── CDX query ─────────────────────────────────────────────────────────────────

def query_cdx(genus: str, crawl_id: str) -> list[dict]:
    """Return CDX index records for any URL containing the genus name.

    Builds the query URL manually so that special characters (* : /) are
    encoded correctly — requests.get(params=...) encodes colons as %3A which
    breaks the CDX filter syntax.
    """
    from urllib.parse import quote
    pattern = quote(f"*{genus.lower()}*", safe="")   # %2A genus %2A
    url = (
        f"https://index.commoncrawl.org/{crawl_id}-index"
        f"?url={pattern}"
        f"&output=json"
        f"&limit={CDX_LIMIT}"
        f"&filter=mime:text/html"
        f"&fl=url,filename,offset,length,status"
    )
    try:
        r = _SESSION.get(url, timeout=30)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        records = []
        for line in r.text.strip().splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return records
    except Exception as e:
        print(f"    [cdx error] {e}")
        return []


# ── WARC fetch ────────────────────────────────────────────────────────────────

def fetch_warc(filename: str, offset: str, length: str) -> str | None:
    """Fetch a single page from Common Crawl S3 via HTTP Range request."""
    start  = int(offset)
    end    = start + int(length) - 1
    warc_url = f"https://data.commoncrawl.org/{filename}"
    try:
        r = _SESSION.get(warc_url,
                         headers={"Range": f"bytes={start}-{end}"},
                         timeout=30)
        r.raise_for_status()
        # Decompress gzip
        raw = gzip.decompress(r.content)
        # WARC record structure:
        #   <WARC headers>\r\n\r\n<HTTP headers>\r\n\r\n<body>
        parts = raw.split(b"\r\n\r\n", 2)
        if len(parts) < 3:
            return None
        return parts[2].decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"    [warc error] {e}")
        return None


# ── Filters ───────────────────────────────────────────────────────────────────

def domain_of(url: str) -> str:
    try:
        return url.split("/")[2].lstrip("www.")
    except IndexError:
        return ""


def is_blocked(url: str) -> bool:
    d = domain_of(url)
    return (any(nd in d for nd in _NOISE_DOMAINS) or
            any(pd in d for pd in _PLATFORM_DOMAINS))


def looks_commercial_url(url: str) -> bool:
    return bool(_COMMERCIAL_URL_RE.search(url))


def extract_from_html(html: str, species: str) -> dict | None:
    """Parse HTML, return {title, price} if page looks like a sale page."""
    try:
        tree  = lx.fromstring(html.encode())
        title = (tree.findtext(".//title") or "").strip()
        text  = " ".join(tree.xpath("//body//text()"))[:3000]
    except Exception:
        return None
    genus = species.split()[0].lower()
    if genus not in (title + text).lower():
        return None
    if not (_PRICE_RE.search(text) or _BUY_RE.search(text)):
        return None
    price_m = _PRICE_RE.search(text)
    price   = price_m.group(0).strip() if price_m else ""
    return {"title": title[:120], "price": price}


# ── Output ────────────────────────────────────────────────────────────────────

CSV_FIELDS = ["species", "platform", "url", "title", "price", "crawl_id", "date", "run_id", "life_state", "life_terms"]


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
    ws.title = "Common Crawl Results"

    hfill = PatternFill("solid", start_color="1F4E79")
    hfont = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    for ci, h in enumerate(
        ["Species", "Platform/Domain", "URL", "Title", "Price",
         "Crawl ID", "Date", "Run ID", "Life state", "Life terms"], 1
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

    for ci, w in enumerate([28, 22, 60, 50, 10, 18, 12, 14, 11, 30], 1):
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = w

    ws.freeze_panes = "A2"
    try:
        wb.save(OUTPUT_XLSX)
        print(f"  [saved {len(df)} unique records -> {OUTPUT_XLSX}]")
    except PermissionError:
        print(f"  [skipped save - close {OUTPUT_XLSX} first]")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    run_id   = datetime.now().strftime("%Y%m%d_%H%M")
    all_rows = []
    seen     = set()
    today    = datetime.now().strftime("%Y-%m-%d")

    atexit.register(lambda: save_xlsx(all_rows))

    # Resolve crawl IDs
    crawl_ids = CRAWL_IDS if CRAWL_IDS else get_crawl_ids(N_CRAWLS)

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

    print(f"Crawls   : {len(crawl_ids)}  ({', '.join(crawl_ids)})")
    print(f"Output   : {OUTPUT_XLSX}")
    print(f"Run ID   : {run_id}\n{'-'*60}")

    total = 0

    for si, species in enumerate(species_list, 1):
        genus = species.split()[0]
        print(f"\n[{si}/{len(species_list)}] {species}")

        for crawl_id in crawl_ids:
            print(f"  CDX {crawl_id} ...", end=" ", flush=True)
            records = query_cdx(genus, crawl_id)

            # -- per-stage debug counts --
            n_blocked   = sum(1 for r in records if is_blocked(r.get("url", "")))
            n_noncom    = sum(1 for r in records
                              if not is_blocked(r.get("url", ""))
                              and not looks_commercial_url(r.get("url", "")))
            print(f"{len(records)} hits  "
                  f"({n_blocked} blocked-domain, {n_noncom} non-commercial URL, "
                  f"{len(records)-n_blocked-n_noncom} to fetch)")

            for rec in records:
                url = rec.get("url", "")
                if not url or is_blocked(url):
                    continue
                if not looks_commercial_url(url):
                    continue
                if (species, url) in seen:
                    continue

                # Fetch the actual page from WARC
                html = fetch_warc(rec["filename"], rec["offset"], rec["length"])
                if html is None:
                    continue

                info = extract_from_html(html, species)
                if info is None:
                    continue

                seen.add((species, url))
                row = {
                    "species":  species,
                    "platform": domain_of(url),
                    "url":      url,
                    "title":    info["title"],
                    "price":    info["price"],
                    "crawl_id": crawl_id,
                    "date":     today,
                    "run_id":   run_id,
                }
                all_rows.append(row)
                total += 1
                append_csv([row])
                price_str = f"  {row['price']}" if row["price"] else ""
                print(f"    + [{row['platform']}]{price_str}  {row['title'][:55]}")
                print(f"      {url}")

                time.sleep(0.5)   # be polite to CC S3

            time.sleep(DELAY + random.uniform(0, 1.5))

        if any(r["species"] == species for r in all_rows):
            save_xlsx(all_rows)
            hits = sum(1 for r in all_rows if r["species"] == species)
            print(f"  [{hits} listings, {total} total]")
        else:
            print("  -- no listings found")

    print(f"\n{'='*60}")
    print(f"Done. {total} listings across {len(species_list)} species.")
    save_xlsx(all_rows)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\nStopped by user.")
