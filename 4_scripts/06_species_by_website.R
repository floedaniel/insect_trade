# 06_species_by_website.R
# Tie candidate species names to real sale pages (pipeline steps 2-3).
#
# For each discovered shop (default: shops labelled "live"):
#   1. read robots.txt and obey it (Disallow/Allow for our agent and "*",
#      Crawl-delay); at least DELAY seconds between requests to one shop
#   2. collect product URLs from the sitemaps (product sitemaps first); if a
#      shop has no sitemap, use links and link text from the home page and the
#      page where the shop was found, plus category pages one level down
#   3. search URL slugs, image titles and link text for candidate species
#      names (accepted names, synonyms, EPPO names; "genus species" or
#      "genus-species"), the method of Marshall et al. 2022 (Code S04-S05)
#   4. fetch the matching product pages (regulated species first, capped per
#      shop) and record title, price, currency, availability, live/dead label
#      (3_metadata/life_state_terms.csv), seller-country hint (domain) and a
#      ships-to hint (Norway / EU / international / domestic only)
#
# Every HTTP response is cached in 2_processed_data/web_cache/, each shop's
# result in 2_processed_data/web_results/, so runs can be stopped and resumed.
# No personal data is stored: only shop domains and product pages.
#
# Output: 5_outputs/tables/species_by_website.xlsx (xlsx only)
#
# Usage: Rscript 4_scripts/06_species_by_website.R [shop_states] [n_workers]
#   shop_states: comma list of discovered_shops life_state values (default "live")
#   n_workers:   shops crawled in parallel (default 8)
#   max_shops:   only the first n shops (pilot), default all

suppressMessages({
  library(dplyr); library(readr); library(stringr); library(purrr)
  library(tidyr); library(httr2); library(xml2); library(jsonlite)
})
source(here::here("4_scripts", "utils_xlsx.R"))

args        <- commandArgs(trailingOnly = TRUE)
SHOP_STATES <- if (length(args) >= 1) strsplit(args[1], ",")[[1]] else "live"
N_WORKERS   <- if (length(args) >= 2) as.integer(args[2]) else 8L
MAX_SHOPS   <- if (length(args) >= 3) as.integer(args[3]) else Inf  # pilot runs
DELAY       <- 1.5      # seconds between requests to the same shop (minimum)
MAX_SITEMAPS <- 25      # child sitemaps read per shop
MAX_URLS    <- 30000    # sitemap URLs kept per shop
MAX_PAGES   <- 40       # product pages fetched per shop
MAX_CATEG   <- 25       # category pages fetched per shop (no-sitemap fallback)
UA <- "insect-trade-research/0.1 (non-commercial plant-pest screening; obeys robots.txt)"

proc     <- \(...) here::here("2_processed_data", ...)
out_dir  <- here::here("5_outputs", "tables")
cache_root  <- proc("web_cache");   dir.create(cache_root, showWarnings = FALSE)
result_root <- proc("web_results"); dir.create(result_root, showWarnings = FALSE)

# --- 1. Names to search -------------------------------------------------------
acc <- read_csv(proc("species_accepted_state.csv"), show_col_types = FALSE,
                guess_max = Inf)
sp  <- read_csv(proc("species_scope_state.csv"), show_col_types = FALSE,
                guess_max = Inf)
cand <- acc |> filter(in_scope, life_state != "dead")

norm_txt <- function(x) {
  x <- str_to_lower(x)
  x <- str_replace_all(x, "%20|[-_+/.,;:()\\[\\]|=&?#\"'*]", " ")
  str_squish(str_replace_all(x, "[^a-z ]", " "))
}
aliases <- bind_rows(
  sp |> transmute(alias = name, accepted_name = coalesce(gbif_accepted, name)),
  sp |> filter(!is.na(gbif_accepted)) |>
    transmute(alias = gbif_accepted, accepted_name = gbif_accepted),
  cand |> filter(!is.na(eppo_name)) |>
    transmute(alias = word(eppo_name, 1, 2), accepted_name),
  cand |> transmute(alias = accepted_name, accepted_name)) |>
  mutate(key = norm_txt(word(alias, 1, 2))) |>
  filter(str_count(key, " ") == 1, nchar(key) >= 7) |>
  semi_join(cand, by = "accepted_name") |>
  distinct(key, accepted_name)
# a key that points to two accepted species is ambiguous: drop it
aliases <- aliases |> group_by(key) |> filter(n() == 1) |> ungroup() |>
  left_join(cand |> select(accepted_name, regulated), by = "accepted_name")
