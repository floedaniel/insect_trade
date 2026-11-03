# 07_main_results.R
# DRAFT main result: one workbook (tables only, no plots) from all existing
# results: 5_outputs/insect_trade_main_DRAFT.xlsx. Figures and the panel
# presentation are in 'insect trade results.qmd', which reads this workbook.
# One row per accepted species (synonyms merged), all evidence side by side,
# a draft tier (Regulated here / Regulated elsewhere / Potential plant pest /
# No known plant risk) and a priority order. Rerun when the web crawl has finished.
#
# Draft tier rules (to be discussed with the panel):
#   Regulated here: EPPO A1/A2, EU-listed, Norwegian list, or whole genus listed
#   Regulated elsewhere: on a quarantine/A1/A2 list of another country/RPPO
#   Potential plant pest: EPPO host-plant records, or a plant-feeding group
#      (family/order proxy, flagged as such)
#   No known plant risk: the rest
# Live trade: ANY live evidence counts the same (pet, hobby, lab or rearing:
#   a live animal is a live animal). Sources: live listing on a web shop now,
#   live US import (LEMIS 2000-2022), live use in the literature (Lassaline).
#   Then: traded in unknown form; dead material only.

suppressMessages({
  library(dplyr); library(readr); library(stringr); library(tidyr)
})
source(here::here("4_scripts", "utils_xlsx.R"))
proc <- \(...) here::here("2_processed_data", ...)
out_dir <- here::here("5_outputs")
rd <- \(f) read_csv(proc(f), show_col_types = FALSE, guess_max = Inf)
TIERS <- c("Regulated here", "Regulated elsewhere", "Potential plant pest", "No known plant risk")   # draft tiers, in priority order

acc <- rd("species_accepted_state.csv")
sp  <- rd("species_scope_state.csv") |>
  mutate(accepted_name = coalesce(gbif_accepted, name))
web_file <- proc("web_listings.csv")
web <- if (file.exists(web_file)) rd("web_listings.csv") else tibble()
n_shops_crawled <- length(list.files(proc("scrape_results"), pattern = "\\.json$"))

# --- 1. Evidence per accepted species ----------------------------------------
per_name <- sp |>
  group_by(accepted_name) |>
  summarise(
    hughes_sources = paste(sort(unique(unlist(lapply(
      c("CITES Trade", "CITES App", "LEMIS", "IUCN", "TRAFFIC", "Research"),
      \(col) if (any(pick(all_of(col))[[1]] %in% 1)) col)))), collapse = "; "),
    lemis_live_shipments = sum(lemis_live_n, na.rm = TRUE),
    lemis_dead_shipments = sum(lemis_dead_n, na.rm = TRUE),
    lemis_live_purpose = first(na.omit(lemis_live_purpose)),
    lemis_live_first = suppressWarnings(min(lemis_live_years, na.rm = TRUE)),
    lemis_live_last = suppressWarnings(max(lemis_live_last, na.rm = TRUE)),
    lemis_live_origin = first(na.omit(lemis_live_origin)),
    lassaline_use = first(na.omit(lassaline_use)),
    wang_ebay_dead_sales = sum(wang_dead_sales, na.rm = TRUE),
    n_countries_categorized = max(n_countries_categorized, na.rm = TRUE),
    n_major_hosts = max(n_major_hosts, na.rm = TRUE),
    eppo_family = first(na.omit(eppo_family)),
    .groups = "drop") |>
  mutate(across(c(lemis_live_first, lemis_live_last, n_countries_categorized,
                  n_major_hosts), \(x) if_else(is.infinite(x), NA, x)))

web_sp <- if (nrow(web)) web |>
  # evidence of sale: strong (live) or probable (see species_on_sale.xlsx guide)
  filter(evidence %in% c("strong", "probable")) |>
  group_by(species) |>
  summarise(web_shops = n_distinct(domain), web_listings = n_distinct(url),
            web_live_listings = n_distinct(url[evidence == "strong"]),
            web_shops_ship_norway = n_distinct(domain[str_starts(coalesce(ships_to_norway, ""), "yes")]),
            web_seller_countries = paste(sort(unique(na.omit(seller_country[seller_country != ""]))), collapse = "; "),
            web_example = first(c(url[evidence == "strong"], url)),
            web_match = paste(sort(unique(match_type)), collapse = "; "),
            .groups = "drop") else tibble(species = character())

# --- 2. Plant-feeding proxy (only where EPPO has no host records) ------------
plant_feeding_orders <- c("Lepidoptera", "Orthoptera", "Phasmida", "Phasmatodea",
                          "Thysanoptera", "Stylommatophora", "Sigmurethra",
                          "Systellommatophora", "Architaenioglossa")
