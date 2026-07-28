# Insect Trade Research

Maps the global live insect trade online — discovering which websites sell live insects and which species are available.

## Architecture

Three independent scripts, run separately:

### Phase 1 — Shop Discovery (`insect_trade_loop.py`)
Searches the web with generic multilingual queries (beetles, larvae, pupae, eggs, imago…) across Tavily and DuckDuckGo to find specialist insect trade websites. One row per domain. Output: `discovered_shops.csv` / `discovered_shops.xlsx`.

### Platform Scraping (`platform_search.py`)
Scrapes 17 specific platforms (eBay ×8, MorphMarket, Marktplaats, Gumtree, Etsy, AliExpress, JD.com, Taobao, Xianyu, 1688.com) for species-level listings. Uses Playwright for JS-heavy sites. Output: `platform_results.csv` / `platform_results.xlsx`.

### Common Crawl Mining (`commoncrawl_search.py`)
Queries the Common Crawl CDX index by genus name, fetches matching WARC records via HTTP Range requests, and extracts sale pages. No API key needed. Output: `commoncrawl_results.csv` / `commoncrawl_results.xlsx`.

## Setup

```bash
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
playwright install chromium
```

```bash
# WSL / Linux
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Edit `.env`:
```
TAVILY_API_KEY=...
ANTHROPIC_API_KEY=...
```

## Run

```bash
# Phase 1 — runs forever, Ctrl+C to stop and save
python insect_trade_loop.py

# Platform scrape — one pass over all platforms
python platform_search.py

# Common Crawl — one pass over EPPO species list
python commoncrawl_search.py
```

## Output columns

`discovered_shops.csv`: `domain, url, title, snippet, query, engine, date, run_id`

`platform_results.csv`: `species, platform, url, title, price, date, run_id`

`commoncrawl_results.csv`: `species, platform, url, title, price, crawl_id, date, run_id`

## Key design decisions

- `insect_trade_loop.py` deduplicates by **domain** — one entry per specialist shop
- Large generic marketplaces (eBay, Alibaba, Amazon, Etsy, Allegro…) are blocked in the noise list
- Queries are category-level only (no species names) to find shop homepages, not product listings
- Tavily (1.5 s delay) + DuckDuckGo (4.0 s delay) run on every query for double coverage
- Playwright used for eBay and all Chinese platforms to bypass bot detection