name_keys <- set_names(aliases$accepted_name, aliases$key)
# genus words used to spot pages about another taxon (all our names + EPPO)
known_genera <- unique(c(word(sp$name, 1), word(na.omit(sp$gbif_accepted), 1),
  {con <- DBI::dbConnect(RSQLite::SQLite(), here::here("1_raw_data", "eppo db", "eppocodes.sqlite"))
   g <- DBI::dbGetQuery(con, "SELECT DISTINCT n.fullname FROM t_names n JOIN t_codes c ON c.codeid = n.codeid WHERE c.dtcode = 'SIT' AND c.eppocode LIKE '%G' AND n.codelang = 'la'")$fullname
   DBI::dbDisconnect(con); g}, "Collembola"))
regulated_names <- cand$accepted_name[cand$regulated]
cat(sprintf("Search names: %d keys for %d candidate species (%d regulated)\n",
            nrow(aliases), n_distinct(aliases$accepted_name),
            length(regulated_names)))

# --- 1b. Common names from GBIF (vernacular names by species key) ------------
# Feeder and pet shops often use only common names ("hornworm", "fire ant").
# A common name is kept if it points to one candidate species; if it points
# to several species of ONE genus it becomes a genus hit ("Pomacea spp.").
# Common-name hits are weaker evidence than scientific-name hits.
langs <- c("eng", "nor", "nob", "nno", "deu", "swe", "dan", "nld", "fra",
           "spa", "pol")
generic <- c("ant", "ants", "beetle", "beetles", "snail", "snails", "cricket",
             "crickets", "butterfly", "butterflies", "moth", "moths", "worm",
             "worms", "roach", "roaches", "cockroach", "mantis", "fly", "flies",
             "bee", "bees", "wasp", "bug", "bugs", "grasshopper", "locust",
             "slug", "slugs", "mite", "mites", "caterpillar", "stick insect",
             "leaf insect", "weevil", "termite", "scarab", "isopod",
             "mealworm beetle", "darkling beetle", "rhinoceros beetle",
             "stag beetle", "praying mantis", "giant snail", "land snail",
             "mosquito", "mosquitoes", "springtail", "springtails", "isopods",
             "tarantula", "scorpion", "centipede", "millipede", "earwig",
             "aphid", "aphids", "lacewing", "ladybird", "ladybug", "ladybugs",
             "firefly", "cicada", "dragonfly", "hornet", "louse", "flea",
             "tick", "spider", "gnat", "midge", "mantid", "phasmid", "katydid",
             "sawfly", "leafhopper", "planthopper", "thrips", "whitefly",
             "scale insect", "mealybug", "bumblebee", "honeybee", "honey bee",
             "silkmoth", "hawkmoth", "skipper", "tropical white")

# page types: only product and category pages count as evidence of sale
info_rx <- "/blogs?/|care[- ]?sheet|care[- ]?guide|/guide|/pages/|/faq|archives|/tag/|/news/|/article|/wiki|how[- ]to"
accessory_rx <- "\\bfood\\b|\\bdiet\\b|\\bchow\\b|feeding block|\\bgel\\b|substrate|leaf litter|bedding|\\bcages?\\b|enclosure|terrarium|habitat kit|repellent|killer|\\btrap\\b|\\bbait\\b|insecticide|\\bbooks?\\b|poster|sticker|shirt|\\bmug\\b|plush|\\btoy\\b|figure|\\bdish\\b|\\bbowl\\b|heat ?mat|thermometer|\\bnet\\b|incubator|cup ?lids?|deli ?cups?|\\bgift card"
category_rx <- "/collections?/|/categor(y|ies)/|/product-category/|/c/|/shop/?$|/kategori|/kategorie"
page_type <- function(url, title) {
  slug <- sub("^https?://[^/]+", "", url)
  t <- paste(coalesce(title, ""), gsub("[-_]", " ", slug))
  case_when(grepl(info_rx, slug, ignore.case = TRUE) |
              grepl("care sheet|archives|blog", coalesce(title, ""), ignore.case = TRUE) ~ "info",
            grepl(accessory_rx, t, ignore.case = TRUE) ~ "accessory",
            grepl(category_rx, slug, ignore.case = TRUE) ~ "category",
            TRUE ~ "product")
}
vern_dir <- proc("gbif_vernacular_cache"); dir.create(vern_dir, showWarnings = FALSE)
keys <- unique(na.omit(as.character(cand$gbif_key)))
need <- keys[!file.exists(file.path(vern_dir, paste0(keys, ".json")))]
if (length(need)) {
  cat(sprintf("Fetching GBIF vernacular names for %d species keys\n", length(need)))
  reqs <- map(need, \(k) request(sprintf("https://api.gbif.org/v1/species/%s/vernacularNames", k)) |>
                req_url_query(limit = 300) |> req_retry(max_tries = 4) |>
                req_error(is_error = \(r) FALSE))
  resps <- req_perform_parallel(reqs, max_active = 8, on_error = "continue",
                                progress = FALSE)
  walk2(need, resps, \(k, r) if (inherits(r, "httr2_response") && resp_status(r) == 200)
    write_file(resp_body_string(r), file.path(vern_dir, paste0(k, ".json"))))
}
vern <- map(keys, \(k) {
  f <- file.path(vern_dir, paste0(k, ".json"))
  if (!file.exists(f)) return(NULL)
  x <- fromJSON(f)$results
  if (!length(x) || !is.data.frame(x)) return(NULL)
  tibble(gbif_key = k, common = x$vernacularName, language = x$language)
}) |> list_rbind() |>
  filter(language %in% langs) |>
  left_join(cand |> transmute(gbif_key = as.character(gbif_key), accepted_name, regulated),
            by = "gbif_key", relationship = "many-to-many") |>
  mutate(key = norm_txt(common)) |>
  filter(nchar(key) >= 6, str_count(key, " ") <= 3, !key %in% generic,
         !key %in% names(name_keys))
