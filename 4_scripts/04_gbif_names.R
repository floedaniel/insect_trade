# 04_gbif_names.R
# Resolve every species name (Hughes list + LEMIS live species) against the
# GBIF backbone via the GBIF API directly (api.gbif.org/v1/species/match,
# no key needed). Gives class/order for names EPPO doesn't know, and the
# accepted name for synonyms (pipeline step 4).
# Responses are cached as JSON, so reruns only fetch missing names.
# Output: 2_processed_data/gbif_names.csv (one row per queried name)

library(httr2)
library(jsonlite)
library(dplyr)
library(readr)
library(purrr)
library(stringr)

proc      <- \(...) here::here("2_processed_data", ...)
cache_dir <- proc("gbif_cache")
dir.create(cache_dir, showWarnings = FALSE)

names_all <- c(
  read_csv(proc("article_eppo_match.csv"), show_col_types = FALSE,
           col_select = name)$name,
  read_csv(proc("lemis_live_species.csv"), show_col_types = FALSE,
           col_select = name)$name) |>
  str_squish() |> unique() |> sort()

cache_file <- \(nm) file.path(cache_dir,
                              paste0(str_replace_all(nm, "[^A-Za-z0-9]+", "_"), ".json"))
todo <- names_all[!file.exists(cache_file(names_all))]
cat(sprintf("Names: %d | cached: %d | to fetch: %d\n",
            length(names_all), length(names_all) - length(todo), length(todo)))

# --- Fetch in parallel batches (GBIF asks for polite use; 8 at a time) -------
batches <- split(todo, ceiling(seq_along(todo) / 200))
t0 <- Sys.time()
for (b in seq_along(batches)) {
  nms  <- batches[[b]]
  reqs <- map(nms, \(nm) request("https://api.gbif.org/v1/species/match") |>
                req_url_query(name = nm, kingdom = "Animalia", strict = "false") |>
                req_retry(max_tries = 4) |>
                req_error(is_error = \(r) FALSE))
  resps <- req_perform_parallel(reqs, max_active = 8, on_error = "continue",
                                progress = FALSE)
  walk2(nms, resps, \(nm, r) {
    if (inherits(r, "httr2_response") && resp_status(r) == 200)
      write_file(resp_body_string(r), cache_file(nm))
  })
  done <- sum(map_lgl(resps, \(r) inherits(r, "httr2_response")))
  cat(sprintf("[batch %d/%d] %d/%d ok  (%.0fs)\n", b, length(batches), done,
              length(nms), as.numeric(difftime(Sys.time(), t0, units = "secs"))))
}

# --- Parse ---------------------------------------------------------------------
fields <- c("usageKey", "acceptedUsageKey", "scientificName", "canonicalName",
            "rank", "status", "confidence", "matchType", "kingdom", "phylum",
            "class", "order", "family", "genus", "species",
            # keys: speciesKey = accepted species (use for occurrences and
            # distribution); usageKey = the matched name (may be a synonym)
            "speciesKey", "genusKey", "familyKey", "orderKey", "classKey",
            "phylumKey", "kingdomKey")
parse_one <- function(nm) {
  f <- cache_file(nm)
  if (!file.exists(f)) return(tibble(name = nm, gbif_matchType = "NOT_FETCHED"))
  x <- fromJSON(f)
  vals <- map(set_names(fields), \(k) if (is.null(x[[k]])) NA else as.character(x[[k]]))
  as_tibble(vals) |> rename_with(\(k) paste0("gbif_", k)) |>
    mutate(name = nm, .before = 1)
}
gb <- map(names_all, parse_one, .progress = TRUE) |> list_rbind() |>
  mutate(gbif_confidence = as.integer(gbif_confidence),
         # usable = exact/fuzzy species-level match in Animalia
         gbif_ok = gbif_matchType %in% c("EXACT", "FUZZY") &
           gbif_rank %in% c("SPECIES", "SUBSPECIES") &
           gbif_kingdom %in% "Animalia",
         # accepted species name (differs from query for synonyms)
         gbif_accepted = if_else(gbif_ok, gbif_species, NA_character_),
         gbif_is_synonym = gbif_ok & !is.na(gbif_status) &
           gbif_status != "ACCEPTED",
         # species key only for usable species-level matches
         gbif_speciesKey = if_else(gbif_ok, gbif_speciesKey, NA_character_))

# --- Fallback: Catalogue of Life for names without a GBIF species key --------
# Many are old genus combinations the GBIF backbone only knows at genus level
# (e.g. Allomyrina dichotoma -> Trypoxylus dichotomus). Ask COL (ChecklistBank
# latest release, 3LR) for the accepted name, then match that name in GBIF.
col_dir <- proc("col_cache"); dir.create(col_dir, showWarnings = FALSE)
get_json <- function(req, file) {
  if (!file.exists(file)) {
    r <- req |> req_retry(max_tries = 4) |> req_error(is_error = \(r) FALSE) |>
      req_perform()
    write_file(if (resp_status(r) == 200) resp_body_string(r) else "null", file)
  }
  txt <- read_file(file)
  if (txt %in% c("", "null")) NULL else fromJSON(txt, simplifyVector = FALSE)
}
no_key <- gb$name[is.na(gb$gbif_speciesKey)]
cat(sprintf("\nFallback for %d names without a GBIF species key\n", length(no_key)))
search_dir <- proc("gbif_search_cache"); dir.create(search_dir, showWarnings = FALSE)

