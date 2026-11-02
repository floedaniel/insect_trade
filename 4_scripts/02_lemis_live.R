# 02_lemis_live.R
# Live invertebrate imports into the US, 2000-2022, from LEMIS as corrected by
# Marshall et al. 2025 (PNAS 122:e2410774121; figshare
# doi:10.6084/m9.figshare.25041584, lemisDataCorrected_2023-11-11.csv.gz).
# Pre-2014 records are the Eskew et al. 2020 data; names are corrected
# (`corrected`, `rank`), with a broad group (`group_`).
# Only live trade matters for plant pests, so keep description codes:
#   LIV = live specimens, EGL = live eggs, DEA = shipped live, died in transit
# No company fields exist in this file.
#
# Outputs (2_processed_data/):
#   lemis_invert_all_forms.fst  invertebrate import records, all forms (step 05)
#   lemis_live_species.csv      one row per LEMIS species with live imports
#   article_eppo_lemis.csv      step-01 table + live flags

library(dplyr)
library(readr)
library(stringr)
library(tidyr)
library(DBI)
library(RSQLite)

lemis_file <- here::here("1_raw_data", "marshall2025_figshare_25041584",
                         "lemisDataCorrected_2023-11-11.csv.gz")
proc_dir   <- here::here("2_processed_data")
db_path    <- here::here("1_raw_data", "eppo db", "eppocodes.sqlite")

live_codes <- c("LIV", "EGL", "DEA")
# Marshall `group_` values that are not invertebrates
drop_groups <- c("Terrestrial Mammals", "Marine Mammals", "Birds", "Reptiles",
                 "Amphibians", "Fish", "Plants", "Echinoderms and Cnidaria",
                 "Porifera Sponges, Bryozoa, and Squirts")

# --- 1. Read and filter --------------------------------------------------------
cols <- c("control_number", "description", "quantity", "unit",
          "country_origin", "country_imp_exp", "purpose", "source", "action",
          "import_export", "sYear", "corrected", "rank", "group_",
          "orderCorrected")
lem <- read_csv(lemis_file, col_select = all_of(cols),
                col_types = cols(.default = "c"), progress = FALSE) |>
  mutate(quantity = suppressWarnings(as.numeric(quantity)),
         shipment_year = as.integer(sYear),
         across(c(country_origin, country_imp_exp), \(x) str_remove(x, "^Ctry_")))
cat(sprintf("LEMIS records 2000-2022: %d\n", nrow(lem)))

inv <- lem |>
  filter(import_export == "I", !group_ %in% drop_groups) |>
  mutate(species_ok = rank %in% "Species" & !is.na(corrected) &
           corrected != "REMAINS UNKNOWN",
         name = if_else(species_ok, word(str_squish(corrected), 1, 2),
                        NA_character_),
         lemis_class = case_when(group_ == "Lepidoptera" ~ "Insecta",
                                 group_ == "Arachnids" ~ "Arachnida"))
rm(lem)

# all forms (live, dead, products, unspecified) for the life-state step
fst::write_fst(inv |> filter(species_ok) |>
                 select(name, control_number, description, shipment_year),
               file.path(proc_dir, "lemis_invert_all_forms.fst"))

live <- inv |> filter(description %in% live_codes)
rm(inv)

cat(sprintf("Live invertebrate import records: %d (%d with species name)\n",
            nrow(live), sum(live$species_ok)))

# --- 2. Per-species summary ----------------------------------------------------
top_n_paste <- function(x, n = 5) {
  x <- x[!is.na(x) & !x %in% c("XX", "non-standard value")]
  if (!length(x)) return(NA_character_)
  tb <- sort(table(x), decreasing = TRUE)
  paste(head(names(tb), n), collapse = ";")
}