common_tbl <- vern |>
  group_by(key) |>
  summarise(n_sp = n_distinct(accepted_name), genus = n_distinct(word(accepted_name, 1)),
            target = if (n_distinct(accepted_name) == 1) first(accepted_name)
                     else paste(word(first(accepted_name), 1), "spp."),
            all_regulated = all(regulated), any_regulated = any(regulated),
            members = paste(sort(unique(accepted_name)), collapse = "; "),
            common = first(common), .groups = "drop") |>
  filter(genus == 1)       # drop names spread over several genera
common_keys <- set_names(common_tbl$target, common_tbl$key)
cat(sprintf("Common names: %d (in %d languages) -> %d targets (%d genus-level)\n",
            nrow(common_tbl), n_distinct(vern$language), n_distinct(common_tbl$target),
            sum(grepl(" spp\\.$", common_tbl$target))))

# all 1-4 word sequences in a text, matched against scientific names
# (2 words) and common names (1-4 words). Returns "target||match_type".
find_names <- function(text) {
  w <- strsplit(norm_txt(text), " ", fixed = TRUE)[[1]]
  if (!length(w) || !nzchar(w[1])) return(character())
  ng <- function(n) if (length(w) >= n)
    unique(vapply(seq_len(length(w) - n + 1), \(i) paste(w[i:(i + n - 1)], collapse = " "), "")) else character()
  bi <- ng(2)
  sci <- unique(unname(name_keys[bi[bi %in% names(name_keys)]]))
  grams <- c(ng(1), bi, ng(3), ng(4))
  # shops write plurals ("superworms", "dubia roaches"): also try singular
  grams <- unique(c(grams, sub("(?<=[a-z]{3})(es|s)$", "", grams, perl = TRUE),
                    sub("(?<=[a-z]{3})s$", "", grams, perl = TRUE)))
  com <- unique(unname(common_keys[grams[grams %in% names(common_keys)]]))
  com <- setdiff(com, sci)
  as.character(c(if (length(sci)) paste0(sci, "||scientific name"),
                 if (length(com)) paste0(com, "||common name")))
}
# genus targets ("Pomacea spp.") are regulated when every member is
genus_reg <- common_tbl |> filter(grepl(" spp\\.$", target)) |>
  group_by(target) |> summarise(regulated = all(all_regulated), .groups = "drop")
regulated_names <- c(regulated_names, genus_reg$target[genus_reg$regulated])

# --- 2. Shops ------------------------------------------------------------------
# working csv written by insect_trade_loop.py (results go out as xlsx only)
shops <- read_csv(proc("discovered_shops.csv"),
                  show_col_types = FALSE, col_types = cols(.default = "c")) |>
  distinct(domain, .keep_all = TRUE) |>
  filter(life_state %in% SHOP_STATES) |>
  select(domain, found_url = url, shop_title = title, shop_life_state = life_state) |>
  head(MAX_SHOPS)
cat(sprintf("Shops to crawl: %d (life_state: %s)\n", nrow(shops),
            paste(SHOP_STATES, collapse = ", ")))

