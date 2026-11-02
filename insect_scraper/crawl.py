"""Crawl one shop: platform catalogue, sitemaps or links; shipping/imprint pages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from lxml import etree, html as lxml_html

from . import classify, config, extract
from .fetch import Fetcher
from .names import NameIndex

INSECT_RX = re.compile(
    r"insect|insekt|feeder|live|levende|lebend|vivant|larva|larve|pupa|puppe|nymph|"
    r"ant|ameise|maur|beetle|kafer|käfer|bille|butterfl|schmetter|sommerfugl|moth|"
    r"mantis|mante|phasm|stick|stab|pinne|cricket|grille|sirisse|roach|schabe|kakerlak|"
    r"locust|heuschreck|gresshopp|worm|wurm|mark|snail|schnecke|snegl|isopod|assel|"
    r"springtail|spring|tarantul|scorpion|invert|colony|kolonie|koloni|queen|königin|"
    r"dronning|cocoon|kokon|egg|eier|ova|ootheca|bug|wanze|silk|seide", re.I)
SHIP_LINK_RX = re.compile(
    r"ship|deliver|versand|liefer|frakt|levering|leverans|livraison|env[ií]o|envio|"
    r"spedizion|wysy|verzend|terms|agb|vilk[aå]r|villkor|conditions|condiciones|"
    r"impressum|imprint|contact|kontakt|about|om-oss|om oss|uber-uns|qui-sommes", re.I)
LATIN_RX = re.compile(r"\b([A-Z][a-z]{2,})\s([a-z]{3,})\b")
NOT_EPITHET = {
    "sp", "spp", "and", "the", "with", "genus", "larvae", "larva", "larve", "larven",
    "adult", "adults", "female", "females", "male", "males", "colony", "colonies",
    "queen", "queens", "nymph", "nymphs", "pair", "pairs", "eggs", "egg", "ova",
    "pupa", "pupae", "culture", "cultures", "morph", "form", "var", "species",
    "for", "from", "sale", "live", "per", "pack", "count", "size", "small", "medium",
    "large", "jumbo", "mini", "giant", "food", "kit", "set", "box", "cup", "pcs",
    "stk", "und", "mit", "der", "die", "das", "och", "med", "til", "les", "des",
}
# genus mentions incl. "Collembola sp." (for the other-genus check)
GENUS_RX = re.compile(r"\b([A-Z][a-z]{2,})\s(?:spp?\.?|[a-z]{3,})(?=\W|$)")


@dataclass
class ShopResult:
    domain: str
    status: str = "ok"
    platform: str = ""
    method: str = ""
    n_catalogue: int = 0
    n_pages_fetched: int = 0
    products: list = field(default_factory=list)     # matched product rows
    new_names: list = field(default_factory=list)    # Latin names not in our list
    shop_info: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return self.__dict__


def _skip_reason(domain: str) -> str | None:
    d = domain.lower().removeprefix("www.")
    for k, why in config.SKIP_DOMAINS.items():
        if d == k or d.endswith("." + k):
            return why
    return None


def _platform(page: str) -> str:
    p = page[:300000]
    if "cdn.shopify.com" in p or "Shopify.theme" in p:
        return "shopify"
    if "woocommerce" in p.lower() or "wc-block" in p:
        return "woocommerce"
    if "shopware" in p.lower():
        return "shopware"
    if "Magento" in p or "mage/cookies" in p:
        return "magento"
    if "prestashop" in p.lower():
        return "prestashop"
    if "wix.com" in p or "wixstatic" in p:
        return "wix"
    if "squarespace" in p.lower():
        return "squarespace"
    return "other"


def _clean_life_text(t: str) -> str:
    # live-arrival guarantees mention "dead" but mean live animals are shipped
    return re.sub(r"dead on arrival|\bdoa\b|arrive[sd]? (alive|dead)|live arrival",
                  " live arrival ", t or "", flags=re.I)


async def _sitemap_urls(f: Fetcher, base: str, host: str) -> list[tuple[str, str]]:
    queue = f.sitemaps_from_robots(host) + [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml",
                                           f"{base}/product-sitemap.xml", f"{base}/sitemap_products_1.xml"]
    seen, urls = set(), []
    while queue and len(seen) < config.MAX_SITEMAPS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        r = await f.get(sm)
        if not r.ok or "<" not in (r.text or "")[:500]:
            continue
        try:
            root = etree.fromstring(r.text.encode("utf-8"), parser=etree.XMLParser(recover=True, huge_tree=True))
        except Exception:
            continue
        if root is None:
            continue
        children = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.getparent() is not None
                    and e.getparent().tag.endswith("sitemap") and e.text]
        if children:
            pri = [c for c in children if re.search(r"product|produkt|item|artikel|catalog|shop", c, re.I)]
            rest = [c for c in children if c not in pri and not re.search(r"blog|post|page|tag|author|image", c, re.I)]
            queue = pri + queue + rest
        for u in root.iter():
            if not u.tag.endswith("url"):
                continue
            loc, titles = "", []
            for e in u.iter():
                if e.tag.endswith("loc") and e.getparent() is u and e.text:
                    loc = e.text.strip()
                elif (e.tag.endswith("title") or e.tag.endswith("caption")) and e.text:
                    titles.append(e.text.strip())
            if loc:
                urls.append((loc, " ".join(titles)))
    return urls


async def _link_crawl(f: Fetcher, base: str, host: str, start: list[str]) -> list[tuple[str, str]]:
    """Breadth-first crawl of same-site pages, insect-looking links first."""
    queue = list(dict.fromkeys(start))
    seen, found = set(), {}
    while queue and len(seen) < config.MAX_BFS_PAGES:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = await f.get(url)
        if not r.ok:
            continue
        try:
            doc = lxml_html.fromstring(r.text)
        except Exception:
            continue
        hot, cold = [], []
        for a in doc.xpath("//a[@href]"):
            href = urljoin(url, a.get("href").split("#")[0])
            if urlsplit(href).netloc.removeprefix("www.") != host.removeprefix("www.") or \
                    re.search(r"\.(jpg|png|gif|pdf|zip|webp)$|/cart|/account|/login|mailto:", href, re.I):
                continue
            text = " ".join(a.text_content().split())[:150]
            found.setdefault(href, text)
            (hot if INSECT_RX.search(href + " " + text) else cold).append(href)
        queue = [u for u in hot if u not in seen] + queue + [u for u in cold[:20] if u not in seen]
    return list(found.items())


async def _shop_info(f: Fetcher, base: str, home_html: str, platform: str, currency: str) -> dict:
    urls = []
    if platform == "shopify":
        urls += [f"{base}/policies/shipping-policy", f"{base}/policies/terms-of-service"]
    try:
        doc = lxml_html.fromstring(home_html)
        for a in doc.xpath("//a[@href]"):
            href, text = a.get("href"), " ".join(a.text_content().split())
            if SHIP_LINK_RX.search(f"{href} {text}"):
                u = urljoin(base + "/", href.split("#")[0])
                if urlsplit(u).netloc.removeprefix("www.") == urlsplit(base).netloc.removeprefix("www."):
                    urls.append(u)
    except Exception:
        pass
    urls = list(dict.fromkeys(urls))[: config.MAX_INFO_PAGES + 2]
    texts, imprint = [], ""
    for u in urls[: config.MAX_INFO_PAGES]:
        r = await f.get(u)
        if r.ok:
            t = extract.strip_html(r.text, limit=20000)
            texts.append(t)
            if re.search(r"impressum|imprint|contact|kontakt|about|om-oss", u, re.I):
                imprint += " " + t
    ship = classify.ships_to(" ".join(texts) + " " + extract.strip_html(home_html, 20000))
    country, basis = classify.seller_country(urlsplit(base).netloc, currency, imprint)
    return {**ship, "seller_country": country, "seller_country_basis": basis,
            "info_pages_read": len(texts)}


def _rows(prod: extract.Product, idx: NameIndex, domain: str, platform: str,
          match_extra: str = "") -> tuple[list[dict], list[dict]]:
    slug = re.sub(r"^https?://[^/]+", "", prod.url or "")
    hits = idx.find(f"{prod.title} {prod.category} {re.sub(r'[-_/]', ' ', slug)} {match_extra}")
    # also search the description, but only for scientific names (common names
    # in long descriptions give too many false hits)
    desc_hits = [h for h in idx.find(prod.description) if h[1] == "scientific name"]
    seen = {h[0] for h in hits}
    hits += [(s, "scientific name (description)") for s, _ in desc_hits if s not in seen]
    ptype = classify.page_type(prod.url, prod.title, prod.is_product)
    state, terms = classify.life_state(_clean_life_text(
        f"{prod.title} {prod.category} {slug} {prod.description[:400]}"))
    rows = []
    for species, mtype in hits:
        # common-name hit, but the title names a different known genus
        if mtype == "common name":
            other = {g for g in GENUS_RX.findall(prod.title) if g in idx.genera}
            if other and species.split()[0] not in other:
                continue
        rows.append({"domain": domain, "species": species, "match_type": mtype,
                     "regulated": species in idx.regulated, "url": prod.url,
                     "title": prod.title, "page_type": ptype, "life_state": state,
                     "life_terms": terms, "price": prod.price, "currency": prod.currency,
                     "available": prod.available, "category": prod.category[:200],
                     "source": prod.source, "platform": platform})
    # Latin names of known genera that are not in our list (possible new species)
    new = []
    if ptype in ("product", "category"):
        known = set(idx.scientific)
        for g, s in LATIN_RX.findall(f"{prod.title} {prod.description[:600]}"):
            key = f"{g.lower()} {s}"
            # only plant-pest-scope genera; skip words that are not epithets
            if g in idx.scope_genera and key not in known and s not in NOT_EPITHET:
                new.append({"domain": domain, "name": f"{g} {s}", "url": prod.url,
                            "title": prod.title, "life_state": state, "page_type": ptype})
    return rows, new


async def crawl_shop(f: Fetcher, idx: NameIndex, domain: str, found_url: str = "") -> ShopResult:
    res = ShopResult(domain=domain)
    why = _skip_reason(domain)
    if why:
        res.status = f"skipped: {why}"
        return res

    base, home = None, None
    for cand in (f"https://{domain}", f"https://www.{domain.removeprefix('www.')}"):
        r = await f.get(cand + "/")
        if r.ok:
            base, home = cand, r.text
            break
        if r.status == -1:
            res.status = "disallowed by robots.txt"
            return res
    if not base:
        res.status = "unreachable"
        return res
    host = urlsplit(base).netloc
    res.platform = _platform(home)
    m = re.search(r'Shopify\.currency\s*=\s*\{"active":"([A-Z]{3})"', home) or \
        re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', home) or \
        re.search(r'product:price:currency"\s+content="([A-Z]{3})"', home)
    currency = m.group(1) if m else ""

    products: list[extract.Product] = []
    # 1. platform catalogue APIs
    if res.platform == "shopify":
        for page in range(1, config.MAX_API_PAGES + 1):
            r = await f.get(f"{base}/products.json?limit=250&page={page}")
            if not r.ok:
                break
            try:
                items = json.loads(r.text).get("products", [])
            except ValueError:
                break
            if not items:
                break
            products += [extract.from_shopify(p, base, currency) for p in items]
        res.method = "shopify catalogue" if products else ""
    if not products and res.platform in ("woocommerce", "other"):
        for api in ("/wp-json/wc/store/v1/products", "/wp-json/wc/store/products"):
            for page in range(1, config.MAX_API_PAGES + 1):
                r = await f.get(f"{base}{api}?per_page=100&page={page}")
                if not r.ok:
                    break
                try:
                    items = json.loads(r.text)
                except ValueError:
                    break
                if not isinstance(items, list) or not items:
                    break
                products += [extract.from_woocommerce(p) for p in items]
            if products:
                res.method = "woocommerce catalogue"
                res.platform = "woocommerce"
                break

    rows, new = [], []
    if products:
        res.n_catalogue = len(products)
        for p in products:
            a, b = _rows(p, idx, domain, res.platform)
            rows += a
            new += b
    else:
        # 2. sitemaps, else 3. link crawl
        urls = await _sitemap_urls(f, base, host)
        res.method = "sitemap"
        if not urls:
            urls = await _link_crawl(f, base, host, [base + "/"] + ([found_url] if found_url else []))
            res.method = "link crawl"
        res.n_catalogue = len(urls)

        def prio(item):
            url, text = item
            slug = re.sub(r"^https?://[^/]+", "", url)
            if classify.INFO_RX.search(slug):
                return 4
            if idx.find(f"{re.sub(r'[-_/]', ' ', slug)} {text}"):
                return 0
            if INSECT_RX.search(f"{slug} {text}"):
                return 1
            return 2 if re.search(r"/products?/|/p/|/item|/artikel|\.html$", slug) else 3
        scored = [(u, t, prio((u, t))) for u, t in dict(urls).items()]
        scored.sort(key=lambda x: x[2])
        # pages with a name or insect word: always. Other product pages only
        # for shops that are mainly insect shops (>= 30% insect-looking URLs);
        # general pet shops would otherwise cost ~40 min for dog food pages.
        n_insect = sum(1 for *_, p in scored if p <= 1)
        n_prod = sum(1 for *_, p in scored if p <= 2)
        max_prio = 2 if n_prod and n_insect / n_prod >= 0.3 else 1
        res.method += f" ({n_insect} insect-looking of {n_prod} product URLs)"
        fetched = 0
        for url, text, p in scored:
            if fetched >= config.MAX_PRODUCT_PAGES or p > max_prio:
                break
            r = await f.get(url)
            fetched += 1
            if not r.ok:
                continue
            p = extract.from_html(url, r.text)
            a, b = _rows(p, idx, domain, res.platform, match_extra=text)
            rows += a
            new += b
        res.n_pages_fetched = fetched

    res.products = rows
    res.new_names = new
    res.shop_info = await _shop_info(f, base, home, res.platform, currency)
    return res
