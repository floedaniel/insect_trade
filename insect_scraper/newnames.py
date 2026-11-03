"""Resolve Latin names found on shop pages that are not in our species list.

  py -3 -m insect_scraper newnames

For every name collected by the crawler (ShopResult.new_names):
  1. GBIF backbone match (cached, same cache as 4_scripts/04_gbif_names.R):
     valid animal species? accepted name, class, order, family, species key
  2. compare with our species list: synonym of a listed species, a new species
     in the plant-pest scope, out of scope, or not a valid animal species name
  3. new in-scope species: EPPO code (local sqlite, exact name or synonym) and
     EPPO GDAPI categorization + host plants (cached; key from EPPO_API_KEY or
     ~/.Renviron) -> regulated here / regulated elsewhere / potential / none
Outputs: 5_outputs/tables/new_latin_names_resolved.xlsx (xlsx only)
         2_processed_data/web_new_species.csv (working file: new in-scope
         species, used by names.py for later crawls and by 07_main_results.R)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import httpx
import pandas as pd

from . import config
from .report import write_xlsx

RESULTS_DIR = config.DATA_DIR / "scrape_results"
GBIF_CACHE = config.DATA_DIR / "gbif_cache"
EPPO_CACHE = config.DATA_DIR / "eppo_api_cache"
EPPO_DB = config.PROJECT_DIR / "1_raw_data" / "eppo db" / "eppocodes.sqlite"
OUT_CSV = config.DATA_DIR / "web_new_species.csv"

MITE_ORDERS = {"Acarida", "Acari", "Acarina", "Mesostigmata", "Trombidiformes",
               "Sarcoptiformes", "Prostigmata"}
SNAIL_ORDERS = {"Stylommatophora", "Sigmurethra", "Systellommatophora", "Architaenioglossa"}
NEMATODES = {"Chromadorea", "Secernentea", "Enoplea", "Nematoda"}
PF_ORDERS = {"Lepidoptera", "Orthoptera", "Phasmida", "Phasmatodea", "Thysanoptera"} | SNAIL_ORDERS
PF_FAMILIES = {"Curculionidae", "Chrysomelidae", "Cerambycidae", "Scarabaeidae", "Buprestidae",
               "Bostrichidae", "Elateridae", "Tenebrionidae", "Aphididae", "Aleyrodidae",
               "Pseudococcidae", "Diaspididae", "Coccidae", "Cicadellidae", "Delphacidae",
               "Psyllidae", "Miridae", "Pentatomidae", "Tephritidae", "Agromyzidae",
               "Cecidomyiidae", "Anthomyiidae", "Tenthredinidae", "Cynipidae", "Formicidae",
               "Tetranychidae", "Eriophyidae", "Tenuipalpidae", "Acrididae", "Gryllidae",
               "Gryllotalpidae", "Lymantriidae", "Ampullariidae", "Achatinidae", "Helicidae"}


def _cache_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s) + ".json"


def _eppo_key() -> str:
    k = os.environ.get("EPPO_API_KEY", "")
    if not k:
        for f in (Path.home() / ".Renviron", Path.home() / "Documents" / ".Renviron",
                  Path(os.environ.get("USERPROFILE", "")) / "OneDrive - Folkehelseinstituttet" / "Documents" / ".Renviron"):
            if f.exists():
                m = re.search(r"^EPPO_API_KEY=(.+)$", f.read_text(encoding="utf-8"), re.M)
                if m:
                    return m.group(1).strip()
    return k


def collect() -> pd.DataFrame:
    """Names with the same evidence rules as listings: name in the page TITLE,
    a product/category page that is not an accessory (pesticide, trap, PCR kit
    ...), in a live-animal shop, not an excluded site (shop_type_overrides)."""
    from . import classify
    rows = []
    for f in RESULTS_DIR.glob("*.json"):
        for n in json.loads(f.read_text(encoding="utf-8")).get("new_names", []):
            rows.append(n)
    d = pd.DataFrame(rows)
    n_all = d["name"].nunique()
    # live-animal shops as decided by the report (web_listings.csv)
    wl = pd.read_csv(config.DATA_DIR / "web_listings.csv", usecols=["domain", "live_animal_shop"],
                     low_memory=False)
    live_shops = set(wl.loc[wl["live_animal_shop"].astype(str).str.upper() == "TRUE", "domain"])
    ov_file = config.META_DIR / "shop_type_overrides.csv"
    ov = pd.read_csv(ov_file, dtype=str) if ov_file.exists() else pd.DataFrame(columns=["domain", "type"])
    excluded = set(ov.loc[ov["type"].isin(["not a shop", "dead goods"]), "domain"])
    in_title = [str(nm).lower() in str(t).lower() for nm, t in zip(d["name"], d["title"])]
    ptype = [classify.page_type(u, t, True) for u, t in zip(d["url"].astype(str), d["title"].astype(str))]
    keep = (pd.Series(in_title, index=d.index) & pd.Series(ptype, index=d.index).isin(["product", "category"])
            & d["domain"].isin(live_shops) & ~d["domain"].isin(excluded) & ~d["life_state"].eq("dead"))
    d = d[keep]
    print(f"New names: {n_all} found; {d['name'].nunique()} pass the evidence rules "
          f"(name in title of a product page in a live-animal shop)")
    return (d.groupby("name")
             .agg(n_shops=("domain", "nunique"), n_pages=("url", "nunique"),
                  live_pages=("life_state", lambda s: int((s == "live").sum())),
                  dead_pages=("life_state", lambda s: int((s == "dead").sum())),
                  example_title=("title", "first"), example_url=("url", "first"),
                  shops=("domain", lambda s: "; ".join(list(dict.fromkeys(s))[:10])))
             .reset_index())


async def gbif_match(names: list[str]) -> dict[str, dict]:
    GBIF_CACHE.mkdir(exist_ok=True)
    out, todo = {}, []
    for n in names:
        f = GBIF_CACHE / _cache_name(n)
        if f.exists():
            out[n] = json.loads(f.read_text(encoding="utf-8"))
        else:
            todo.append(n)
    print(f"GBIF: {len(names)} names, {len(todo)} to fetch")
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": config.USER_AGENT}) as c:
        async def one(n):
            async with sem:
                for _ in range(3):
                    try:
                        r = await c.get("https://api.gbif.org/v1/species/match",
                                        params={"name": n, "kingdom": "Animalia", "strict": "false"})
                        if r.status_code == 200:
                            (GBIF_CACHE / _cache_name(n)).write_text(r.text, encoding="utf-8")
                            out[n] = r.json()
                            return
                    except httpx.HTTPError:
                        await asyncio.sleep(2)
        await asyncio.gather(*(one(n) for n in todo))
    return out


def eppo_lookup(names: list[str]) -> dict[str, tuple[str, str, str]]:
    """name (lower) -> (eppocode, eppo preferred name, 'exact'|'synonym')."""
    con = sqlite3.connect(EPPO_DB)
    rows = con.execute("""
        SELECT lower(n.fullname), c.eppocode, n.preferred, c.codeid FROM t_names n
        JOIN t_codes c ON c.codeid = n.codeid
        WHERE c.dtcode = 'GAI' AND c.status = 'A' AND n.status = 'A' AND n.codelang = 'la'""").fetchall()
    pref = dict(con.execute("""SELECT n.codeid, n.fullname FROM t_names n JOIN t_codes c ON c.codeid = n.codeid
        WHERE c.dtcode = 'GAI' AND n.preferred = 1 AND n.codelang = 'la'""").fetchall())
    con.close()
    best = {}
    for name, code, preferred, cid in sorted(rows, key=lambda r: -r[2]):
        best.setdefault(name, (code, pref.get(cid, ""), "exact" if preferred else "synonym"))
    return {n: best[n.lower()] for n in names if n and n.lower() in best}


async def eppo_api(codes: list[str]) -> dict[str, dict]:
    key = _eppo_key()
    EPPO_CACHE.mkdir(exist_ok=True)
    out = {}
    async with httpx.AsyncClient(timeout=30, headers={"X-Api-Key": key}) as c:
        for code in codes:
            res = {}
            for svc in ("categorization", "hosts"):
                f = EPPO_CACHE / f"{code}_{svc}.json"
                if not f.exists() and key:
                    try:
                        r = await c.get(f"https://api.eppo.int/gd/v2/taxons/taxon/{code}/{svc}")
                        f.write_text(r.text if r.status_code == 200 else "null", encoding="utf-8")
                    except httpx.HTTPError:
                        continue
                if f.exists():
                    txt = f.read_text(encoding="utf-8")
                    res[svc] = json.loads(txt) if txt not in ("", "null") else []
            out[code] = res
    return out


def scope_group(cls: str, order: str, family: str) -> str:
    if cls == "Insecta":
        return "insect"
    if cls == "Arachnida" and order in MITE_ORDERS:
        return "mite"
    if cls == "Gastropoda" and (order in SNAIL_ORDERS or family == "Ampullariidae"):
        return "snail/slug"
    if cls in NEMATODES:
        return "nematode"
    return "out of scope"


def resolve() -> None:
    names = collect()
    print(f"New names from shop pages: {len(names)}")
    gb = asyncio.run(gbif_match(names["name"].tolist()))

    acc = pd.read_csv(config.DATA_DIR / "species_accepted_state.csv", dtype=str, low_memory=False)
    sp = pd.read_csv(config.DATA_DIR / "species_scope_state.csv", dtype=str, low_memory=False,
                     usecols=["name", "gbif_accepted"])
    listed = set(acc["accepted_name"]) | set(sp["name"]) | set(sp["gbif_accepted"].dropna())
    listed_key = set(acc["gbif_key"].dropna().astype(str).str.replace(r"\.0$", "", regex=True))

    recs = []
    for _, r in names.iterrows():
        g = gb.get(r["name"], {}) or {}
        ok = (g.get("matchType") in ("EXACT", "FUZZY") and g.get("rank") in ("SPECIES", "SUBSPECIES")
              and g.get("kingdom") == "Animalia")
        accepted = g.get("species") if ok else None
        key = str(g.get("speciesKey")) if ok and g.get("speciesKey") else None
        cls, order, fam = (g.get("class") or ""), (g.get("order") or ""), (g.get("family") or "")
        if not ok:
            kingdom = g.get("kingdom") or ""
            status = ("not an animal (" + kingdom + ")") if kingdom and kingdom != "Animalia" and g.get("matchType") != "NONE" \
                else "not a valid species name"
        elif accepted in listed or (key and key in listed_key):
            status = "already in our list (synonym or same species)"
        elif scope_group(cls, order, fam) == "out of scope":
            status = "valid species, out of plant-pest scope"
        else:
            status = "NEW in-scope species"
        recs.append({**r.to_dict(), "resolution": status, "gbif_accepted": accepted, "gbif_key": key,
                     "gbif_match": g.get("matchType"), "gbif_confidence": g.get("confidence"),
                     "class": cls, "order": order, "family": fam,
                     "pest_group": scope_group(cls, order, fam) if ok else ""})
    res = pd.DataFrame(recs)

    new = res[res["resolution"] == "NEW in-scope species"].copy()
    # several shop spellings can resolve to one accepted species
    lookup = eppo_lookup(list(set(new["gbif_accepted"].dropna()) | set(new["name"])))
    def eppo_for(row):
        return lookup.get(row["gbif_accepted"]) or lookup.get(row["name"]) or ("", "", "")
    new[["eppocode", "eppo_name", "eppo_match"]] = new.apply(lambda r: pd.Series(eppo_for(r)), axis=1)
    codes = sorted({c for c in new["eppocode"] if c})
    print(f"New in-scope names: {len(new)} | with EPPO code: {len(codes)}")
    api = asyncio.run(eppo_api(codes))

    def cat_summary(code):
        if not code or code not in api:
            return pd.Series({"eppo_list": "", "eu_list": "", "no_list": "", "n_countries_listed": 0,
                              "n_hosts": 0, "hosts": "", "tier": ""})
        cat = [c for c in api[code].get("categorization") or [] if not c.get("year_delete")]
        lab = lambda iso: "; ".join(sorted({c.get("qlist_label", "") for c in cat if c.get("country_iso") == iso}))
        eppo_a12 = any(c.get("country_iso") == "9A" and c.get("qlist") in ("1", "2") for c in cat)
        eu, no = lab("9L"), lab("NO")
        n_c = len({c.get("country_iso") for c in cat if not str(c.get("country_iso", "")).startswith("9")})
        hosts = api[code].get("hosts") or []
        return pd.Series({"eppo_list": lab("9A"), "eu_list": eu, "no_list": no, "n_countries_listed": n_c,
                          "n_hosts": len(hosts), "hosts": "; ".join(h.get("prefname", "") for h in hosts[:15]),
                          "tier": ("Regulated here" if eppo_a12 or eu or no else
                                   "Regulated elsewhere" if n_c else "")})
    new = pd.concat([new.reset_index(drop=True), new["eppocode"].apply(cat_summary).reset_index(drop=True)], axis=1)
    pf = new["family"].isin(PF_FAMILIES) | new["order"].isin(PF_ORDERS) | new["pest_group"].eq("snail/slug")
    new["tier"] = new["tier"].mask(new["tier"].eq("") & (new["n_hosts"].astype(int) > 0), "Potential plant pest")
    new["tier"] = new["tier"].mask(new["tier"].eq("") & pf, "Potential plant pest")
    new["tier"] = new["tier"].mask(new["tier"].eq(""), "No known plant risk")
    tier_order = {"Regulated here": 0, "Regulated elsewhere": 1, "Potential plant pest": 2, "No known plant risk": 3}
    new = new.sort_values(["tier", "live_pages", "n_shops"], key=lambda s: s.map(tier_order) if s.name == "tier" else -s)

    # one row per accepted species for the pipeline (merge shop spellings)
    per_sp = (new.groupby("gbif_accepted")
              .agg(names_on_shops=("name", lambda s: "; ".join(sorted(set(s)))),
                   n_shops=("n_shops", "sum"), n_pages=("n_pages", "sum"), live_pages=("live_pages", "sum"),
                   gbif_key=("gbif_key", "first"), pest_group=("pest_group", "first"),
                   **{c: (c, "first") for c in ["class", "order", "family", "eppocode", "eppo_name",
                                                "eppo_list", "eu_list", "no_list", "n_countries_listed",
                                                "n_hosts", "hosts", "tier", "example_title", "example_url"]})
              .reset_index().rename(columns={"gbif_accepted": "species"}))
    # cumulative: species found earlier stay in the list. Once they are in the
    # name index, later crawls match them as ordinary species (web_listings),
    # so they no longer appear as "new names" here.
    if OUT_CSV.exists():
        old = pd.read_csv(OUT_CSV)
        per_sp = pd.concat([per_sp, old[~old["species"].isin(per_sp["species"])]], ignore_index=True)
    per_sp.to_csv(OUT_CSV, index=False)

    summary = (res["resolution"].value_counts().rename_axis("resolution").reset_index(name="names"))
    tiers = per_sp["tier"].value_counts().rename_axis("tier").reset_index(name="new species")
    guide = pd.DataFrame([
        ("resolution", "GBIF backbone match (animals): not a valid species name (e.g. 'Formica laminate', brand words), not an animal (plants, bacteria: 'Ficus elastica', 'Bacillus thuringiensis'), already in our list (synonym or same species), valid but out of plant-pest scope, or NEW in-scope species"),
        ("new_species", "One row per new in-scope accepted species with EPPO code and status (local EPPO database + EPPO API) and a draft tier"),
        ("tier", "Regulated here (EPPO A1/A2, EU, Norway) / Regulated elsewhere / Potential plant pest (EPPO hosts or plant-feeding family/order) / No known plant risk"),
        ("live_pages", "Shop pages for the name labelled live"),
    ], columns=["item", "meaning"])
    out = config.TABLES_DIR / "new_latin_names_resolved.xlsx"
    write_xlsx({"summary": summary, "new_species_by_tier": tiers, "new_species": per_sp,
                "all_names": res.sort_values(["resolution", "n_shops"], ascending=[True, False]),
                "column_guide": guide}, out)
    print(summary.to_string(index=False))
    print(tiers.to_string(index=False))
    print(f"Written: {out}\nWorking file: {OUT_CSV}")
