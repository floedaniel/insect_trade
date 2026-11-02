"""Paths, limits and politeness settings for the insect scraper."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "2_processed_data"
TABLES_DIR = PROJECT_DIR / "5_outputs" / "tables"
META_DIR = PROJECT_DIR / "3_metadata"
CACHE_DB = DATA_DIR / "scrape_cache.sqlite"
VERN_CACHE = DATA_DIR / "gbif_vernacular_cache"
SHOPS_MASTER = DATA_DIR / "shops_master.csv"          # working file
DISCOVERED_SHOPS = DATA_DIR / "discovered_shops.csv"   # from insect_trade_loop.py
for d in (DATA_DIR, TABLES_DIR, VERN_CACHE):
    d.mkdir(parents=True, exist_ok=True)

# Honest identification; robots.txt rules for this token or "*" are obeyed.
UA_TOKEN = "insect-trade-research"
USER_AGENT = (f"{UA_TOKEN}/1.0 (non-commercial plant-pest screening; "
              "obeys robots.txt; contact via project owner)")

MIN_DELAY = 1.5          # seconds between requests to one host (or crawl-delay)
MAX_CRAWL_DELAY = 20     # ignore absurd crawl-delay values above this
TIMEOUT = 25
CACHE_MAX_AGE_DAYS = 30  # reuse cached responses younger than this

MAX_API_PAGES = 200      # Shopify / WooCommerce catalogue pages per shop
MAX_SITEMAPS = 40        # sitemap files read per shop
MAX_PRODUCT_PAGES = 1500  # product pages fetched per shop (generic shops)
MAX_BFS_PAGES = 300      # pages fetched in a link crawl (shops without sitemap)
MAX_INFO_PAGES = 6       # shipping / imprint pages per shop
SHOP_TIME_BUDGET = 45 * 60  # seconds per shop; results so far are kept

# Marketplaces and big retailers whose terms forbid automated collection, or
# that are not insect shops. Listed in the report as skipped.
SKIP_DOMAINS = {
    "finn.no": "terms forbid automated collection (use manual search)",
    "ebay.com": "terms forbid scraping (official API needs a key)",
    "ebay.de": "terms forbid scraping", "ebay.co.uk": "terms forbid scraping",
    "ebay.fr": "terms forbid scraping", "ebay.it": "terms forbid scraping",
    "ebay.es": "terms forbid scraping", "ebay.nl": "terms forbid scraping",
    "amazon.com": "terms forbid scraping", "amazon.de": "terms forbid scraping",
    "amazon.co.uk": "terms forbid scraping", "etsy.com": "terms forbid scraping",
    "aliexpress.com": "terms forbid scraping", "alibaba.com": "terms forbid scraping",
    "facebook.com": "terms forbid scraping", "instagram.com": "terms forbid scraping",
    "chewy.com": "terms forbid scraping", "walmart.com": "terms forbid scraping",
    "tise.com": "terms forbid automated collection", "blocket.se": "terms forbid scraping",
    "dba.dk": "terms forbid scraping", "kleinanzeigen.de": "terms forbid scraping",
    "marktplaats.nl": "terms forbid scraping", "gumtree.com": "terms forbid scraping",
    "youtube.com": "not a shop", "wikipedia.org": "not a shop",
    "reddit.com": "not a shop", "pinterest.com": "not a shop",
}
