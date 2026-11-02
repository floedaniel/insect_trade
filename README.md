# Online insect trade: plant-pest screening

Exploratory screening of **live** invertebrates sold online that could be
**plant pests** in Norway (Phase 1) and the EU (Phase 2).

Two rules apply throughout:

1. **Plant pests only.** Insects, mites, land snails and slugs, apple snails
   (*Pomacea*) and nematodes are in scope. Crustaceans, spiders, scorpions,
   ticks and marine or aquarium molluscs are out, even when they are listed
   invasive species. Names with unknown class are kept and labelled.
2. **Live specimens only.** Dead material (pinned, papered, mounted, framed,
   "ex pupa" specimens, collection material, bodies, jewellery) carries no
   plant-pest risk. It is **labelled `dead`, not deleted**. `unknown` is kept.

The result is a ranked list of candidate species: in scope, and traded live or
in unknown form, with their EPPO/EU/Norwegian regulatory status and host plants.

---

## Quick start

```r
# R >= 4.2, run from the project root (open insect_trade.Rproj)
install.packages(c("here", "dplyr", "readr", "stringr", "tidyr", "purrr",
                   "httr2", "jsonlite", "DBI", "RSQLite", "fst", "readxl"))

source("4_scripts/01_match_article_eppo.R")  # names -> EPPO codes (local)
source("4_scripts/02_lemis_live.R")          # live US imports (LEMIS)
source("4_scripts/03_eppo_gdapi.R")          # EPPO Global Database API
source("4_scripts/04_gbif_names.R")          # GBIF backbone names
source("4_scripts/05_scope_life_state.R")    # scope + live/dead labels
# 06 crawls shops: run from a terminal (long, parallel)
#   Rscript 4_scripts/06_species_by_website.R live 8
```

Main output: `5_outputs/tables/plant_pest_candidates.xlsx` (sheets: candidates, regulated, all_species, column_guide). **All results are `.xlsx` only:** `species_by_website.xlsx`, `lemis_live_species.xlsx`, `gbif_names.xlsx`, `discovered_shops.xlsx`. Csv files exist only as internal working files in `2_processed_data/`.

**Results are not in the repository.** `5_outputs/` is created when you run
the pipeline. It is gitignored because the shop and listing tables hold
third-party listing text, the candidate table is built from EPPO data, and the
results are exploratory.

Steps 03 and 04 call web APIs. Every response is cached in `2_processed_data/`,
so later runs are fast and work offline.

### Before the first run

