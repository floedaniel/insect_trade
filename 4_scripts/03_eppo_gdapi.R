# 03_eppo_gdapi.R
# Pull EPPO Global Database data for the EPPO codes matched in step 01,
# straight from the GDAPI v2 (https://data.eppo.int/apis/GDAPI,
# spec: https://api.eppo.int/gd/v2/eppo_api_gd_v2.yml).
#
# Needs EPPO_API_KEY in ~/.Renviron (token from the dashboard at data.eppo.int).
# Raw JSON is cached per code/service, so reruns only fetch what is missing.
#
# Outputs (2_processed_data/):
#   eppo_categorization.csv  one row per code x country/RPPO list entry
#   eppo_hosts.csv           one row per code x host plant
#   eppo_distribution.csv    one row per code x country/state
#   eppo_code_summary.csv    one row per EPPO code (Hughes + LEMIS live)
#   article_eppo_enriched.csv  step-01 table + per-code summary

library(httr2)
library(jsonlite)
library(dplyr)
library(readr)
library(purrr)
library(stringr)
library(tibble)

base_url  <- "https://api.eppo.int/gd/v2"
key_file  <- r"(C:\Users\dafl\OneDrive - Folkehelseinstituttet\API keys\EPPO_beta.txt)"
api_key   <- Sys.getenv("EPPO_API_KEY")
if (api_key == "" && file.exists(key_file)) {
  api_key <- trimws(readLines(key_file, n = 1, warn = FALSE))
}
if (api_key == "") stop("No EPPO key: set EPPO_API_KEY or check key_file.")

proc_dir  <- here::here("2_processed_data")
cache_dir <- file.path(proc_dir, "eppo_api_cache")
dir.create(cache_dir, showWarnings = FALSE, recursive = TRUE)

services <- c("overview", "categorization", "hosts", "distribution")

# --- API helper --------------------------------------------------------------
# Returns parsed JSON (list / data.frame) or NULL on 404. Caches raw text.
gd_get <- function(path, cache_file) {
  if (file.exists(cache_file)) {
    txt <- read_file(cache_file)
  } else {
    resp <- request(base_url) |>
      req_url_path_append(path) |>
      req_headers(`X-Api-Key` = api_key, Accept = "application/json") |>
      req_timeout(30) |>
      req_retry(max_tries = 5, retry_on_failure = TRUE,
                is_transient = \(r) resp_status(r) %in% c(429, 500, 502, 503)) |>
      req_error(is_error = \(r) FALSE) |>
      req_perform()
    st <- resp_status(resp)
    if (st == 401 || st == 403) stop("EPPO API auth failed (HTTP ", st, ")")
    txt <- if (st == 404) "null" else if (st == 200) resp_body_string(resp) else
      stop("HTTP ", st, " for ", path)
    # write to temp file then rename, so an interrupt never leaves a partial file
    tmp <- paste0(cache_file, ".tmp")
    write_file(txt, tmp)
    file.rename(tmp, cache_file)
  }
  if (txt %in% c("", "null")) return(NULL)
  fromJSON(txt, flatten = TRUE)
}

as_rows <- function(x, code) {
  if (is.null(x) || length(x) == 0) return(NULL)
  df <- if (is.data.frame(x)) x else as_tibble(compact(x))
  if (nrow(df) == 0) return(NULL)
  mutate(as_tibble(df), eppocode_query = code, .before = 1)
}

# --- 1. Codes to query -------------------------------------------------------
match_tbl <- read_csv(file.path(proc_dir, "article_eppo_match.csv"),
                      show_col_types = FALSE, guess_max = Inf)
lemis_tbl <- read_csv(file.path(proc_dir, "lemis_live_species.csv"),
                      show_col_types = FALSE, guess_max = Inf)
# Hughes codes + codes of live-imported LEMIS species (step 02)
codes <- sort(unique(na.omit(c(match_tbl$eppocode, lemis_tbl$lemis_eppocode))))
cat(sprintf("EPPO codes to query: %d x %d services\n",
            length(codes), length(services)))

# --- 2. Reference tables (code lists for qlist, status, host class) ----------
refs <- c("qList", "distributionStatus", "pestHostClassification")
ref_data <- set_names(map(refs, \(r)
  gd_get(file.path("references", r), file.path(cache_dir, paste0("ref_", r, ".json")))),
  refs)

# --- 3. Fetch per code -------------------------------------------------------
n_codes <- length(codes)
t0 <- Sys.time()
raw <- map(set_names(codes), \(code) {
  files <- file.path(cache_dir, paste0(code, "_", services, ".json"))
  n_new <- sum(!file.exists(files))
  out <- map(set_names(services), \(s)
    gd_get(file.path("taxons", "taxon", code, s),
           file.path(cache_dir, paste0(code, "_", s, ".json"))))
  i <- match(code, codes)
  cat(sprintf("[%d/%d] %-8s %s  (%.0fs)\n", i, n_codes, code,
              if (n_new == 0) "cached" else paste(n_new, "fetched"),
              as.numeric(difftime(Sys.time(), t0, units = "secs"))))
  out
})

