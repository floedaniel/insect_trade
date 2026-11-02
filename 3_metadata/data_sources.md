# Data sources

Downloaded 2026-11-01/02. Live = whether the source tells live from dead trade.

| Source | Paper | Data | Local folder | Live? |
|---|---|---|---|---|
| Hughes et al. 2026 | *Sci. Adv.* 12:eaei3504, doi 10.1126/sciadv.aei3504 | figshare doi 10.26188/33194898 ("Appearance of species in trade data sources"). Preprint and Google Drive files: ecoevorxiv.org/repository/view/11272/ | `1_raw_data/hughes2026_figshare_33194898/` | No |
| Lassaline et al. 2025 | *Biol. Conserv.* 308:111266, doi 10.1016/j.biocon.2025.111266 (CC-BY) | ScienceDirect supplement, downloaded by hand 2026-11-02: mmc1 (search terms, extraction variables), mmc2 (included literature), mmc3 (excluded literature), mmc4.xlsx (4,738 taxa: GBIF id and taxonomy, `reported_use`, source article). No repository. Figshare 10.6084/m9.figshare.22115045 is a different Lassaline paper (2023, Austral Entomology). | `1_raw_data/lassaline2025_sciencedirect/` | Partly: `reported_use` (collection = likely dead; pet, biocontrol, pollination, exhibit = likely live) |
| Wang et al. 2023 | *Biol. Conserv.* 283:110092, doi 10.1016/j.biocon.2023.110092 (CC-BY) | Zenodo doi 10.5281/zenodo.7825674: README + SM1 (species traits, volume, price), SM2 (51,348 eBay sales, 2022), SM3 (image clusters). Scraper code: github.com/Weilin37/ButterflyTradeEbay | `1_raw_data/wang2023_zenodo_7825674/` | Dead specimens (inferred from listing titles) |
| Marshall et al. 2022 | *Commun. Biol.* 5:448, doi 10.1038/s42003-022-03374-0; PMC9120460 | Zenodo doi 10.5281/zenodo.5758541 (record 5758540 resolves to the same files): Data S1–S13 + Code S01–S12, 26 files, MD5 verified. See `Basic_metadata.txt`. | `1_raw_data/marshall2022_zenodo_5758541/` | Mostly live pets (inferred) |
| **LEMIS 2000–2022, corrected** (used) | Marshall et al. 2025, *PNAS* 122:e2410774121; PMC11745324 | figshare doi 10.6084/m9.figshare.25041584 (CC BY 4.0): `lemisDataCorrected_2023-11-11.csv.gz` (8.7 M records, corrected names, groups), `MainDataFileDetails.txt`, `lemis_codes_2023-04-19.csv`, `nameConvertingKey_complete.csv`, `LEMIS_known_outliers.csv`; MD5 verified, downloaded 2026-11-02. Code: figshare links in the paper. Distribution add-on (vertebrates and arachnids only): Zenodo 10.5281/zenodo.14982583 | `1_raw_data/marshall2025_figshare_25041584/` | Yes: description codes LIV / EGL / DEA; UNS = unspecified |
| LEMIS 2000–2014 (older, not used) | Eskew et al. 2020, *Sci. Data* | github.com/eveskew/lemis release v1.1.0, `lemis.fst` (5.5 M records); included in the 2025 data | `1_raw_data/eskew2020_lemis_github/` | Yes: description codes LIV / EGL / DEA |
| Catalogue of Life | checklistbank.org, dataset 3LR (latest release) | API, used for the manual synonym check (`3_metadata/synonym_review.csv`) | n/a | n/a |
| EPPO codes | EPPO | local SQLite | `1_raw_data/eppo db/eppocodes.sqlite` | n/a |
| EPPO Global Database | EPPO | GDAPI v2 api.eppo.int/gd/v2, spec in `eppo_api_gd_v2.yml` | `2_processed_data/eppo_api_cache/` | n/a |

Paper PDFs are in `3_metadata/pdf/`: `Hughes_2026_wildlife_trade_data_gaps.pdf`, `Lassaline_2025_global_invertebrate_trade.pdf`, `Wang_2023_butterfly_species_sold_online.pdf`, `Marshall_2022_arachnid_trade.pdf`.

## Reusable for listing collection (pipeline step 2)
- Marshall 2022, Code S01–S05: finds sale websites through search engines, retrieves pages, generates species keywords from names and synonyms, then searches the pages for them. Data S1 holds the website list (URLs redacted), with flags for whether each site sells arachnids and whether it allows automated searching.
- Wang 2023, GitHub `ButterflyTradeEbay`: eBay sold-listing scraper.