plant_feeding_families <- c("Curculionidae", "Chrysomelidae", "Cerambycidae",
  "Scarabaeidae", "Buprestidae", "Bostrichidae", "Elateridae", "Tenebrionidae",
  "Aphididae", "Aleyrodidae", "Pseudococcidae", "Diaspididae", "Coccidae",
  "Cicadellidae", "Delphacidae", "Psyllidae", "Miridae", "Pentatomidae",
  "Tephritidae", "Agromyzidae", "Cecidomyiidae", "Anthomyiidae",
  "Tenthredinidae", "Cynipidae", "Formicidae", "Tetranychidae", "Eriophyidae",
  "Tenuipalpidae", "Acrididae", "Gryllidae", "Gryllotalpidae", "Lymantriidae")

# --- 3. Main table -----------------------------------------------------------
main <- acc |>
  filter(in_scope) |>
  left_join(per_name, by = "accepted_name") |>
  left_join(web_sp, by = c("accepted_name" = "species")) |>
  mutate(
    across(c(web_shops, web_listings, web_live_listings, web_shops_ship_norway),
           \(x) coalesce(x, 0L)),
    family = coalesce(gbif_family, eppo_family),
    plant_feeding = case_when(
      coalesce(n_hosts, 0) > 0 ~ "yes (EPPO host records)",
      family %in% plant_feeding_families ~ "likely (family)",
      order %in% plant_feeding_orders | pest_group %in% c("snail/slug") ~ "likely (order/group)",
      TRUE ~ "unknown"),
    tier = case_when(
      regulated ~ "Regulated here",
      coalesce(n_countries_categorized, 0) > 0 ~ "Regulated elsewhere",
      plant_feeding != "unknown" ~ "Potential plant pest",
      TRUE ~ "No known plant risk"),
    tier_order = match(tier, TIERS),
    live_evidence_level = case_when(
      web_live_listings > 0 ~ "1 live on sale online now",
      lemis_live_shipments > 0 ~ "2 live US imports (LEMIS)",
      str_detect(coalesce(lassaline_use, ""), "pet|biocontrol|exhibit|pollin|honey|aquarium|feeder") ~ "3 live use (literature)",
      life_state == "unknown" ~ "4 traded, form unknown",
      TRUE ~ "5 dead material only"),
    on_sale_online = web_shops > 0,
    live_any = as.integer(substr(live_evidence_level, 1, 1)) <= 3,
    n_live_sources = (web_live_listings > 0) + (lemis_live_shipments > 0) +
      str_detect(coalesce(lassaline_use, ""), "pet|biocontrol|exhibit|pollin|honey|aquarium|feeder"),
    # tier first; then live (any source, all equal) > unknown > dead; then
    # number of independent live sources, shops shipping to Norway, shops
    priority_score = (5 - tier_order) * 1000 +
      case_when(live_any ~ 300, str_starts(live_evidence_level, "4") ~ 200, TRUE ~ 100) +
      n_live_sources * 20 + pmin(web_shops_ship_norway, 5) * 2 + pmin(web_shops, 20) / 4) |>
  arrange(desc(priority_score), desc(web_shops), desc(lemis_live_shipments)) |>
  mutate(rank = row_number()) |>
  transmute(rank, species = accepted_name, tier, live_evidence_level,
            pest_group, order, family, eppocode, eppo_name, gbif_key,
            regulated_via, eppo_list, eu_list, no_list, no_status,
            n_countries_categorized, plant_feeding, n_hosts, n_major_hosts, hosts,
            on_sale_online, web_shops, web_listings, web_live_listings,
            web_shops_ship_norway, web_seller_countries, web_example,
            lemis_live_shipments, lemis_live_first, lemis_live_last,
            lemis_live_purpose, lemis_live_origin, lemis_dead_shipments,
            lassaline_use, wang_ebay_dead_sales, hughes_sources,
            n_live_sources, life_state_before_web = life_state, names_merged = names,
            priority_score) |>
  mutate(source = "species list (Hughes 2026 / LEMIS)")