# --- 3. Crawler (one shop at a time per worker) ------------------------------
crawl_shop <- function(domain, found_url) {
  res_file <- file.path(result_root, paste0(domain, ".rds"))
  if (file.exists(res_file)) return(readRDS(res_file))
  cache_dir <- file.path(cache_root, domain)
  dir.create(cache_dir, showWarnings = FALSE, recursive = TRUE)
  last_req <- 0
  delay <- DELAY
  status <- list(domain = domain, robots = NA, crawl_delay = NA,
                 sitemap_urls = 0L, link_urls = 0L, method = NA,
                 pages_fetched = 0L, error = NA_character_)

  fetch <- function(url, type = "page") {
    f <- file.path(cache_dir, paste0(type, "_", rlang::hash(url), ".txt"))
    if (file.exists(f)) {
      txt <- read_file(f)
      return(if (identical(txt, "\u0001ERR")) NULL else txt)
    }
    wait <- delay - as.numeric(Sys.time()) + last_req
    if (wait > 0) Sys.sleep(wait)
    last_req <<- as.numeric(Sys.time())
    r <- tryCatch(
      request(url) |> req_user_agent(UA) |> req_timeout(25) |>
        req_error(is_error = \(r) FALSE) |> req_perform(),
      error = \(e) NULL)
    ok <- !is.null(r) && resp_status(r) == 200
    txt <- if (ok) tryCatch(resp_body_string(r), error = \(e) NULL) else NULL
    write_file(if (is.null(txt)) "\u0001ERR" else txt, f)
    txt
  }

  base <- paste0("https://", domain)

  # robots.txt: rules for our agent token or "*"
  rob <- fetch(paste0(base, "/robots.txt"), "robots")
  rules <- tibble(type = character(), path = character())
  sitemaps <- character()
  if (!is.null(rob)) {
    status$robots <- "found"
    lines <- str_trim(str_remove(strsplit(rob, "\r?\n")[[1]], "#.*$"))
    agents <- character(); in_rules <- FALSE; applies <- FALSE
    for (ln in lines) {
      kv <- str_match(ln, "^([A-Za-z-]+)\\s*:\\s*(.*)$")
      if (is.na(kv[1, 1])) next
      k <- str_to_lower(kv[1, 2]); v <- kv[1, 3]
      if (k == "sitemap") { sitemaps <- c(sitemaps, v); next }
      if (k == "user-agent") {
        if (in_rules) { agents <- character(); in_rules <- FALSE }
        agents <- c(agents, str_to_lower(v))
        applies <- any(agents %in% c("*", "insect-trade-research"))
        next
      }
      in_rules <- TRUE
      if (!applies) next
      if (k %in% c("disallow", "allow") && nzchar(v))
        rules <- tibble::add_row(rules, type = k, path = v)
      if (k == "crawl-delay") {
        cd <- suppressWarnings(as.numeric(v))
        if (!is.na(cd)) { status$crawl_delay <- cd; delay <- max(DELAY, min(cd, 30)) }
      }
    }
  } else status$robots <- "none"

  rule_rx <- if (nrow(rules)) paste0("^", str_replace_all(
    str_replace_all(rules$path, "([.+?^(){}|\\[\\]\\\\])", "\\\\\\1"),
    c("\\*" = ".*")), "") else character()
  rule_rx <- str_replace(rule_rx, "\\\\\\$$", "$")
  allowed <- function(url) {
    if (!nrow(rules)) return(TRUE)
    path <- sub("^https?://[^/]+", "", url); if (!nzchar(path)) path <- "/"
    hit <- vapply(rule_rx, \(rx) grepl(rx, path, perl = TRUE), logical(1))
    if (!any(hit)) return(TRUE)
    best <- which(hit)[which.max(nchar(rules$path[hit]))]
    rules$type[best] == "allow"
  }

  # sitemaps (product sitemaps first)
  sm_queue <- unique(c(sitemaps, paste0(base, c("/sitemap.xml", "/sitemap_index.xml"))))
  urls <- tibble(url = character(), text = character())
  seen_sm <- character()
  while (length(sm_queue) && length(seen_sm) < MAX_SITEMAPS && nrow(urls) < MAX_URLS) {
    sm <- sm_queue[1]; sm_queue <- sm_queue[-1]
    if (sm %in% seen_sm || !allowed(sm)) next
    seen_sm <- c(seen_sm, sm)
    txt <- fetch(sm, "sitemap")
    if (is.null(txt) || !grepl("<(urlset|sitemapindex)", txt)) next
    doc <- tryCatch(xml_ns_strip(read_xml(txt)), error = \(e) NULL)
    if (is.null(doc)) next
    child <- xml_text(xml_find_all(doc, "//sitemap/loc"))
    if (length(child)) {
      pri <- grepl("product|produkt|shop|item|artikel|catalog", child, ignore.case = TRUE)
      sm_queue <- unique(c(child[pri], sm_queue, child[!pri]))
    }
    nodes <- xml_find_all(doc, "//url")
    if (length(nodes)) {
      loc <- xml_text(xml_find_first(nodes, "./loc"))
      img <- vapply(nodes, \(n) paste(xml_text(xml_find_all(n, ".//*[local-name()='title' or local-name()='caption']")),
                                      collapse = " "), character(1))
      urls <- bind_rows(urls, tibble(url = loc, text = img))
    }
  }
  status$sitemap_urls <- nrow(urls)

  # fallback: links on the home page, the found page and category pages
  if (nrow(urls) == 0) {
    get_links <- function(u) {
      if (!allowed(u)) return(tibble(url = character(), text = character()))
      h <- fetch(u, "page"); if (is.null(h)) return(tibble(url = character(), text = character()))
      doc <- tryCatch(read_html(h), error = \(e) NULL)
      if (is.null(doc)) return(tibble(url = character(), text = character()))
      a <- xml_find_all(doc, "//a[@href]")
      tibble(url = url_absolute(xml_attr(a, "href"), u), text = xml_text(a)) |>
        filter(grepl(paste0("^https?://(www\\.)?", gsub("^www\\.", "", domain)), url))
    }
    start <- unique(na.omit(c(paste0(base, "/"), found_url)))
    lk <- map(start, get_links) |> list_rbind()
    categ <- lk |> filter(grepl("insect|feeder|live|ant|beetle|butterfl|snail|mantis|stick|phasm|isopod|roach|cricket|larv|tarantula|bug|invert|insekt|futter|levend|koloni|ameise|maur|käfer|schmetter|caracol|escarab",
                                paste(url, text), ignore.case = TRUE)) |>
      distinct(url) |> head(MAX_CATEG)
    lk2 <- map(categ$url, get_links) |> list_rbind()
    urls <- bind_rows(lk, lk2) |> distinct(url, .keep_all = TRUE)
    status$link_urls <- nrow(urls)
  }
  status$method <- if (status$sitemap_urls > 0) "sitemap" else if (status$link_urls > 0) "links" else "none"

  # match names in slug + image/link text
  hits <- urls |>
    filter(!is.na(url)) |>
    mutate(slug = sub("^https?://[^/]+", "", url),
           species = map(paste(slug, text), find_names)) |>
    filter(lengths(species) > 0) |>
    unnest(species) |>
    separate_wider_delim(species, "||", names = c("species", "match_type")) |>
    distinct(url, species, .keep_all = TRUE)

  if (nrow(hits) == 0) {
    out <- list(status = status, hits = tibble())
    saveRDS(out, res_file); return(out)
  }

  # fetch matched product pages: regulated species first
  pages <- hits |>
    mutate(reg = species %in% regulated_names,
           info = grepl(info_rx, slug, ignore.case = TRUE) |
             grepl(accessory_rx, gsub("[-_]", " ", paste(slug, text)), ignore.case = TRUE)) |>
    arrange(desc(reg), info) |>
    distinct(url, .keep_all = TRUE) |>
    filter(map_lgl(url, allowed)) |>
    head(MAX_PAGES)

  page_info <- map(pages$url, \(u) {
    h <- fetch(u, "page")
    if (is.null(h)) return(tibble(url = u, fetched = FALSE))
    doc <- tryCatch(read_html(h), error = \(e) NULL)
    if (is.null(doc)) return(tibble(url = u, fetched = FALSE))
    meta <- \(p) xml_attr(xml_find_first(doc, sprintf("//meta[@property='%s' or @name='%s']", p, p)), "content")
    title <- coalesce(meta("og:title"), xml_text(xml_find_first(doc, "//h1")),
                      xml_text(xml_find_first(doc, "//title")))
    # JSON-LD Product (name, description, offers)
    ld <- xml_text(xml_find_all(doc, "//script[@type='application/ld+json']"))
    prod <- NULL
    for (j in ld) {
      x <- tryCatch(fromJSON(j, simplifyVector = FALSE), error = \(e) NULL)
      cands <- if (!is.null(x[["@graph"]])) x[["@graph"]] else if (is.null(names(x))) x else list(x)
      for (o in cands) if (is.list(o) && any(grepl("Product", unlist(o[["@type"]])))) { prod <- o; break }
      if (!is.null(prod)) break
    }
    # JSON-LD fields can be strings, lists or arrays: take the first value
    first1 <- \(x) if (is.null(x) || !length(x)) NA_character_ else
      as.character(unlist(x, use.names = FALSE))[1]
    off <- prod$offers
    if (!is.null(off) && is.null(names(off))) off <- off[[1]]
    if (!is.null(off) && !is.null(off$offers)) {        # AggregateOffer
      inner <- off$offers; if (is.null(names(inner))) inner <- inner[[1]]
      off$price <- off$price %||% off$lowPrice %||% inner$price
    }
    descr <- coalesce(first1(prod$description), meta("og:description"),
                      meta("description"))
    num1 <- \(x) { v <- as.character(unlist(x, use.names = FALSE))
      v <- v[grepl("^[0-9]+([.,][0-9]+)?$", v) & suppressWarnings(as.numeric(sub(",", ".", v))) > 0]
      if (length(v)) v[1] else NA_character_ }
    price <- coalesce(num1(off$price), num1(off$lowPrice),
                      num1(off$priceSpecification), num1(off),
                      meta("product:price:amount"))
    curr <- coalesce(first1(off$priceCurrency), meta("product:price:currency"))
    avail <- first1(off$availability)
    body <- str_squish(xml_text(xml_find_first(doc, "//body")))
    if (is.na(price)) {
      # first non-zero money amount in the page text (weaker; e.g. not "$0.00" cart)
      pm <- str_extract_all(body, "(?:[€$£]|NOK|kr|EUR|USD|GBP)\\s?\\d+[\\.,]?\\d*|\\d+[\\.,]\\d{2}\\s?(?:€|EUR|NOK|kr|USD|\\$|£)")[[1]]
      pm <- pm[!grepl("(^|\\D)0[\\.,]00(\\D|$)", pm)]
      price <- if (length(pm)) paste(pm[1], "(page text)") else NA_character_
    }
    ships <- c(
      norway = grepl("\\b(norway|norge|norwegen|norv[eè]ge|noruega|norwegia)\\b", body, ignore.case = TRUE),
      eu = grepl("ship(s|ping)? (to|within|across) (the )?(eu|europe)|europaweit|eu-weit|livraison en europe|envíos? a europa|eu shipping|europe shipping", body, ignore.case = TRUE),
      worldwide = grepl("worldwide|international shipping|ship internationally|weltweit|international delivery", body, ignore.case = TRUE),
      domestic = grepl("(only|exclusively) (ship|deliver)\\w* (within|to) (the )?(us|usa|united states|uk|germany|deutschland)|lower 48|contiguous (us|united states)|nur innerhalb deutschlands|we do not ship (outside|internationally)|no international shipping", body, ignore.case = TRUE))
    tibble(url = u, fetched = TRUE, page_title = str_trunc(title %||% NA, 200),
           description = str_trunc(descr %||% NA, 300), price = price,
           currency = curr, availability = sub(".*/", "", avail),
           ships_to_hint = paste(names(ships)[ships], collapse = ";"),
           name_on_page = NA)
  }) |> list_rbind()
  status$pages_fetched <- sum(page_info$fetched %in% TRUE)

  out_hits <- hits |>
    left_join(page_info, by = "url") |>
    mutate(domain = domain, .before = 1)
  out <- list(status = status, hits = out_hits)
  saveRDS(out, res_file)
  out
}

