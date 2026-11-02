# Online insect sales: plant pest screening (exploratory)

## Goal
Find out whether insects sold online **alive** are known or potential plant pests.
Norway first, EU second. This is exploration, so keep things light and reproducible.

## Scope rules (important)
- **Plant pests only.** Keep insects, mites (Acarida, Mesostigmata, Trombidiformes, Sarcoptiformes), land snails and slugs (Stylommatophora/Sigmurethra, Systellommatophora), apple snails (Architaenioglossa / Ampullariidae, e.g. *Pomacea*) and nematodes. Drop crustaceans, spiders, scorpions, ticks, centipedes, marine and aquarium molluscs, corals etc., even if they are EU-listed invasives (e.g. *Eriocheir sinensis*). Keep names with unknown class, and label them.
- **Live specimens only.** Dead material (pinned, papered, mounted, framed, ex-pupa/ex-larva specimens, collection, bodies, jewellery, figures) carries no plant-pest risk. **Label it as dead; don't delete it.** Keep unknown.
- Life-state rule (per species): `live` if any live evidence; else `unknown` if any evidence of unclear form (e.g. food use, LEMIS unspecified); else `dead` if there is dead evidence; else `unknown`.
- **Phase 1 (Norway):** sellers in Norway or shipping to Norway (Finn.no, Norwegian hobby shops, foreign shops that ship to NO). **Phase 2 (EU):** same pipeline, more platforms.

## Folder layout
One folder per publication in `1_raw_data/`, named `author+year_source+id`. Paper PDFs are in `3_metadata/pdf/` as `Author_Year_topic.pdf`. Every source with its DOI is in `3_metadata/data_sources.md`. The running summary for people is in `Trade_notes.Rmd`.
- `1_raw_data/hughes2026_figshare_33194898/`: Hughes et al. 2026, *Sci. Adv.* 12:eaei3504. `upSetData_singleNames_inv_comp.csv` (10,682 names) with 0/1 flags per source (CITES Trade, CITES App, LEMIS, LEMIS_noS, IUCN, TRAFFIC, Research). `Data_S1 - sourceSpeciesLists/` holds the raw lists. **The flags don't say live or dead.**
- `1_raw_data/lassaline2025_sciencedirect/`: `mmc4.xlsx`, 4,738 taxa with `reported_use`. Collection = dead; pet, biocontrol, pollination, exhibit, aquarium = live; food and medical = unknown.
- `1_raw_data/wang2023_zenodo_7825674/`: 51,348 eBay butterfly sales from 2022. **All dead specimens** (papered, unmounted, framed; "ex pupa"/"ex larva" = reared then mounted). No Norwegian sellers.
- `1_raw_data/marshall2022_zenodo_5758541/`: arachnid online-trade data and R code. Code S01–S05 is a web search and keyword pipeline, reusable for step 2.
- `1_raw_data/eskew2020_lemis_github/lemis.fst`: LEMIS, US imports 2000–2014. Live = `LIV`/`EGL`/`DEA`; `UNS` = unspecified; other codes = dead or products.
- `1_raw_data/eppo db/eppocodes.sqlite`: local EPPO codes (names, synonyms, taxonomy; rank is the last letter of SIT codes).
- `2_processed_data/`: intermediate tables and API caches (`eppo_api_cache/`, `gbif_cache/`). `5_outputs/tables/`: results. `3_metadata/synonym_review.csv`: manual check of EPPO synonym matches.