# --- 4. Long tables ----------------------------------------------------------
long <- function(service) {
  imap(raw, \(x, code) as_rows(x[[service]], code)) |> list_rbind()
}
overview <- long("overview")
categ    <- long("categorization")
hosts    <- long("hosts")
distr    <- long("distribution")

write_csv(categ, file.path(proc_dir, "eppo_categorization.csv"), na = "")
write_csv(hosts, file.path(proc_dir, "eppo_hosts.csv"), na = "")
write_csv(distr, file.path(proc_dir, "eppo_distribution.csv"), na = "")
saveRDS(ref_data, file.path(proc_dir, "eppo_references.rds"))

# --- 5. Per-code summary -----------------------------------------------------
# Categorization: current entries only (no year_delete)
cat_now <- categ |>
  filter(is.na(year_delete)) |>
  mutate(entry = paste0(country_iso, ":", qlist_label))

# RPPO codes: 9A = EPPO, 9L = EU. qlist codes: see references/qList
# (1 A1, 2 A2, 9 Alert, X/0 Quarantine, N/O/P/Q EU 2019/2072, L IAS Union concern)
lab <- \(df, iso) paste(unique(df$qlist_label[df$country_iso == iso]),
                        collapse = "; ")
cat_sum <- cat_now |>
  group_by(eppocode_query) |>
  group_modify(\(d, k) tibble(
    n_categorizations = nrow(d),
    n_countries_categorized = n_distinct(d$country_iso[!str_detect(d$country_iso, "^9")]),
    eppo_A1 = any(d$country_iso == "9A" & d$qlist == "1"),
    eppo_A2 = any(d$country_iso == "9A" & d$qlist == "2"),
    eppo_list = lab(d, "9A"),
    eu_list = lab(d, "9L"),
    no_list = lab(d, "NO"),
    categorization = paste(sort(unique(d$entry)), collapse = "; "))) |>
  ungroup() |>
  mutate(across(c(eppo_list, eu_list, no_list), \(x) na_if(x, "")),
         eu_listed = !is.na(eu_list), no_listed = !is.na(no_list))

host_sum <- hosts |>
  group_by(eppocode_query) |>
  summarise(n_hosts = n(),
            n_major_hosts = sum(str_detect(class_label, regex("major", TRUE)),
                                na.rm = TRUE),
            hosts = paste(head(prefname, 20), collapse = "; "),
            .groups = "drop")

status_lab <- deframe(ref_data$distributionStatus[, c("peststatus",
                                                      "peststatus_label")])
dist_sum <- distr |>
  mutate(present = str_starts(status_lab[peststatus], "Present") |
           peststatus == "T") |>
  group_by(eppocode_query) |>
  summarise(n_dist_records = n(),
            n_countries_present = n_distinct(country_iso[present %in% TRUE]),
            no_status = paste(unique(status_lab[peststatus[country_iso == "NO"]]),
                              collapse = "; "),
            .groups = "drop") |>
  mutate(no_status = na_if(no_status, ""))

ov_sum <- overview |>
  select(eppocode_query, any_of(c("prefname", "is_active", "replacedby",
                                  "datatype"))) |>
  rename_with(\(x) paste0("api_", x), -eppocode_query)

summary_tbl <- tibble(eppocode_query = codes) |>
  left_join(ov_sum,   by = "eppocode_query") |>
  left_join(cat_sum,  by = "eppocode_query") |>
  left_join(host_sum, by = "eppocode_query") |>
  left_join(dist_sum, by = "eppocode_query") |>
  mutate(across(c(n_categorizations, n_countries_categorized, n_hosts,
                  n_major_hosts, n_dist_records, n_countries_present), \(x) coalesce(x, 0L)),
         across(c(eppo_A1, eppo_A2, eu_listed, no_listed),
                \(x) coalesce(x, FALSE)))

write_csv(summary_tbl |> rename(eppocode = eppocode_query),
          file.path(proc_dir, "eppo_code_summary.csv"), na = "")

enriched <- match_tbl |>
  left_join(summary_tbl, by = c("eppocode" = "eppocode_query"))
write_csv(enriched, file.path(proc_dir, "article_eppo_enriched.csv"), na = "")

# --- 6. Short report ---------------------------------------------------------
cat("\nCodes with any categorization:", sum(summary_tbl$n_categorizations > 0),
    "\nCodes with EPPO A1 / A2:", sum(summary_tbl$eppo_A1), "/",
    sum(summary_tbl$eppo_A2),
    "\nCodes EU-listed:", sum(summary_tbl$eu_listed),
    "\nCodes categorized by Norway:", sum(summary_tbl$no_listed),
    "\nCodes with host plants:", sum(summary_tbl$n_hosts > 0), "\n")

cat("\nCategorized or with hosts (insects first):\n")
enriched |>
  filter(n_categorizations > 0 | n_hosts > 0) |>
  arrange(desc(is_insect %in% TRUE), desc(n_categorizations)) |>
  select(name, eppocode, eppo_order, n_cat = n_categorizations, eppo_list,
         eu_list, no_list, n_hosts, no_status) |>
  as.data.frame() |>
  print()