# --- 4. Run in parallel (one shop per worker at a time) ----------------------
todo <- shops |> filter(!file.exists(file.path(result_root, paste0(domain, ".rds"))))
cat(sprintf("Already done: %d | to crawl: %d | workers: %d\n",
            nrow(shops) - nrow(todo), nrow(todo), N_WORKERS))
if (nrow(todo) > 0) {
  cl <- parallel::makeCluster(N_WORKERS)
  on.exit(parallel::stopCluster(cl), add = TRUE)
  parallel::clusterEvalQ(cl, suppressMessages({
    library(dplyr); library(readr); library(stringr); library(purrr)
    library(tidyr); library(httr2); library(xml2); library(jsonlite)}))
  parallel::clusterExport(cl, c("crawl_shop", "find_names", "norm_txt",
                                "name_keys", "common_keys", "regulated_names", "cache_root",
                                "info_rx", "accessory_rx",
                                "result_root", "DELAY", "MAX_SITEMAPS",
                                "MAX_URLS", "MAX_PAGES", "MAX_CATEG", "UA"))
  t0 <- Sys.time()
  idx <- seq_len(nrow(todo))
  chunks <- split(idx, ceiling(idx / N_WORKERS))
  for (ch in chunks) {
    # (argument names must not partially match clusterApplyLB's own `fun`)
    r <- parallel::clusterApplyLB(cl, ch, \(i, doms, furls) {
      tryCatch({ x <- crawl_shop(doms[i], furls[i]); c(doms[i], nrow(x$hits)) },
               error = \(e) c(doms[i], paste("error:", conditionMessage(e))))
    }, doms = todo$domain, furls = todo$found_url)
    for (x in r) cat(sprintf("[%d/%d] %-35s hits: %s  (%.0f min)\n",
                             match(x[1], todo$domain), nrow(todo), x[1], x[2],
                             as.numeric(difftime(Sys.time(), t0, units = "mins"))))
  }
}