1. **Raw data are not in the repository.** Download them into `1_raw_data/`
   (one folder per source), as listed in [Data](#data).
2. **EPPO API key.** Create a token at <https://data.eppo.int> and set it in
   `~/.Renviron`:
   ```
   EPPO_API_KEY=your_token
   ```
   Script 03 reads only this variable.
3. **EPPO codes database.** Download the SQLite version of the EPPO codes from
   <https://data.eppo.int> to `1_raw_data/eppo db/eppocodes.sqlite`.

---

## Pipeline

| Step | Script | Does | Output (`2_processed_data/` unless noted) |
|---|---|---|---|
| 1 | `01_match_article_eppo.R` | Matches the Hughes et al. 2026 names (inverts + Lepidoptera) to EPPO codes by **exact name or synonym** (no code guessing). Adds EPPO taxonomy | `article_eppo_match.csv` |
| 2 | `02_lemis_live.R` | Live US imports 2000–2022 (Marshall et al. 2025 corrected LEMIS) (LEMIS codes `LIV`, `EGL`, `DEA`), per species: shipments, individuals, years, origin countries | `lemis_live_species.csv`, `article_eppo_lemis.csv` |
| 3 | `03_eppo_gdapi.R` | EPPO GDAPI v2 for all matched codes: categorization (A1/A2, EU, national lists), host plants, distribution. Prints a counter | `eppo_code_summary.csv`, `eppo_categorization.csv`, `eppo_hosts.csv`, `eppo_distribution.csv` |
| 4 | `04_gbif_names.R` | GBIF backbone match for class/order, accepted names and **GBIF species keys** (for later occurrence/distribution queries). Names the backbone only knows at genus level get a second try through Catalogue of Life and GBIF synonym search (flagged "check") | `gbif_names.csv`, `5_outputs/tables/gbif_names.xlsx` |
| 5 | `05_scope_life_state.R` | Plant-pest scope; live/dead/unknown per species from all evidence; merges synonyms by GBIF accepted name | `species_scope_state.csv`, `species_accepted_state.csv`, **`5_outputs/tables/plant_pest_candidates.csv`** |
| 6 | `06_species_by_website.R` | **Ties species to real sale pages.** For each discovered shop: obeys robots.txt, reads product sitemaps (or links when there is no sitemap), finds candidate names in URLs, image titles and link text, fetches the matching pages (regulated species first) and records title, price, page type, live/dead label and ships-to hints. Names: scientific names and synonyms, plus GBIF common names in 11 languages (weaker evidence) | `5_outputs/tables/species_by_website.xlsx` (hits, by_species, by_shop, shop_status, column_guide) |
| 7 | *planned* `07_tier.R` | Tiers A–D (below) | |

APIs are called directly with `httr2`, without wrapper packages.

### How life state is decided

Each species gets one label from all the evidence we have:

| Evidence | Live | Dead | Unknown form |
|---|---|---|---|
| LEMIS (US imports) | `LIV`, `EGL`, `DEA` (died in transit) | bodies, specimens, shells, jewellery, products | `UNS` (unspecified) |
| Lassaline et al. 2025 (`reported_use`) | pet, pet food (live feeders), biocontrol, pollination, honey, exhibit, aquarium | collection | food (human food, form unclear), medical, venom, … |
| Wang et al. 2023 (eBay butterflies) | n/a | all sales (papered/mounted specimens) | n/a |
| Listings (`life_state.py`) | live, eggs, larvae, pupae, colony, live food, feeder insects, … | pinned, papered, mounted, ex pupa, … | no terms |
| Hughes et al. 2026 flags | n/a | n/a | all flags (they don't say live or dead) |

Rule: **live** if any live evidence; otherwise **unknown** if any evidence of
unknown form; otherwise **dead** if any dead evidence; otherwise **unknown**.
The columns `live_evidence`, `unknown_evidence` and `dead_evidence` say why.

### Planned tiers

- **A** known pest: EPPO A1/A2, Norwegian or EU regulated
- **B** regulated elsewhere: quarantine or A1 in another country
- **C** potential: phytophagous group, or EPPO host-plant records
- **D** no known plant risk

Modifiers: live evidence, ships to NO/EU, host plants in Norway, climate match.
The ranked list feeds Pathway (FinnPRIO ENT3).

---

## Listing collection (Python)

Run from the project root. Keys (`TAVILY_API_KEY`, `ANTHROPIC_API_KEY`) go in
`.env`, which is gitignored.

```bash
pip install -r requirements.txt
playwright install chromium

python insect_trade_loop.py     # find specialist shops (one row per domain)
python platform_search.py       # species listings on marketplaces
python commoncrawl_search.py    # sale pages in Common Crawl
```

All three write their results as `.xlsx` to `5_outputs/tables/` (working csv in `2_processed_data/`) and label every row through
`life_state.py`:

| `life_state` | Meaning |
|---|---|
| `live` | live terms: live, eggs/ova, larvae, pupae, cocoons, colony, queen, L2, captive bred, … |
| `dead` | dead terms: pinned, papered, unmounted, framed, A1, ex pupa, dried, resin, pendant, figure, … |
| `check` | both kinds of term; needs a look |
| `unknown` | no terms |

The keywords are in `3_metadata/life_state_terms.csv`, shared by the Python
scripts and script 06. They cover English, Norwegian, Swedish, Danish, German, Dutch, French,
Spanish and Polish. The matched words are kept in `life_terms`. To test the
classifier: `python life_state.py`.

Respect each site's terms of service. Store no seller personal data beyond what
the analysis needs.

---

## Web scraper: species on sale (`insect_scraper/`)

The main tool for tying species to real sale pages. It replaces the lighter R
crawler in `4_scripts/06_species_by_website.R`. It needs the R pipeline 01–05
first, for the species list.

```bash
pip install --user -r requirements.txt
py -3 -m insect_scraper discover --max-queries 400   # find more shops
py -3 -m insect_scraper crawl --workers 16           # crawl all shops (resumes)
py -3 -m insect_scraper report                       # write the xlsx again
py -3 -m insect_scraper all                          # all three
py -3 -m pytest tests                                # unit tests
```

**How it works**

1. **Shops:** `discovered_shops.csv` (from `insect_trade_loop.py`) plus
   `discover`: multilingual searches for regulated and live-traded species
   (scientific and common names) and generic live-insect shop terms, Norwegian
   and Nordic first. Engines: DuckDuckGo/Bing/Yahoo (free, via `ddgs`) and
   Tavily (if `TAVILY_API_KEY` is in `.env`). Each query runs once.
2. **Full catalogue per shop:** Shopify and WooCommerce shops publish their
   whole catalogue as JSON (`/products.json`, `/wp-json/wc/store/products`).
   Other shops: product sitemaps, else a link crawl. Product pages give
   structured data (JSON-LD, microdata, OpenGraph): name, price, currency, stock.
3. **Matching:** all ~6,800 in-scope plant-pest species, including those
   labelled dead (a live listing upgrades them): scientific names, synonyms,
   EPPO names (also in URLs and descriptions), and GBIF common names in 11
   languages (plurals; Nordic letters; one-word names only if clearly an animal
   word, such as "superworm"). A common name shared by several species of one
   genus becomes a genus hit ("*Pomacea* spp.").
4. **Labels:** page type (product, category, info, accessory), live/dead
   (shared keyword file; "dead on arrival" guarantees count as live), price.
5. **Shop facts:** ships to Norway (yes / no / no (EU only) / unclear, with the
   evidence sentence), shipping scope (worldwide, EU, domestic only), seller
   country (imprint address, domain or currency). Only country codes are kept.
6. **New names:** Latin names of in-scope genera found on product pages but
   missing from our species list, as candidates to add.

**Rules:** robots.txt is obeyed (our agent name `insect-trade-research` or `*`,
plus Crawl-delay); at least 1.5 s between requests to one shop; responses are
cached for 30 days (`2_processed_data/scrape_cache.sqlite`). Marketplaces whose
terms forbid automated collection (Finn.no, eBay, Amazon, Etsy, AliExpress,
Facebook, Chewy, Blocket, DBA, Kleinanzeigen, Marktplaats ...) are skipped and
listed. No JavaScript rendering: this PC's group policy blocks headless
browsers, so shops that only render products with JavaScript are missed.

**Output:** `5_outputs/tables/species_on_sale.xlsx` with sheets
`regulated_on_sale`, `species_on_sale`, `listings`, `shops`, `new_latin_names`,
`skipped` and `column_guide`.

---

## Data

Raw data are **not in the repository**. Get them from the sources below. Full
details are in [`3_metadata/data_sources.md`](3_metadata/data_sources.md).

| Source | Folder in `1_raw_data/` | Get it from |
|---|---|---|
| Hughes et al. 2026, *Sci. Adv.* 12:eaei3504: traded species per data source | `hughes2026_figshare_33194898/` | figshare doi:10.26188/33194898 |
| Lassaline et al. 2025, *Biol. Conserv.* 308:111266: terrestrial invertebrate trade review (`mmc4.xlsx`) | `lassaline2025_sciencedirect/` | Supplementary data at doi:10.1016/j.biocon.2025.111266 |
| Wang et al. 2023, *Biol. Conserv.* 283:110092: eBay butterfly sales 2022 | `wang2023_zenodo_7825674/` | Zenodo doi:10.5281/zenodo.7825674 |
| Marshall et al. 2022, *Commun. Biol.* 5:448: arachnid web trade, R scraping code | `marshall2022_zenodo_5758541/` | Zenodo doi:10.5281/zenodo.5758541 |
| LEMIS 2000–2022, corrected (Marshall et al. 2025, *PNAS* 122:e2410774121) | `marshall2025_figshare_25041584/lemisDataCorrected_2023-11-11.csv.gz` | figshare doi:10.6084/m9.figshare.25041584 (CC BY 4.0) |
| LEMIS 2000–2014 (Eskew et al. 2020, *Sci. Data*), older version, no longer used by the pipeline | `eskew2020_lemis_github/lemis.fst` | github.com/eveskew/lemis, release v1.1.0 |
| EPPO codes | `eppo db/eppocodes.sqlite` | data.eppo.int |
| EPPO Global Database | API (cached) | api.eppo.int/gd/v2 (token) |
| GBIF backbone | API (cached) | api.gbif.org/v1/species/match |

## Folder layout

```
1_raw_data/          raw data, one folder per publication (not in git)
2_processed_data/    intermediate tables and API caches (not in git)
3_metadata/          data_sources.md, synonym_review.csv, pdf/ (pdf not in git)
4_scripts/           R pipeline 01–06
5_outputs/tables/    results, created by the pipeline (not in git)
*.py, life_state.py  shop discovery scripts (Python)
insect_scraper/      web scraper: species on sale (Python)
tests/               unit tests for the scraper
Trade_notes.Rmd      running notes: status, findings, to-do
```

## Status and findings

See [`Trade_notes.Rmd`](Trade_notes.Rmd) for current numbers, findings and the
to-do list. In short (November 2026):

- 10,859 species (after merging synonyms) → **2,373 candidates** in scope and
  traded live or in unknown form.
- **11 regulated plant pests with live trade**, through two pathways:
  - **pets:** ants *Solenopsis invicta*, *S. geminata*, *Wasmannia auropunctata*,
    *Pachycondyla chinensis* (EU IAS of Union concern) and apple snails
    *Pomacea canaliculata* (EPPO A1), *P. maculata* (A2), *P. diffusa*
    (genus *Pomacea* EU-listed);
  - **lab/rearing imports** (LEMIS 2015–2022): *Spodoptera frugiperda*,
    *Helicoverpa armigera*, *Lycorma delicatula*, *Diabrotica virgifera*.
- Most traded insect species show only **dead** trade (specimens).
- No Norwegian listings collected yet. Finn.no, Norwegian shops and a
  `ships_to` field are next.

## Caveats

- LEMIS covers **US imports only**, 2000–2022. Lassaline and Wang describe
  trade reported in the literature or on eBay, not current sales to Norway.
- EPPO matching is exact. Names EPPO doesn't know, or knows only at genus level,
  have no regulatory data yet. Five synonym matches are uncertain
  (`3_metadata/synonym_review.csv`).
- Live/dead keyword labels for listings are heuristic. `check` rows need a
  manual look.
