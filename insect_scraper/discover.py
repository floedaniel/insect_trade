"""Find more shops: search engines queried for species and live-insect terms.

Queries (each run once; logged in 2_processed_data/discovery_queries.csv):
  1. regulated species: scientific + common names x "for sale/kaufen/kjøp ..."
  2. live-traded candidate species (LEMIS live / Lassaline live use)
  3. generic live-insect shop terms in NO, SV, DA, DE, NL, FR, ES, PL, EN
New shop domains go to 2_processed_data/shops_master.csv (working file).
"""

from __future__ import annotations

import os
import random
import re
import time
from datetime import date
from urllib.parse import urlsplit

import pandas as pd

from . import classify, config
from .names import build_index

QUERY_LOG = config.DATA_DIR / "discovery_queries.csv"

SALE_WORDS = ["for sale", "kaufen", "kjøp", "köpa", "køb", "kopen", "acheter", "comprar", "kupić"]
GENERIC = [
    # Norwegian / Nordic first (Phase 1)
    "kjøp levende insekter", "levende insekter nettbutikk", "fôrinsekter kjøp", "maurkoloni kjøp",
    "kjøp maur dronning", "vandrende pinne kjøp", "pinnedyr til salgs", "kjøp knelere mantis",
    "levende biller kjøp", "akvariesnegler kjøp", "eksotiske insekter nettbutikk norge",
    "köpa levande insekter", "myrkoloni köpa", "foderinsekter köpa", "vandrande pinne köpa",
    "levende insekter køb", "myrekoloni køb", "foderdyr insekter køb",
    # German / Dutch
    "lebende Insekten kaufen", "Ameisenkolonie kaufen", "Ameisen Königin kaufen", "Futterinsekten kaufen",
    "Gespenstschrecken kaufen", "Gottesanbeterin kaufen", "Käferlarven kaufen", "Rosenkäfer Larven kaufen",
    "Apfelschnecken kaufen", "Schnecken für Aquarium kaufen", "levende insecten kopen", "mierenkolonie kopen",
    "wandelende takken kopen", "bidsprinkhaan kopen",
    # French / Spanish / Polish
    "acheter insectes vivants", "colonie de fourmis acheter", "phasmes à vendre", "mante religieuse acheter",
    "comprar insectos vivos", "colonia de hormigas comprar", "kupić żywe owady", "kolonia mrówek sklep",
    "patyczaki sklep", "modliszki sklep",
    # English
    "live insects for sale", "ant colony for sale", "ant queen for sale ship to Europe",
    "live beetle larvae for sale", "stick insects for sale", "praying mantis for sale",
    "exotic invertebrates shop Europe", "live caterpillars for sale", "butterfly pupae for sale",
    "live snails for sale aquarium apple snail", "giant african land snail for sale",
    "isopods and beetles shop EU shipping", "insect eggs for sale ootheca",
]
NOISE = re.compile(r"wikipedia|wikimedia|gbif|inaturalist|researchgate|ncbi|sciencedirect|springer|"
                   r"wiley|jstor|youtube|pinterest|facebook|instagram|tiktok|twitter|x\.com|reddit|"
                   r"quora|antwiki|bugguide|eppo\.int|cabi\.org|europa\.eu|\.gov|\.edu|\.ac\.|"
                   r"museum|naturkunde|artsdatabanken|mattilsynet|lovdata|news|avis|zeitung|bundesamt|"
                   r"ministerium|ministry|agency|authority|acnh|animal-?crossing|gamewith|fandom|"
                   r"wiki|dictionary|translate|linkedin|crunchbase|tracxn|investing|finance", re.I)
# whole-word insect cues (stricter than the crawler's URL-ranking pattern)
RELEVANT_RX = re.compile(
    r"(insects?|insekt(er|en)?|feeders?|larvae?|larver?|larven|pupae?|puppen|nymphs?|"
    r"ants?|ant colon(y|ies)|ameisen?|maur(koloni)?|myror|beetles?|k[äa]fer|biller?|"
    r"butterfl(y|ies)|schmetterlinge?|sommerfugler?|moths?|mantis|mantids?|phasmids?|"
    r"stick insects?|gespenstschrecken|pinnedyr|crickets?|grillen?|sirisser?|roach(es)?|"
    r"schaben?|locusts?|snails?|schnecken?|snegler?|invertebrates?|wirbellose|"
    r"kolonien?|koloni|queens?|k[öo]nigin|dronning|ootheca|eggs|ova|caterpillars?|raupen?)", re.I)
COMMERCE = re.compile(r"shop|store|butikk|nettbutikk|butik|kaufen|kjøp|köp|køb|kopen|acheter|comprar|"
                      r"kupi|sklep|for sale|til salgs|zu verkaufen|à vendre|en venta|price|pris|preis|prix|"
                      r"precio|cena|€|\$|£|\bkr\b|nok|add to cart|warenkorb|handlekurv|varukorg|panier|"
                      r"carrito|koszyk|/products?/|/shop/|/produkt", re.I)