# --- 5. Combine and write -----------------------------------------------------
res <- map(shops$domain, \(d) {
  f <- file.path(result_root, paste0(d, ".rds"))
  if (file.exists(f)) readRDS(f) else NULL
}) |> compact()
status_tbl <- map(res, \(x) as_tibble(x$status)) |> list_rbind()
hits <- map(res, "hits") |> keep(\(h) nrow(h) > 0) |> list_rbind()

tld_country <- function(d) {
  t <- str_extract(d, "\\.[a-z]{2,3}$")
  m <- c(.no = "NO", .se = "SE", .dk = "DK", .fi = "FI", .de = "DE", .at = "AT",
         .ch = "CH", .nl = "NL", .be = "BE", .fr = "FR", .es = "ES", .it = "IT",
         .pl = "PL", .cz = "CZ", .sk = "SK", .hu = "HU", .pt = "PT", .ie = "IE",
         .uk = "GB", .lt = "LT", .lv = "LV", .ee = "EE", .ro = "RO", .bg = "BG",
         .gr = "GR", .si = "SI", .hr = "HR", .ca = "CA", .au = "AU", .nz = "NZ",
         .jp = "JP", .ru = "RU", .ua = "UA", .eu = "EU")
  unname(m[t])
}

# live/dead label from the shared term list (same rules as life_state.py)
terms <- read_csv(here::here("3_metadata", "life_state_terms.csv"),
                  show_col_types = FALSE)
