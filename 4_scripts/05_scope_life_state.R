# 05_scope_life_state.R
# Two filters on the species list:
#  1. Plant-pest scope: keep insects, mites, land/apple snails and slugs,
#     nematodes. Drop crustaceans, spiders, scorpions, ticks, marine molluscs etc.
#     Unknown class is kept and labelled.
#  2. Life state: live / dead / unknown, from all trade evidence we have.
#     Dead material (pinned, papered, mounted, collection, products) is
#     labelled, not deleted.
#
# Inputs: 2_processed_data/ article_eppo_lemis.csv (01+02),
#         lemis_live_species.csv (02), eppo_code_summary.csv (03),
#         gbif_names.csv (04); LEMIS fst, Lassaline 2025 mmc4.xlsx,
#         Wang 2023 SM2.
# Outputs:
#   2_processed_data/species_scope_state.csv   all species, with labels
#   5_outputs/tables/plant_pest_candidates.csv in scope, live or unknown

library(dplyr)
library(readr)
library(stringr)
library(tidyr)
library(DBI)
library(RSQLite)

raw  <- \(...) here::here("1_raw_data", ...)
proc <- \(...) here::here("2_processed_data", ...)
rd   <- \(f) read_csv(proc(f), show_col_types = FALSE, guess_max = Inf)

# Join non-empty strings element-wise with "; ", NA if all empty
paste_nonempty <- function(...) {
  m <- cbind(...)
  out <- apply(m, 1, \(r) paste(r[!is.na(r) & r != ""], collapse = "; "))
  na_if(out, "")
}

# --- 1. Species universe: Hughes names + live LEMIS species not in Hughes ---
art <- rd("article_eppo_lemis.csv") |> mutate(in_hughes = TRUE)
lem_live <- rd("lemis_live_species.csv")

lem_only <- lem_live |>
  filter(!name %in% art$name) |>
  transmute(name, eppocode = lemis_eppocode,
            match_type = if_else(is.na(lemis_eppocode), "none",
                                 lemis_eppo_match),
            lemis_class, in_hughes = FALSE)

sp <- bind_rows(art, lem_only) |>
  left_join(lem_live |> select(name, lemis_class_live = lemis_class),
            by = "name") |>
  mutate(lemis_class = coalesce(lemis_class, lemis_class_live)) |>
  select(-lemis_class_live) |>
  # EPPO GDAPI summary per code (step 03), covers LEMIS-only codes too
  left_join(rd("eppo_code_summary.csv"), by = "eppocode") |>
  # GBIF backbone (step 04)
  left_join(rd("gbif_names.csv") |>
              filter(gbif_ok) |>
              select(name, gbif_class, gbif_order, gbif_family,
                     gbif_accepted, gbif_is_synonym),
            by = "name")

# --- 2. Lassaline 2025: reported use -> life state ---------------------------
live_use <- "pet|biocontrol|exhibit|pollinat|honey|brood|aquarium|tank cleaner"
las <- readxl::read_excel(
  raw("lassaline2025_sciencedirect", "1-s2.0-S0006320725003039-mmc4.xlsx"),
  sheet = 1) |>
  filter(!is.na(species)) |>
  mutate(use = str_squish(str_to_lower(reported_use))) |>
  group_by(name = species) |>
  summarise(las_class = first(class), las_order = first(order),
            lassaline_use = paste(sort(unique(na.omit(use))), collapse = "; "),
            .groups = "drop") |>
  mutate(lassaline_use = na_if(lassaline_use, ""),
         las_live = str_detect(coalesce(lassaline_use, ""), live_use),
         las_dead = str_detect(coalesce(lassaline_use, ""), "collection"))

# --- 3. Wang 2023: eBay butterfly sales = dead mounted/papered specimens -----
wang <- read_csv(raw("wang2023_zenodo_7825674", "Supplementary_Material_2.csv"),
                 show_col_types = FALSE, col_select = c(name_accepted, frame)) |>
  count(name_accepted, name = "wang_dead_sales") |>
  rename(name = name_accepted)

# --- 4. LEMIS: all forms per species (live / dead / unspecified) -------------
live_codes <- c("LIV", "EGL", "DEA")
unk_codes  <- c("UNS", "non-standard value")
lem <- fst::read_fst(raw("eskew2020_lemis_github", "lemis.fst"),
                     columns = c("control_number", "genus", "species",
                                 "description", "import_export")) |>
  filter(import_export == "I", !is.na(genus), !is.na(species)) |>
  mutate(name = paste(str_squish(genus), str_squish(species))) |>
  filter(name %in% sp$name)

