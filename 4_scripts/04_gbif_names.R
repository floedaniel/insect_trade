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
            "class", "order", "family", "genus", "species")
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
           gbif_status != "ACCEPTED")

write_csv(gb, proc("gbif_names.csv"), na = "")

cat("\nGBIF match types:\n"); print(as.data.frame(count(gb, gbif_matchType, gbif_rank) |> arrange(desc(n))))
cat("\nUsable matches:", sum(gb$gbif_ok), "of", nrow(gb),
    "| synonyms:", sum(gb$gbif_is_synonym), "\n")
cat("\nClass of usable matches:\n"); print(as.data.frame(count(filter(gb, gbif_ok), gbif_class, sort = TRUE)))