# --- 3b. New species found only on shop pages (insect_scraper newnames) -----
# Same evidence rules as listings: name in the title of a priced product page
# in a live-animal shop. Status from GBIF + EPPO (see new_latin_names_resolved.xlsx).
new_file <- proc("web_new_species.csv")
if (file.exists(new_file)) {
  newsp <- rd("web_new_species.csv") |>
    filter(!species %in% main$species) |>
    # once matched by later crawls, graded listing evidence replaces the
    # counts from name discovery
    left_join(web_sp |> select(species, ws = web_shops, wl = web_listings, wlive = web_live_listings,
                               wno = web_shops_ship_norway, wex = web_example), by = "species") |>
    mutate(n_shops = coalesce(ws, n_shops), n_pages = coalesce(wl, n_pages),
           live_pages = coalesce(wlive, live_pages), example_url = coalesce(wex, example_url),
           ship_no = coalesce(wno, 0L)) |>
    transmute(
      species, tier,
      live_evidence_level = if_else(live_pages > 0, "1 live on sale online now", "4 traded, form unknown"),
      pest_group, order, family, eppocode = na_if(eppocode, ""), eppo_name = na_if(eppo_name, ""),
      gbif_key = as.numeric(gbif_key), eppo_list, eu_list, no_list,
      n_countries_categorized = n_countries_listed,
      plant_feeding = case_when(n_hosts > 0 ~ "yes (EPPO host records)",
                                family %in% plant_feeding_families ~ "likely (family)",
                                TRUE ~ "likely (order/group)"),
      n_hosts, hosts, on_sale_online = TRUE, web_shops = n_shops, web_listings = n_pages,
      web_live_listings = live_pages, web_shops_ship_norway = ship_no, web_example = example_url,
      n_live_sources = as.integer(live_pages > 0), names_merged = names_on_shops,
      source = "found on shop pages only (not in Hughes 2026 / LEMIS)")
  main <- bind_rows(main, newsp)
}
main <- main |>
  mutate(priority_score = (5 - match(tier, TIERS)) * 1000 +
           case_when(as.integer(substr(live_evidence_level, 1, 1)) <= 3 ~ 300,
                     str_starts(live_evidence_level, "4") ~ 200, TRUE ~ 100) +
           n_live_sources * 20 + pmin(coalesce(web_shops_ship_norway, 0L), 5) * 2 +
           pmin(coalesce(web_shops, 0L), 20) / 4) |>
  arrange(desc(priority_score), desc(web_shops), desc(lemis_live_shipments)) |>
  mutate(rank = row_number())

priority <- main |> filter(tier != "No known plant risk",
                           !str_starts(live_evidence_level, "5")) |>
  head(150)

# --- 4. Front page --------------------------------------------------------------
fmt <- \(x) format(x, big.mark = ",")
cnt <- function(d) d |> count(tier, live_evidence_level) |>
  pivot_wider(names_from = live_evidence_level, values_from = n, values_fill = 0)
front <- tibble::tribble(
  ~item, ~value,
  "Title", "Live plant-pest invertebrates in trade and on sale online: screening for Norway (DRAFT)",
  "Status", sprintf("Draft built %s from existing results. Web crawl in progress: %s shops done.", format(Sys.Date()), fmt(n_shops_crawled)),
  "Question", "Which invertebrates traded alive (online, imports) are known or potential plant pests for Norway?",
  "Species screened", fmt(nrow(acc)),
  "New species found only on shop pages (in scope)", fmt(sum(main$source != "species list (Hughes 2026 / LEMIS)")),
  "In plant-pest scope (insects, mites, snails/slugs, nematodes)", fmt(nrow(main)),
  "Regulated here (EPPO A1/A2, EU, Norway, genus listing)", fmt(sum(main$tier == "Regulated here")),
  "Regulated elsewhere (on a list of another country)", fmt(sum(main$tier == "Regulated elsewhere")),
  "Potential plant pest (plant-feeding; partly a family/order proxy)", fmt(sum(main$tier == "Potential plant pest")),
  "No known plant risk", fmt(sum(main$tier == "No known plant risk")),
  "Live evidence of any kind", fmt(sum(as.integer(substr(main$live_evidence_level, 1, 1)) <= 3)),
  "On sale online now (web scrape, product/category pages)", fmt(sum(main$on_sale_online)),
  "  of which live listings", fmt(sum(main$web_live_listings > 0)),
  "Regulated here: species with live evidence", paste(main$species[main$tier == "Regulated here" & as.integer(substr(main$live_evidence_level, 1, 1)) <= 3], collapse = "; "),
  "Regulated here: species on sale online", paste(main$species[main$tier == "Regulated here" & main$on_sale_online], collapse = "; "),
  "Sheets", "summary | priority (top 150) | main (all in-scope species) | web_listings | tier_x_evidence | sources_methods | column_guide. Figures and presentation: insect trade results.qmd",
  "Draft tier rules", "Regulated here: EPPO A1/A2, EU or Norwegian list (incl. whole genus). Regulated elsewhere: on a quarantine/A1/A2 list of another country or RPPO. Potential plant pest: EPPO host records or plant-feeding family/order (proxy). No known plant risk: the rest. Live trade: any live evidence counts the same (pet, hobby, lab, rearing). Ranking: tier, then live > unknown form > dead only, then number of live sources, shops shipping to Norway, shops.")