dead_rx <- paste0("(?<!\\w)(?:", terms$pattern[terms$state == "dead"], ")")
live_rx <- paste0("(?<!\\w)(?:", terms$pattern[terms$state == "live"], ")")
classify <- function(text) {
  text <- coalesce(text, "")
  d <- vapply(dead_rx, \(rx) grepl(rx, text, ignore.case = TRUE, perl = TRUE), logical(1))
  s <- text
  for (rx in dead_rx[d]) s <- gsub(rx, " ", s, ignore.case = TRUE, perl = TRUE)
  l <- vapply(live_rx, \(rx) grepl(rx, s, ignore.case = TRUE, perl = TRUE), logical(1))
  state <- if (any(d) && any(l)) "check" else if (any(d)) "dead" else if (any(l)) "live" else "unknown"
  terms_found <- c(if (any(d)) paste0("dead:", terms$pattern[terms$state == "dead"][d]),
                   if (any(l)) paste0("live:", terms$pattern[terms$state == "live"][l]))
  c(state, paste(terms_found, collapse = "; "))
}

if (nrow(hits) > 0) {
  hits <- hits |>
    mutate(label_text = paste(coalesce(page_title, ""), coalesce(description, ""),
                              sub("^https?://[^/]+", "", url)))
  lab <- map(hits$label_text, classify)
  hits <- hits |>
    mutate(life_state = map_chr(lab, 1), life_terms = map_chr(lab, 2),
           # is the species name in the page title/description itself?
           name_in_title = map2_lgl(paste(page_title, description), species,
                                    \(t, s) any(startsWith(find_names(t), paste0(s, "||")))),
           seller_country_hint = tld_country(domain),
           page_type = page_type(url, page_title),
           # common-name hit, but the page names another genus ("Collembola sp.")
           other_genus = match_type == "common name" & map2_lgl(
             coalesce(page_title, ""), species, \(t, s) {
               g <- str_match_all(t, "\\b([A-Z][a-z]{2,}) (?:sp\\.|spp\\.|[a-z]{3,}\\b)")[[1]][, 2]
               g <- g[g %in% known_genera]
               length(g) > 0 && !word(s, 1) %in% g })) |>
    filter(!other_genus) |>
    left_join(cand |> select(accepted_name, eppocode, gbif_key, pest_group,
                             species_life_state = life_state, regulated,
                             eppo_list, eu_list, no_list),
              by = c("species" = "accepted_name")) |>
    left_join(shops |> select(domain, shop_title), by = "domain") |>
    left_join(common_tbl |> filter(grepl(" spp\\.$", target)) |>
                distinct(target, members), by = c("species" = "target")) |>
    mutate(regulated = species %in% regulated_names,
           pest_group = coalesce(pest_group, if_else(!is.na(members), "genus (common name)", NA_character_))) |>
    transmute(species, match_type, regulated, eppocode, gbif_key, pest_group, domain,
              shop_title, url, page_type, page_title, life_state, life_terms, price,
              currency, availability, seller_country_hint, ships_to_hint,
              name_in_title, fetched, eppo_list, eu_list, no_list,
              species_life_state, genus_members = members,
              match_text = str_trunc(text, 150)) |>
    arrange(desc(regulated), species, domain)
} else {
  hits <- tibble(species = character())
}

