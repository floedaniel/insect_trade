"""Combine per-shop results into 5_outputs/tables/species_on_sale.xlsx (xlsx only)."""

from __future__ import annotations

import json
import re

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from . import config
from .names import build_index

RESULTS_DIR = config.DATA_DIR / "scrape_results"
SALE_TYPES = ("product", "category")


def write_xlsx(sheets: dict[str, pd.DataFrame], path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", start_color="1F4E79")
                c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.freeze_panes = "A2"
            if len(df.columns) > 1 and len(df):
                ws.auto_filter.ref = ws.dimensions
            for i, col in enumerate(df.columns, 1):
                vals = [str(col)] + [str(v) for v in df[col].head(300).tolist()]
                ws.column_dimensions[ws.cell(1, i).column_letter].width = \
                    min(max(len(v) for v in vals) + 2, 60)


def write_report(shops: pd.DataFrame) -> None:
    res = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS_DIR.glob("*.json"))]
    if not res:
        print("No crawl results yet.")
        return
    idx = build_index(fetch_missing=False)
    info = idx.info.reset_index()

    shop_rows = []
    for r in res:
        si = r.get("shop_info") or {}
        shop_rows.append({
            "domain": r["domain"], "status": r.get("status", ""),
            "platform": r.get("platform", ""), "method": r.get("method", ""),
            "catalogue_items": r.get("n_catalogue", 0), "pages_fetched": r.get("n_pages_fetched", 0),
            "seller_country": si.get("seller_country", ""),
            "seller_country_basis": si.get("seller_country_basis", ""),
            "ships_to_norway": si.get("ships_to_norway", ""), "ship_scope": si.get("ship_scope", ""),
            "live_shipping_note": si.get("live_shipping_note", ""),
            "ship_evidence": si.get("ship_evidence", "")})
    shops_df = pd.DataFrame(shop_rows)

    listings = pd.DataFrame([p for r in res for p in r.get("products", [])])
    # re-check common-name hits with the current rules (results saved by
    # earlier crawls used older rules): drop when the title names another
    # genus, or when the common name no longer matches (e.g. now generic)
    if not listings.empty:
        from .crawl import GENUS_RX

        def keep(row):
            if not str(row.get("match_type", "")).startswith("common"):
                return True
            title = str(row.get("title", ""))
            genus = str(row["species"]).split()[0]
            other = {g for g in GENUS_RX.findall(title) if g in idx.genera}
            if other and genus not in other:
                return False
            # title names another species of the same genus ("Pomacea bridgesii")
            named = {f"{g} {e.lower()}" for g, e in
                     re.findall(r"\b([A-Z][a-z]{2,})\s([A-Za-z][a-z]{2,})\b", title)
                     if g == genus and e.lower() not in ("sp", "spp", "snail", "snails", "species")}
            if named and row["species"] not in named and not str(row["species"]).endswith(" spp."):
                return False
            text = f"{row.get('title', '')} {row.get('url', '')} {row.get('category', '')}"
            return any(t == row["species"] for t, m in idx.find(text) if m == "common name")
        listings = listings[listings.apply(keep, axis=1)]
        # re-apply current page-type rules (e.g. pest-control products) and
        # re-check "dead" labels that rest only on the plain word "dead"
        from . import classify
        is_prod = listings["page_type"].isin(["product", "accessory", "info"])
        listings["page_type"] = [
            classify.page_type(u, t, p) if pt != "page" else pt
            for u, t, p, pt in zip(listings["url"].astype(str), listings["title"].astype(str),
                                   is_prod, listings["page_type"])]
        redo = listings["life_terms"].astype(str).str.fullmatch(r"dead:dead( ?; ?live:[^;]+)*")
        for i in listings.index[redo]:
            row = listings.loc[i]
            st, te = classify.life_state(str(row["title"]), str(row.get("category", "")),
                                         str(row["url"]).split("/", 3)[-1].replace("-", " "))
            listings.at[i, "life_state"], listings.at[i, "life_terms"] = st, te
    if listings.empty:
        listings = pd.DataFrame(columns=["domain", "species", "match_type", "regulated", "url",
                                         "title", "page_type", "life_state", "price", "currency"])
    listings = (listings.merge(shops_df[["domain", "seller_country", "ships_to_norway", "ship_scope"]],
                               on="domain", how="left")
                .merge(info.rename(columns={"accepted_name": "species", "life_state": "species_life_state_R",
                                            "regulated": "regulated_R"}),
                       on="species", how="left"))
    listings["regulated"] = listings["regulated"].astype(bool)

    # --- evidence grade -----------------------------------------------------
    # A shop is a live-animal shop if it has a priced product labelled live,
    # or if discovery already labelled it live. Agrochemical, lab-supply and
    # research sites therefore don't count, even with a price.
    price_num = pd.to_numeric(listings["price"].astype(str).str.extract(r"(\d+(?:[.,]\d+)?)")[0]
                              .str.replace(",", "."), errors="coerce")
    listings["has_price"] = price_num.fillna(0) > 0
    # >= 2 priced products labelled live AND >= 15% of the shop's priced
    # product matches (one stray "larvae" in a paper title is not enough)
    priced = listings[(listings["page_type"] == "product") & listings["has_price"]]
    st = priced.groupby("domain").agg(n=("url", "nunique"),
                                      n_live=("life_state", lambda s: int((s == "live").sum())))
    by_listings = set(st.index[(st["n_live"] >= 2) & (st["n_live"] / st["n"] >= 0.15)])
    by_discovery = set(shops.loc[shops["life_state"] == "live", "domain"]) if "life_state" in shops else set()
    live_shops = by_listings | by_discovery
    listings["live_animal_shop"] = listings["domain"].isin(live_shops)
    title_hit = listings["match_type"].isin(["scientific name", "common name"])
    base = (listings["page_type"].isin(SALE_TYPES) & listings["has_price"]
            & listings["live_animal_shop"])
    # specimen dealers: at least as many dead- as live-labelled priced products;
    # their listings without a live label are probably specimens
    dead_heavy = priced.groupby("domain")["life_state"].agg(
        lambda s: int((s == "dead").sum()) >= max(1, int((s == "live").sum())))
    listings["specimen_dealer"] = listings["domain"].isin(set(dead_heavy.index[dead_heavy]))
    # hand-curated shop types (3_metadata/shop_type_overrides.csv)
    ov_file = config.META_DIR / "shop_type_overrides.csv"
    ov = pd.read_csv(ov_file, dtype=str).fillna("") if ov_file.exists() else pd.DataFrame(columns=["domain", "type"])
    ov_type = dict(zip(ov["domain"], ov["type"]))
    listings["shop_type"] = listings["domain"].map(ov_type).fillna("")
    listings = listings[listings["shop_type"] != "not a shop"]
    base = base.reindex(listings.index)
    title_hit = title_hit.reindex(listings.index)
    listings.loc[listings["shop_type"] == "dead goods", "specimen_dealer"] = True
    listings.loc[listings["shop_type"] == "live supplier", "live_animal_shop"] = True
    base = (listings["page_type"].isin(SALE_TYPES) & listings["has_price"]
            & listings["live_animal_shop"])

    # biocontrol product whose title names the pest as its target
    from .classify import BIOCONTROL_RX
    target_only = [bool(BIOCONTROL_RX.search(t)) and str(s).split()[0].lower() not in t.lower()
                   for t, s in zip(listings["title"].astype(str), listings["species"].astype(str))]
    title_hit = title_hit & ~pd.Series(target_only, index=listings.index)
    listings["evidence"] = "weak"
    # the name must be in the title or URL: a name only in the description is
    # often the target pest of a product (biocontrol, pesticide), not the product
    listings.loc[base & title_hit & ~listings["life_state"].eq("dead")
                 & ~listings["specimen_dealer"], "evidence"] = "probable"
    listings.loc[base & title_hit & listings["life_state"].eq("live")
                 & ~listings["shop_type"].eq("dead goods"), "evidence"] = "strong"
    listings.loc[listings["life_state"].eq("dead") | listings["shop_type"].eq("dead goods"),
                 "evidence"] = "dead"
    listings = listings.sort_values(["regulated", "species", "domain"], ascending=[False, True, True])
    listings.to_csv(config.DATA_DIR / "web_listings.csv", index=False)   # working file for R

    # evidence of sale = strong or probable (weak and dead stay in 'listings')
    sale = listings[listings["evidence"].isin(["strong", "probable"])]
    by_species = (sale.groupby("species", dropna=False)
                  .agg(regulated=("regulated", "max"),
                       eppocode=("eppocode", "first"), pest_group=("pest_group", "first"),
                       match_types=("match_type", lambda s: "; ".join(sorted(set(s)))),
                       n_shops=("domain", "nunique"), n_listings=("url", "nunique"),
                       n_live=("life_state", lambda s: int((s == "live").sum())),
                       n_dead=("life_state", lambda s: int((s == "dead").sum())),
                       shops_ship_to_norway=("ships_to_norway",
                                             lambda s: int(s.astype(str).str.startswith("yes").sum())),
                       seller_countries=("seller_country",
                                         lambda s: "; ".join(sorted({x for x in s.dropna() if x}))),
                       shops=("domain", lambda s: "; ".join(list(dict.fromkeys(s))[:15])),
                       eppo_list=("eppo_list", "first"), eu_list=("eu_list", "first"),
                       no_list=("no_list", "first"),
                       species_life_state_R=("species_life_state_R", "first"))
                  .reset_index()
                  .sort_values(["regulated", "n_shops", "n_live"], ascending=[False, False, False]))
    regulated = sale[sale["regulated"]].copy()

    hits_per_shop = (sale.groupby("domain")
                     .agg(n_species=("species", "nunique"),
                          n_regulated=("species", lambda s: s[sale.loc[s.index, "regulated"]].nunique()),
                          regulated_species=("species", lambda s: "; ".join(sorted(set(
                              s[sale.loc[s.index, "regulated"]]))))).reset_index())
    shops_df = (shops_df.merge(hits_per_shop, on="domain", how="left")
                .fillna({"n_species": 0, "n_regulated": 0, "regulated_species": ""})
                .sort_values(["n_regulated", "n_species"], ascending=False))

    new = pd.DataFrame([n for r in res for n in r.get("new_names", [])])
    if not new.empty:
        new = (new.groupby("name").agg(n_shops=("domain", "nunique"), n_pages=("url", "nunique"),
                                       live_pages=("life_state", lambda s: int((s == "live").sum())),
                                       example_title=("title", "first"), example_url=("url", "first"),
                                       shops=("domain", lambda s: "; ".join(list(dict.fromkeys(s))[:10])))
               .reset_index().sort_values("n_shops", ascending=False))

    skipped = shops_df[~shops_df["status"].eq("ok")][["domain", "status"]]
    guide = pd.DataFrame([
        ("regulated_on_sale", "Listings of regulated species (EPPO A1/A2, EU, Norway, or whole-genus listing) on product/category pages"),
        ("species_on_sale", "One row per species found on product/category pages: shops, listings, live/dead counts, shops that mention shipping to Norway"),
        ("listings", "All matched pages with an evidence grade; only strong/probable count as on sale"),
        ("evidence", "strong: live-animal shop, priced product page, labelled live. probable: same, label unknown, species name in title. weak: anything else (name only in description, no price, accessory/info page, or not a live-animal shop such as agrochemical, lab-supply or research sites). dead: labelled dead"),
        ("live_animal_shop", "Shop has a priced product labelled live, or was labelled live at discovery"),
        ("shops", "Every shop crawled: platform, method, catalogue size, seller country, ships to Norway, shipping scope, evidence text"),
        ("new_latin_names", "Latin names of known genera found on product pages but NOT in our species list: candidates to add"),
        ("match_type", "scientific name (title/URL), scientific name (description), or common name (GBIF vernacular, weaker)"),
        ("page_type", "product / category = sale evidence; info (blog, care sheet), accessory (food, cages ...), page = other"),
        ("life_state", "live / dead / check / unknown from title, category, URL and start of description (3_metadata/life_state_terms.csv); 'dead on arrival' guarantees count as live"),
        ("ships_to_norway", "From shipping/terms pages: 'yes (mentioned)', 'no' (e.g. 'we do not ship to Norway'), 'no (EU only)', 'unclear'. Heuristic: check evidence"),
        ("seller_country", "From imprint address, else country domain, else currency"),
        ("Method", "robots.txt obeyed; >= 1.5 s between requests per shop; marketplaces whose terms forbid scraping are skipped (see 'skipped'). Shopify/WooCommerce catalogue JSON, else sitemaps, else link crawl. No JavaScript rendering (blocked on this PC)."),
    ], columns=["item", "meaning"])

    out = config.TABLES_DIR / "species_on_sale.xlsx"
    write_xlsx({"regulated_on_sale": regulated, "species_on_sale": by_species, "listings": listings,
                "shops": shops_df, "new_latin_names": new, "skipped": skipped, "column_guide": guide}, out)
    print(f"\nShops: {len(shops_df)} ({(shops_df['status'] == 'ok').sum()} crawled) | "
          f"listings on sale pages: {len(sale)} | species: {sale['species'].nunique()} | "
          f"regulated species: {regulated['species'].nunique()} | new Latin names: {len(new)}")
    print(f"Written: {out}")