sources <- tibble::tribble(
  ~source, ~what, ~live_vs_dead,
  "Hughes et al. 2026, Sci. Adv. (figshare 10.26188/33194898)", "10,682 traded invertebrate names; flags CITES, LEMIS, IUCN, TRAFFIC, Research", "not distinguished",
  "LEMIS 2000-2022 corrected (Marshall et al. 2025, PNAS; figshare 10.6084/m9.figshare.25041584)", "US imports per shipment, description codes", "yes: LIV/EGL/DEA live; UNS unknown; rest dead",
  "Lassaline et al. 2025, Biol. Conserv.", "4,738 taxa with reported use", "pet/biocontrol/... live; collection dead; food unknown",
  "Wang et al. 2023, Biol. Conserv. (Zenodo 7825674)", "51,348 eBay butterfly sales 2022", "dead specimens",
  "EPPO Global Database (GDAPI v2) + EPPO codes", "categorization (EPPO, EU, countries), host plants, distribution", "n/a",
  "GBIF backbone + Catalogue of Life", "accepted names, synonyms, species keys, class/order/family", "n/a",
  "Own web scrape (insect_scraper)", sprintf("%s shops crawled so far: catalogues, product pages, shipping pages", fmt(n_shops_crawled)), "live/dead from listing text",
  "Method notes", "Scope: plant pests only (insects, mites, land/apple snails, slugs, nematodes). Dead material labelled, not removed. Regulated = EPPO A1/A2 or any EU/NO listing; whole-genus listings count.", "")

guide <- tibble::tribble(
  ~column, ~meaning,
  "rank / priority_score", "Order for discussion: tier first; then live (any source, all equal) > unknown form > dead only; then number of independent live sources, shops shipping to Norway, number of shops",
  "tier", "Regulated here / Regulated elsewhere / Potential plant pest / No known plant risk (DRAFT rules, see summary)",
  "live_evidence_level", "Which kind of evidence was found first (1 online live listing, 2 LEMIS live import, 3 live use in literature; 1-3 all count as live), 4 traded in unknown form, 5 dead material only",
  "plant_feeding", "EPPO host records, or proxy from family/order (check)",
  "eppo_list / eu_list / no_list", "Current listings: EPPO (A1/A2, alert), EU (2019/2072 annexes, IAS Union concern), Norway",
  "web_*", "Web scrape, strong or probable evidence only: shops, listings, live listings (strong), shops that state shipping to Norway, seller countries, one example URL. Weak hits (name only in a description, no price, not a live-animal shop, accessories, research/agrochemical sites) are excluded",
  "lemis_*", "US imports 2000-2022: live shipments, years, purpose (T commercial, S scientific, M biomedical, P personal, Z zoo), origin; dead shipments",
  "lassaline_use", "Use reported in the literature review",
  "wang_ebay_dead_sales", "eBay sales 2022 of dead specimens (butterflies)",
  "hughes_sources", "Sources in Hughes et al. 2026 where the species was recorded in trade",
  "life_state_before_web", "Species life state from the R pipeline before the web scrape",
  "source", "species list (Hughes 2026 / LEMIS), or found on shop pages only (new Latin names, resolved with GBIF and EPPO)")

xt <- main |> count(tier = factor(tier, TIERS), live_evidence_level) |>
  pivot_wider(names_from = live_evidence_level, values_from = n, values_fill = 0)

# --- 6. Workbook ---------------------------------------------------------------------
web_out <- if (nrow(web)) web |> filter(evidence %in% c("strong", "probable")) |>
  select(species, regulated, evidence, life_state, title, url, domain, price, currency, available,
         seller_country, ships_to_norway, ship_scope, match_type, page_type) |>
  arrange(desc(regulated), species) else tibble()
path <- file.path(out_dir, "insect_trade_main_DRAFT.xlsx")
write_xlsx_tables(list(summary = front, priority = priority, main = main,
                       web_listings = web_out, tier_x_evidence = xt,
                       sources_methods = sources, column_guide = guide), path)
tt <- table(factor(main$tier, TIERS))
cat(sprintf("Main table: %s species | %s | on sale online %d | shops crawled %d\n",
            fmt(nrow(main)), paste(names(tt), tt, collapse = ", "),
            sum(main$on_sale_online), n_shops_crawled))
cat("Written:", path, "\n")
print(as.data.frame(head(priority |> select(rank, species, tier, live_evidence_level, web_shops,
                                            lemis_live_shipments, eppo_list, eu_list), 25)))