# evidence of sale = product and category pages; info/accessory pages kept in
# the hits sheet but not counted
sale <- if (nrow(hits)) filter(hits, page_type %in% c("product", "category")) else hits
by_species <- if (nrow(sale)) sale |>
  group_by(species, regulated, eppocode, pest_group) |>
  summarise(match_types = paste(sort(unique(match_type)), collapse = "; "),
            n_shops = n_distinct(domain), n_pages = n(),
            n_product_pages = sum(page_type == "product"),
            n_live = sum(life_state == "live", na.rm = TRUE),
            n_dead = sum(life_state == "dead", na.rm = TRUE),
            shops = paste(head(unique(domain), 10), collapse = "; "),
            .groups = "drop") |>
  arrange(desc(regulated), desc(n_shops)) else tibble()
by_shop <- if (nrow(sale)) sale |>
  group_by(domain, shop_title, seller_country_hint) |>
  summarise(n_species = n_distinct(species),
            n_regulated = n_distinct(species[regulated %in% TRUE]),
            regulated_species = paste(unique(species[regulated %in% TRUE]), collapse = "; "),
            ships_to_hints = paste(sort(unique(unlist(strsplit(na.omit(ships_to_hint), ";")))), collapse = ";"),
            .groups = "drop") |>
  arrange(desc(n_regulated), desc(n_species)) else tibble()

guide <- tibble::tribble(
  ~column, ~meaning,
  "species", "Accepted name of the candidate species found, or \"Genus spp.\" when a common name covers several species of one genus",
  "match_type", "scientific name (strong) or common name (weaker; from GBIF vernacular names)",
  "page_type", "product / category = evidence of sale (counted); info (blog, care sheet) and accessory (food, cages, repellent ...) are listed but not counted",
  "url / page_title", "Sale page where the name was found",
  "life_state", "live / dead / check / unknown, from page title, description and URL (3_metadata/life_state_terms.csv)",
  "name_in_title", "TRUE if the species name is in the page title/description (stronger than a URL-only match)",
  "price / currency / availability", "From structured product data (JSON-LD) or the page text",
  "seller_country_hint", "From the shop's country domain (.de, .no ...); NA for .com etc.",
  "ships_to_hint", "Page mentions: norway, eu, worldwide, domestic (only). Heuristic, check by hand",
  "match_text", "Image title or link text that matched (sitemap/links)",
  "method", "Sitemap scan, or link scan when the shop has no sitemap",
  "Limits", sprintf("robots.txt obeyed; >= %.1f s between requests per shop; max %d sitemaps, %d product pages per shop. Terms of service are not checked automatically.", DELAY, MAX_SITEMAPS, MAX_PAGES))

# results: xlsx only (working copy of the hits in 2_processed_data)
write_csv(hits, proc("species_by_website_hits.csv"), na = "")
write_xlsx_tables(list(hits = hits, by_species = by_species, by_shop = by_shop,
                       shop_status = status_tbl, column_guide = guide),
                  file.path(out_dir, "species_by_website.xlsx"))

cat(sprintf("\nShops crawled: %d | method: %s\n", nrow(status_tbl),
            paste(names(table(status_tbl$method)), table(status_tbl$method), collapse = ", ")))
cat(sprintf("Hits: %d pages | %d species | %d shops | regulated species found: %d\n",
            nrow(hits), n_distinct(hits$species), n_distinct(hits$domain),
            n_distinct(hits$species[hits$regulated %in% TRUE])))
if (nrow(hits)) {
  cat("\nLife state of hit pages:\n"); print(table(hits$life_state, useNA = "ifany"))
  cat("\nRegulated species found:\n")
  print(as.data.frame(by_species |> filter(regulated %in% TRUE)))
  cat("\nTop species by number of shops:\n")
  print(as.data.frame(head(by_species, 20)))
}
cat("Written:", file.path(out_dir, "species_by_website.xlsx"), "\n")