## Pipeline (R, `4_scripts/`, run in order)
1. `01_match_article_eppo.R`: Hughes names to EPPO codes by exact name and synonym (no code guessing), with EPPO taxonomy. Output: `article_eppo_match.csv`.
2. `02_lemis_live.R`: live LEMIS imports per species, with EPPO codes. Output: `lemis_live_species.csv`, `article_eppo_lemis.csv`.
3. `03_eppo_gdapi.R`: EPPO GDAPI v2 (direct httr2, `X-Api-Key`; key from `EPPO_API_KEY` or the OneDrive key file) for categorization, hosts and distribution, for Hughes and LEMIS codes. Cached, prints a counter. Output: `eppo_code_summary.csv`, `article_eppo_enriched.csv`, long tables.
4. `04_gbif_names.R`: GBIF backbone match (direct API, cached) for class/order and accepted names. Output: `gbif_names.csv`.
5. `05_scope_life_state.R`: plant-pest scope + life state from LEMIS (all forms), Lassaline and Wang; merges synonyms by GBIF accepted name. Output: `species_scope_state.csv`, `species_accepted_state.csv`, `5_outputs/tables/plant_pest_candidates.csv` (in scope, live or unknown).
6. Next: tiering `06_tier.R`:
   - A: known pest (EPPO A1/A2, Norwegian or EU regulated)
   - B: regulated elsewhere (quarantine or A1 in another country)
   - C: potential (phytophagous group, or EPPO host plant page)
   - D: no known plant risk
   Modifiers: live evidence, ships to NO/EU, host availability, climate match.
7. Feed Pathway (R Shiny, FinnPRIO ENT3) with the ranked list.

Listing collection (Python, project root): `insect_trade_loop.py` (shops), `platform_search.py` (marketplaces), `commoncrawl_search.py` (Common Crawl). Outputs go to `5_outputs/tables/`. All share `life_state.py`, which labels each row `live` / `dead` / `check` / `unknown` from multilingual keywords (EN, NO, SV, DA, DE, NL, FR, ES, PL) in `life_state` and `life_terms`. Keys are in `.env`. Still to add: Finn.no, Norwegian shops, a `ships_to` field.

## Findings so far
- 10,851 accepted species (after merging synonyms). In scope: ~6,100 insects, 394 snails/slugs, 23 nematodes, 10 mites, 308 unknown class. **3,408 candidates** (in scope, live or unknown).
- Live evidence for **regulated** in-scope species: the ants *Solenopsis invicta*, *S. geminata*, *Wasmannia auropunctata* and *Pachycondyla chinensis* (Lassaline pet, EU IAS Union concern), and *Pomacea canaliculata* (A1) and *P. maculata* (A2) (pet). Ant-keeping shops are a priority.
- Regulated with unknown form: *Spodoptera frugiperda*, *Rhynchophorus ferrugineus*/*palmarum* (food), *Lycorma delicatula*, *Anoplophora glabripennis*, *Popillia japonica*, *Ceratitis capitata*, *Bactrocera tryoni* etc. LEMIS shows mostly dead specimens for these.
- Live and EPPO-relevant but not regulated in NO/EU: *Papilio demoleus* (243 live LEMIS shipments), *Cydia pomonella*, giant African snails (*Lissachatina fulica*, *Achatina achatina*), *Theba pisana*, *Cornu aspersum*, *Harmonia axyridis*, and many biocontrol agents (EPPO PM 6/3 list).
- 3,836 Hughes names flagged `LEMIS` were never imported live. 389 live-imported LEMIS species are not in the Hughes list.
- Of 710 shops found so far, keyword labels give 345 live, 332 unknown, 19 dead and 14 check.
- 5 EPPO synonym matches need checking (`3_metadata/synonym_review.csv`); none is regulated.

## Conventions
- R first. Plots with ggplot2 and viridis colour scales.
- Keep outputs short and concrete.
- Read processed CSVs with `guess_max = Inf`; sparse text columns otherwise get parsed as logical and blanked.
- Call APIs directly (httr2), no wrapper packages. Cache responses.
- Flag uncertainty: unresolved names, mislabelled listings, missing EPPO matches.
- Do not store seller personal data beyond what the analysis needs (LEMIS company fields are not read).

## Open questions
- Do genus-level EPPO matches (2,951 names) count for tier C?
- Should *food* use count as live (live feeders) or unknown? Currently unknown.
- Which platforms are in scope, and does their terms of service allow scraping?
- Is LEMIS data after 2014 available (Marshall et al. 2025, *PNAS*)?
- Acceptable false-positive rate for extracting species names from listings?