# candidate accepted names for one query name: COL accepted name (if it
# differs), then accepted names of synonyms in any GBIF checklist
alt_names <- function(nm) {
  out <- character()
  x <- get_json(request("https://api.checklistbank.org/dataset/3LR/match/nameusage") |>
                  req_url_query(scientificName = nm),
                file.path(col_dir, basename(cache_file(nm))))
  u <- x$usage
  if (!is.null(u) && identical(u$rank, "species") && !is.null(u$accepted))
    out <- c(out, u$accepted$name)
  s <- get_json(request("https://api.gbif.org/v1/species/search") |>
                  req_url_query(q = nm, rank = "SPECIES", status = "SYNONYM",
                                limit = 20),
                file.path(search_dir, basename(cache_file(nm))))
  for (r in s$results) {
    if (!is.null(r$accepted) &&
        str_to_lower(word(r$canonicalName %||% "", 1, 2)) == str_to_lower(nm))
      out <- c(out, r$accepted)
  }
  out <- unique(word(str_squish(out), 1, 2))
  out[!is.na(out) & str_to_lower(out) != str_to_lower(nm)]
}

gb2 <- map(no_key, \(nm) {
  for (acc in alt_names(nm)) {
    x <- get_json(request("https://api.gbif.org/v1/species/match") |>
                    req_url_query(name = acc, kingdom = "Animalia", strict = "false"),
                  cache_file(paste0("ALT_", acc)))
    ok <- !is.null(x) && isTRUE(x$matchType %in% c("EXACT", "FUZZY")) &&
      isTRUE(x$rank %in% c("SPECIES", "SUBSPECIES")) &&
      identical(x$kingdom, "Animalia")
    if (ok) return(tibble(
      name = nm, alt_accepted = acc,
      col_gbif_speciesKey = as.character(x$speciesKey),
      col_gbif_species = x$species, col_gbif_class = x$class %||% NA,
      col_gbif_order = x$order %||% NA, col_gbif_family = x$family %||% NA))
  }
  NULL
}, .progress = TRUE) |> list_rbind()

gb <- gb |> mutate(gbif_key_source = if_else(!is.na(gbif_speciesKey),
                                              "backbone match", NA_character_))
if (nrow(gb2) > 0) {
  gb <- gb |>
    left_join(gb2, by = "name") |>
    mutate(gbif_via_col = is.na(gbif_speciesKey) & !is.na(col_gbif_speciesKey),
           gbif_speciesKey = coalesce(gbif_speciesKey, col_gbif_speciesKey),
           gbif_accepted = coalesce(gbif_accepted, col_gbif_species),
           gbif_class = if_else(gbif_via_col, col_gbif_class, gbif_class),
           gbif_order = if_else(gbif_via_col, col_gbif_order, gbif_order),
           gbif_family = if_else(gbif_via_col, col_gbif_family, gbif_family),
           gbif_is_synonym = gbif_is_synonym | gbif_via_col,
           gbif_ok = gbif_ok | gbif_via_col,
           # synonym search can link the wrong taxon: check these by hand
           gbif_key_source = if_else(gbif_via_col,
                                     paste0("via synonym (", alt_accepted, "), check"),
                                     gbif_key_source)) |>
    select(-starts_with("col_gbif_"))
  cat(sprintf("Species keys found through COL / GBIF synonym search: %d\n",
              sum(gb$gbif_via_col)))
}

write_csv(gb, proc("gbif_names.csv"), na = "")
source(here::here("4_scripts", "utils_xlsx.R"))
dir.create(here::here("5_outputs", "tables"), showWarnings = FALSE, recursive = TRUE)
write_xlsx_tables(list(gbif_names = gb),
                  here::here("5_outputs", "tables", "gbif_names.xlsx"))

# Same GBIF species key = same species to GBIF, whatever the name used
dup <- gb |> filter(!is.na(gbif_speciesKey)) |> group_by(gbif_speciesKey) |>
  filter(n_distinct(name) > 1) |> ungroup()
cat(sprintf("\nGBIF species keys: %d names | distinct keys: %d | names sharing a key with another name: %d\n",
            sum(!is.na(gb$gbif_speciesKey)), n_distinct(gb$gbif_speciesKey, na.rm = TRUE),
            nrow(dup)))
cat("Names without a species key:", sum(is.na(gb$gbif_speciesKey)),
    "(genus-only, kingdom-only or no match; listed in gbif_names.csv)\n")

cat("\nGBIF match types:\n"); print(as.data.frame(count(gb, gbif_matchType, gbif_rank) |> arrange(desc(n))))
cat("\nUsable matches:", sum(gb$gbif_ok), "of", nrow(gb),
    "| synonyms:", sum(gb$gbif_is_synonym), "\n")
cat("\nClass of usable matches:\n"); print(as.data.frame(count(filter(gb, gbif_ok), gbif_class, sort = TRUE)))