def _queries(idx, max_queries: int) -> list[str]:
    qs: list[str] = []
    info = idx.info
    reg = sorted(n for n in idx.regulated if not n.endswith(" spp."))
    common_by_target: dict[str, list[str]] = {}
    for k, t in idx.common.items():
        common_by_target.setdefault(t, []).append(k)
    for n in reg:
        qs += [f'"{n}" {w}' for w in ("for sale", "kaufen", "kjøp", "acheter")]
        for c in sorted(common_by_target.get(n, []), key=len)[:2]:
            qs.append(f'"{c}" for sale')
    for t in sorted(t for t in idx.regulated if t.endswith(" spp.")):
        qs.append(f'"{t.split()[0]}" for sale')
    qs += GENERIC
    live = info[info["life_state"] == "live"].index.tolist()
    random.Random(1).shuffle(live)
    for n in live:
        qs.append(f'"{n}" {SALE_WORDS[hash(n) % len(SALE_WORDS)]}')
    return list(dict.fromkeys(qs))[: max_queries * 3]


def _search(query: str) -> list[dict]:
    out = []
    from ddgs import DDGS
    for backend in ("duckduckgo", "bing", "yahoo"):     # free backends fail at times
        try:
            rs = DDGS().text(query, max_results=20, backend=backend) or []
        except Exception:
            continue
        out += [{"url": r.get("href", ""), "title": r.get("title", ""),
                 "snippet": r.get("body", ""), "engine": backend} for r in rs]
        if rs:
            break
    key = os.environ.get("TAVILY_API_KEY")
    if key:
        try:
            from tavily import TavilyClient
            for r in TavilyClient(api_key=key).search(query, max_results=20).get("results", []):
                out.append({"url": r.get("url", ""), "title": r.get("title", ""),
                            "snippet": r.get("content", "")[:300], "engine": "tavily"})
        except Exception as e:
            print(f"    tavily: {type(e).__name__}")
    return out


def discover(max_queries: int = 400) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(config.PROJECT_DIR / ".env")
    except ImportError:
        pass
    idx = build_index(fetch_missing=False)
    done = set(pd.read_csv(QUERY_LOG)["query"]) if QUERY_LOG.exists() else set()
    queries = [q for q in _queries(idx, max_queries) if q not in done][:max_queries]
    master = (pd.read_csv(config.SHOPS_MASTER, dtype=str).fillna("")
              if config.SHOPS_MASTER.exists() else pd.DataFrame())
    known = set(master.get("domain", pd.Series(dtype=str)))
    if config.DISCOVERED_SHOPS.exists():
        known |= set(pd.read_csv(config.DISCOVERED_SHOPS, dtype=str)["domain"].str.removeprefix("www."))
    print(f"Discovery: {len(queries)} new queries | known shops: {len(known)}")

    new_rows, log = [], []
    for i, q in enumerate(queries, 1):
        results = _search(q)
        added = 0
        for r in results:
            dom = urlsplit(r["url"]).netloc.lower().removeprefix("www.")
            text = f"{r['url']} {r['title']} {r['snippet']}"
            if not dom or dom in known or NOISE.search(dom) or not COMMERCE.search(text):
                continue
            # relevance: the queried name/genus, a name from our list, or an insect word
            qname = re.sub(r'"|(for sale|kaufen|kjøp|köpa|køb|kopen|acheter|comprar|kupić)', "", q).strip().lower()
            is_latin = bool(re.match(r"^[A-Z][a-z]+ [a-z]+", re.sub(r'"', "", q)))
            qhit = (qname.split()[0] in text.lower()) if is_latin else (qname in text.lower())
            if not (qhit or idx.find(text) or RELEVANT_RX.search(text)):
                continue
            if any(dom == k or dom.endswith("." + k) for k in config.SKIP_DOMAINS):
                continue
            state, terms = classify.life_state(r["title"], r["snippet"])
            new_rows.append({"domain": dom, "url": r["url"], "title": r["title"][:200],
                             "snippet": r["snippet"][:300], "query": q, "engine": r["engine"],
                             "date": date.today().isoformat(), "life_state": state,
                             "life_terms": terms, "source": "discovery search"})
            known.add(dom)
            added += 1
        log.append({"query": q, "results": len(results), "new_shops": added,
                    "date": date.today().isoformat()})
        print(f"[{i}/{len(queries)}] {q[:60]:<60} results {len(results):>3}  new shops {added}")
        time.sleep(2.5 + random.random() * 2)          # polite to search engines
        if i % 20 == 0 or i == len(queries):
            if new_rows:
                master = pd.concat([master, pd.DataFrame(new_rows)], ignore_index=True)
                master.to_csv(config.SHOPS_MASTER, index=False)
                new_rows = []
            pd.concat([pd.read_csv(QUERY_LOG) if QUERY_LOG.exists() else pd.DataFrame(),
                       pd.DataFrame(log)]).to_csv(QUERY_LOG, index=False)
            log = []
    print(f"Shops master: {len(master)} discovered by search")