lem_state <- lem |>
  mutate(state = case_when(description %in% live_codes ~ "live",
                           description %in% unk_codes | is.na(description) ~ "unspecified",
                           TRUE ~ "dead")) |>
  group_by(name) |>
  summarise(
    lemis_live_n = n_distinct(control_number[state == "live"]),
    lemis_dead_n = n_distinct(control_number[state == "dead"]),
    lemis_unspec_n = n_distinct(control_number[state == "unspecified"]),
    lemis_dead_codes = paste(head(names(sort(table(description[state == "dead"]),
                                             decreasing = TRUE)), 4),
                             collapse = ";"),
    .groups = "drop") |>
  mutate(lemis_dead_codes = na_if(lemis_dead_codes, ""))
rm(lem)

# --- 5. Taxonomy for scope: class/order from EPPO, Hughes, Lassaline, LEMIS --
sp <- sp |>
  left_join(las, by = "name") |>
  left_join(wang, by = "name") |>
  left_join(lem_state, by = "name") |>
  mutate(class = coalesce(eppo_class, art_class, las_class, gbif_class,
                          lemis_class),
         order = coalesce(eppo_order, art_order, las_order, gbif_order))

# Fill missing order from the EPPO genus (local sqlite), for scope decisions
con <- dbConnect(SQLite(), raw("eppo db", "eppocodes.sqlite"))
need <- sp |> filter(is.na(order)) |> distinct(genus = word(name, 1))
dbWriteTable(con, "tmp_g", need, temporary = TRUE, overwrite = TRUE)
gen_tax <- dbGetQuery(con, "
  WITH g AS (
    SELECT DISTINCT t.genus, n.codeid FROM tmp_g t
    JOIN t_names n ON n.fullname = t.genus AND n.codelang = 'la' AND n.status = 'A'
    JOIN t_codes c ON c.codeid = n.codeid AND c.dtcode = 'SIT' AND c.status = 'A'
                  AND c.eppocode LIKE '%G'),
  up(genus, id, d) AS (
    SELECT genus, codeid, 0 FROM g
    UNION ALL
    SELECT up.genus, l.codeid_parent, d + 1 FROM up
    JOIN t_links l ON l.codeid = up.id WHERE l.status = 'A' AND d < 20)
  SELECT up.genus, c.eppocode, n.fullname FROM up
  JOIN t_codes c ON c.codeid = up.id
  JOIN t_names n ON n.codeid = up.id AND n.preferred = 1 AND n.codelang = 'la'
  WHERE c.eppocode LIKE '1%C' OR c.eppocode LIKE '1%O'")
dbDisconnect(con)
gen_tax <- gen_tax |>
  mutate(rank = if_else(str_ends(eppocode, "C"), "g_class", "g_order")) |>
  distinct(genus, rank, .keep_all = TRUE) |>
  select(genus, rank, fullname) |>
  pivot_wider(names_from = rank, values_from = fullname)

sp <- sp |>
  mutate(genus = word(name, 1)) |>
  left_join(gen_tax, by = "genus") |>
  mutate(class = coalesce(class, g_class), order = coalesce(order, g_order)) |>
  select(-genus, -g_class, -g_order)

# --- 6. Plant-pest scope -------------------------------------------------------
mite_orders  <- c("Acarida", "Acari", "Acarina", "Mesostigmata",
                  "Trombidiformes", "Sarcoptiformes", "Prostigmata")
snail_orders <- c("Stylommatophora", "Sigmurethra", "Systellommatophora",
                  "Architaenioglossa")
nematodes    <- c("Chromadorea", "Secernentea", "Enoplea", "Nematoda")

sp <- sp |>
  mutate(pest_group = case_when(
    class == "Insecta" ~ "insect",
    class == "Arachnida" & order %in% mite_orders ~ "mite",
    class == "Gastropoda" & (order %in% snail_orders |
                               gbif_family %in% "Ampullariidae") ~ "snail/slug",
    # other gastropods without order are freshwater/marine aquarium snails
    # (Pachychilidae, Melanopsidae, Thiaridae ...), not plant pests
    class %in% nematodes ~ "nematode",
    is.na(class) ~ "unknown class",
    TRUE ~ "out of scope"),
    in_scope = pest_group != "out of scope")

# --- 7. Life state -------------------------------------------------------------
sp <- sp |>
  mutate(
    across(c(lemis_live_n, lemis_dead_n, lemis_unspec_n, wang_dead_sales),
           \(x) coalesce(x, 0L)),
    las_live = coalesce(las_live, FALSE), las_dead = coalesce(las_dead, FALSE),
    live_evidence = paste_nonempty(
      if_else(lemis_live_n > 0, sprintf("LEMIS live imports (%d shipments)", lemis_live_n), ""),
      if_else(las_live, paste0("Lassaline use: ", lassaline_use), "")),
    dead_evidence = paste_nonempty(
      if_else(wang_dead_sales > 0, sprintf("pinned/papered specimens, eBay 2022 (%d sales, Wang)", wang_dead_sales), ""),
      if_else(las_dead, "collection specimens (Lassaline)", ""),
      if_else(lemis_dead_n > 0, sprintf("LEMIS dead/products %s (%d shipments)", lemis_dead_codes, lemis_dead_n), "")),
    # evidence of trade in a form that may or may not be live
    las_other = !is.na(lassaline_use) & !las_live &
      str_detect(lassaline_use, "food|medical|venom|silk|education|agricultur|soil|recycling"),
    unknown_evidence = paste_nonempty(
      if_else(las_other, paste0("Lassaline use: ", lassaline_use), ""),
      if_else(lemis_unspec_n > 0, sprintf("LEMIS unspecified form (%d shipments)", lemis_unspec_n), "")),
    # dead only when every piece of evidence is dead
    life_state = case_when(
      !is.na(live_evidence) ~ "live",
      !is.na(unknown_evidence) ~ "unknown",
      !is.na(dead_evidence) ~ "dead",
      TRUE ~ "unknown"))

# --- 8. Write ------------------------------------------------------------------
sp <- sp |>
  relocate(name, in_hughes, class, order, pest_group, in_scope, life_state,
           live_evidence, unknown_evidence, dead_evidence, lassaline_use,
           eppocode, match_type)
write_csv(sp, proc("species_scope_state.csv"), na = "")

regulated <- \(d) with(d, coalesce(eppo_A1, FALSE) | coalesce(eppo_A2, FALSE) |
                         coalesce(eu_listed, FALSE) | coalesce(no_listed, FALSE))
collapse_txt <- \(x) na_if(paste(unique(na.omit(x)), collapse = " | "), "")
first_val    <- \(x) first(na.omit(x))

# One row per accepted species: merge synonyms (GBIF accepted name), so a
# live record under one name is not hidden by a dead record under another.
acc <- sp |>
  mutate(accepted_name = coalesce(gbif_accepted, name),
         regulated = regulated(pick(everything())),
         categorized = coalesce(n_categorizations, 0) > 0) |>
  arrange(accepted_name, is.na(eppocode), desc(in_hughes)) |>
  group_by(accepted_name) |>
  summarise(
    names = paste(unique(name), collapse = "; "),
    in_scope = any(in_scope),
    pest_group = if (any(in_scope)) first(pest_group[in_scope]) else "out of scope",
    life_state = case_when(any(life_state == "live") ~ "live",
                           any(life_state == "unknown") ~ "unknown",
                           TRUE ~ "dead"),
    across(c(live_evidence, unknown_evidence, dead_evidence), collapse_txt),
    regulated = any(regulated), categorized = any(categorized),
    across(c(class, order, eppocode, eppo_list, eu_list, no_list, no_status,
             n_categorizations, n_hosts, hosts), first_val),
    in_hughes = any(in_hughes), .groups = "drop")
write_csv(acc, proc("species_accepted_state.csv"), na = "")

cand <- acc |>
  filter(in_scope, life_state != "dead") |>
  arrange(desc(regulated), desc(life_state == "live"), desc(categorized),
          desc(coalesce(n_hosts, 0)), accepted_name) |>
  select(accepted_name, names, pest_group, class, order, life_state,
         live_evidence, unknown_evidence, dead_evidence, eppocode, regulated,
         eppo_list, eu_list, no_list, no_status, n_categorizations, n_hosts,
         hosts, in_hughes)
dir.create(here::here("5_outputs", "tables"), showWarnings = FALSE,
           recursive = TRUE)
write_csv(cand, here::here("5_outputs", "tables", "plant_pest_candidates.csv"),
          na = "")

# --- 9. Report -----------------------------------------------------------------
cat("Species total:", nrow(sp), "\n\nScope:\n")
print(as.data.frame(count(sp, pest_group, sort = TRUE)))
cat("\nLife state, in scope only:\n")
print(as.data.frame(sp |> filter(in_scope) |> count(pest_group, life_state) |>
                      pivot_wider(names_from = life_state, values_from = n,
                                  values_fill = 0)))
cat("\nCandidates (in scope, live or unknown):", nrow(cand),
    "| regulated:", sum(cand$regulated),
    "| regulated and live:", sum(cand$regulated & cand$life_state == "live"),
    "\n\nRegulated or EPPO-categorized candidates:\n")
cand |> filter(regulated | coalesce(n_categorizations, 0) > 0) |>
  transmute(accepted_name, pest_group, life_state, live = str_trunc(coalesce(live_evidence, ""), 45),
            lists = str_trunc(paste_nonempty(eppo_list, eu_list,
                                             if_else(is.na(no_list), NA, paste("NO:", no_list))), 50),
            n_cat = n_categorizations, n_hosts) |>
  as.data.frame() |> print()