sp <- live |>
  filter(species_ok) |>
  group_by(name) |>
  summarise(
    lemis_group = first(group_),
    lemis_order = first(na.omit(orderCorrected)),
    lemis_class = first(na.omit(lemis_class)),
    n_records = n(),
    n_shipments = n_distinct(control_number),
    n_cleared = n_distinct(control_number[action == "C"]),
    n_refused = n_distinct(control_number[action == "R"]),
    qty_live = sum(quantity[description == "LIV" & unit == "NO"], na.rm = TRUE),
    qty_eggs = sum(quantity[description == "EGL" & unit == "NO"], na.rm = TRUE),
    qty_died = sum(quantity[description == "DEA" & unit == "NO"], na.rm = TRUE),
    qty_kg = sum(quantity[unit == "KG"], na.rm = TRUE),
    first_year = min(shipment_year, na.rm = TRUE),
    last_year = max(shipment_year, na.rm = TRUE),
    n_years = n_distinct(shipment_year),
    origin_top = top_n_paste(country_origin),
    export_top = top_n_paste(country_imp_exp),
    n_origin_countries = n_distinct(country_origin[!country_origin %in% c("XX", "non-standard value")]),
    purpose = top_n_paste(purpose, 3),
    source = top_n_paste(source, 3),
    .groups = "drop") |>
  arrange(desc(n_shipments))

# --- 3. EPPO codes for LEMIS names (same exact-match rule as step 01) ---------
con <- dbConnect(SQLite(), db_path)
eppo <- dbGetQuery(con, "
  SELECT c.eppocode, n.fullname, n.preferred, c.dtcode
  FROM t_names n JOIN t_codes c ON c.codeid = n.codeid
  WHERE c.dtcode IN ('GAI','SIT') AND c.status = 'A'
    AND n.status = 'A' AND n.codelang = 'la'")
dbDisconnect(con)
eppo <- eppo |>
  mutate(key = str_to_lower(str_squish(fullname))) |>
  arrange(key, desc(preferred), dtcode != "GAI") |>
  distinct(key, .keep_all = TRUE) |>
  transmute(key, lemis_eppocode = eppocode,
            lemis_eppo_match = if_else(preferred == 1, "exact", "synonym"))

sp <- sp |>
  mutate(key = str_to_lower(name)) |>
  left_join(eppo, by = "key") |>
  select(-key)

write_csv(sp, file.path(proc_dir, "lemis_live_species.csv"), na = "")

# --- 4. Join to the article table ------------------------------------------------
art_file <- file.path(proc_dir, "article_eppo_match.csv")
art <- read_csv(art_file, show_col_types = FALSE, guess_max = Inf)

art_live <- art |>
  left_join(sp |> select(name, lemis_live_shipments = n_shipments,
                         lemis_live_qty = qty_live, lemis_live_eggs = qty_eggs,
                         lemis_live_years = n_years,
                         lemis_live_origin = origin_top),
            by = "name") |>
  mutate(lemis_live = !is.na(lemis_live_shipments))

write_csv(art_live, file.path(proc_dir, "article_eppo_lemis.csv"), na = "")

# --- 5. Short report -----------------------------------------------------------
cat(sprintf("\nSpecies with live imports: %d\n", nrow(sp)))
print(as.data.frame(count(sp, lemis_group, sort = TRUE)))
cat(sprintf("  with EPPO code: %d\n", sum(!is.na(sp$lemis_eppocode))))
cat(sprintf("Article names: %d, of which live in LEMIS: %d (insects: %d)\n",
            nrow(art_live), sum(art_live$lemis_live),
            sum(art_live$lemis_live & art_live$is_insect %in% TRUE)))
cat(sprintf("LEMIS live species not in article list: %d\n",
            sum(!sp$name %in% art$name)))
cat(sprintf("Article 'LEMIS' flag = 1 but no live record: %d\n",
            sum(art_live$LEMIS == 1 & !art_live$lemis_live, na.rm = TRUE)))

cat("\nTop live insect species by shipments:\n")
sp |> filter(lemis_group %in% c("Lepidoptera", "Insecta and Myriapoda")) |>
  select(name, n_shipments, qty_live, qty_eggs, n_years, origin_top,
         lemis_eppocode) |>
  head(25) |> as.data.frame() |> print()

cat("\nLive species with an EPPO code:\n")
sp |> filter(!is.na(lemis_eppocode)) |>
  select(name, lemis_group, n_shipments, qty_live, lemis_eppocode) |>
  as.data.frame() |> print()
