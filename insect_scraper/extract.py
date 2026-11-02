"""Product records from Shopify JSON, WooCommerce Store API JSON and HTML pages."""

from __future__ import annotations

import html as htmlmod
import re
from dataclasses import dataclass, asdict

import extruct
from lxml import html as lxml_html


@dataclass
class Product:
    url: str
    title: str = ""
    description: str = ""
    category: str = ""          # product type, categories, tags, breadcrumbs
    price: str = ""
    currency: str = ""
    available: str = ""         # in stock / out of stock / unknown
    source: str = ""            # shopify-json, woocommerce-api, json-ld, ...
    is_product: bool = True     # False for pages without product data

    def text(self) -> str:
        return " ".join([self.title, self.category, self.description])

    def as_dict(self) -> dict:
        return asdict(self)


def strip_html(s: str | None, limit: int = 1500) -> str:
    if not s:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return " ".join(htmlmod.unescape(s).split())[:limit]


# -- Shopify -------------------------------------------------------------------
def from_shopify(p: dict, base: str, currency: str = "") -> Product:
    variants = p.get("variants") or []
    prices = [v.get("price") for v in variants if v.get("price")]
    avail = [v.get("available") for v in variants if v.get("available") is not None]
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    return Product(
        url=f"{base}/products/{p.get('handle', '')}",
        title=p.get("title") or "",
        description=strip_html(p.get("body_html")),
        category="; ".join(filter(None, [p.get("product_type") or ""] + list(tags)))[:500],
        price=min(prices, key=lambda x: float(x)) if prices else "",
        currency=currency,
        available=("in stock" if any(avail) else "out of stock") if avail else "unknown",
        source="shopify-json")


# -- WooCommerce Store API -------------------------------------------------------
def from_woocommerce(p: dict) -> Product:
    pr = p.get("prices") or {}
    price = pr.get("price") or ""
    minor = pr.get("currency_minor_unit")
    if price and isinstance(minor, int) and minor > 0 and price.isdigit():
        price = f"{int(price) / 10 ** minor:.{minor}f}"
    cats = [c.get("name", "") for c in (p.get("categories") or [])]
    tags = [t.get("name", "") for t in (p.get("tags") or [])]
    return Product(
        url=p.get("permalink") or "",
        title=htmlmod.unescape(p.get("name") or ""),
        description=strip_html((p.get("short_description") or "") + " " + (p.get("description") or "")),
        category="; ".join(filter(None, cats + tags))[:500],
        price=price, currency=pr.get("currency_code") or "",
        available="in stock" if p.get("is_in_stock") else "out of stock",
        source="woocommerce-api")


# -- HTML page -----------------------------------------------------------------
def _first(x):
    while isinstance(x, list):
        if not x:
            return None
        x = x[0]
    return x


def _is_type(item: dict, name: str) -> bool:
    t = item.get("@type")
    t = t if isinstance(t, list) else [t]
    return any(isinstance(v, str) and v.split("/")[-1] == name for v in t)


def _walk(items):
    for it in items or []:
        if isinstance(it, dict):
            yield it
            if "@graph" in it:
                yield from _walk(it["@graph"])


def _num(v) -> str:
    v = _first(v)
    if v is None:
        return ""
    s = str(v).strip()
    return s if re.fullmatch(r"\d+(?:[.,]\d+)?", s) and float(s.replace(",", ".")) > 0 else ""


def from_html(url: str, page: str) -> Product:
    prod = Product(url=url, is_product=False, source="html")
    try:
        data = extruct.extract(page, base_url=url, uniform=True,
                               syntaxes=["json-ld", "microdata", "opengraph"],
                               errors="ignore")
    except Exception:
        data = {}
    items = list(_walk(data.get("json-ld"))) + list(_walk(data.get("microdata")))
    product = next((i for i in items if _is_type(i, "Product")), None)
    crumbs = next((i for i in items if _is_type(i, "BreadcrumbList")), None)
    og = (data.get("opengraph") or [{}])[0] if data.get("opengraph") else {}

    if product:
        prod.is_product = True
        prod.source = "structured data"
        prod.title = str(_first(product.get("name")) or "")
        prod.description = strip_html(str(_first(product.get("description")) or ""))
        offers = _first(product.get("offers")) or {}
        if isinstance(offers, dict):
            inner = _first(offers.get("offers")) if offers.get("offers") else {}
            spec = _first(offers.get("priceSpecification")) or {}
            prod.price = (_num(offers.get("price")) or _num(offers.get("lowPrice"))
                          or _num((inner or {}).get("price"))
                          or _num(spec.get("price") if isinstance(spec, dict) else None))
            prod.currency = str(_first(offers.get("priceCurrency")) or
                                (spec.get("priceCurrency") if isinstance(spec, dict) else "") or "")
            av = str(_first(offers.get("availability")) or "")
            prod.available = ("in stock" if re.search(r"InStock|LimitedAvailability|PreOrder", av)
                              else "out of stock" if re.search(r"OutOfStock|SoldOut|Discontinued", av)
                              else "unknown")
        cat = _first(product.get("category"))
        prod.category = str(cat or "")
    elif og.get("og:type", "").lower().startswith("product"):
        prod.is_product = True
        prod.source = "opengraph"
    if crumbs:
        names = []
        for e in crumbs.get("itemListElement") or []:
            if not isinstance(e, dict):
                continue
            n = _first(e.get("name"))
            item = e.get("item")
            if not n and isinstance(item, dict):
                n = _first(item.get("name"))
            if n:
                names.append(str(n))
        prod.category = "; ".join(filter(None, [prod.category] + names))[:500]

    # fallbacks from the HTML itself
    try:
        doc = lxml_html.fromstring(page)
    except Exception:
        doc = None
    if doc is not None:
        meta = lambda p: (doc.xpath(f"//meta[@property='{p}' or @name='{p}']/@content") or [""])[0]
        if not prod.title:
            prod.title = (og.get("og:title") or meta("og:title")
                          or " ".join(doc.xpath("//h1//text()")).strip()
                          or " ".join(doc.xpath("//title//text()")).strip())
        if not prod.description:
            prod.description = strip_html(og.get("og:description") or meta("og:description")
                                          or meta("description"))
        if not prod.price:
            prod.price = _num(meta("product:price:amount") or meta("og:price:amount"))
            prod.currency = prod.currency or meta("product:price:currency") or meta("og:price:currency")
    prod.title = " ".join(prod.title.split())[:300]
    return prod
