# 01_match_article_eppo.R
# Match invertebrate + Lepidoptera names from Hughes et al. 2026 (Sci. Adv.
# eaei3504) to EPPO codes using the local EPPO codes database.
# Exact name match on Latin names (preferred + synonyms), no code guessing.
# Output: 2_processed_data/article_eppo_match.csv (one row per article name)


library(DBI)
library(RSQLite)
library(dplyr)
library(readr)
library(stringr)
library(tidyr)

art_dir <- here::here("1_raw_data", "hughes2026_figshare_33194898")
db_path <- here::here("1_raw_data", "eppo db", "eppocodes.sqlite")
out_csv <- here::here("2_processed_data", "article_eppo_match.csv")

# --- 1. Article names --------------------------------------------------------
flag_cols <- c("CITES Trade", "CITES App", "LEMIS", "LEMIS_noS",
               "IUCN", "TRAFFIC", "Research")

read_comp <- function(f) {
  read_csv(file.path(art_dir, f), show_col_types = FALSE) |>
    rename(name = corrected, art_group = Taxa)
}

comp <- bind_rows(read_comp("upSetData_singleNames_inv_comp.csv"),
                  read_comp("upSetData_singleNames_but_comp.csv")) |>
  mutate(name = str_squish(name)) |>
  filter(!is.na(name), name != "") |>
  # names in both lists: keep one row, OR the source flags, join group labels
  group_by(name) |>
  summarise(art_group = paste(sort(unique(art_group)), collapse = ";"),
            across(all_of(flag_cols), ~ as.integer(any(.x == 1))),
            .groups = "drop")

# Class/Order as reported in the trimmed invertebrate list (partial coverage)
trim <- read_csv(file.path(art_dir, "Data_S1 - sourceSpeciesLists",
                           "inv_trim_1.csv"), show_col_types = FALSE) |>
  transmute(name = str_squish(reportedName),
            art_class = Class, art_order = Order) |>
  distinct(name, .keep_all = TRUE)

art <- comp |>
  left_join(trim, by = "name") |>
  mutate(key = str_to_lower(name),
         # binomial fallback key for trinomials / names with extra tokens
         key_binom = str_to_lower(word(name, 1, 2)),
         n_words = str_count(name, "\\S+"))

cat(sprintf("Article names: %d (inv + but, deduplicated)\n", nrow(art)))

# --- 2. EPPO Latin names (animals: species GAI + taxonomic groups SIT) -------
con <- dbConnect(SQLite(), db_path)

eppo_names <- dbGetQuery(con, "
  SELECT n.codeid, c.eppocode, c.dtcode, n.fullname, n.preferred
  FROM t_names n
  JOIN t_codes c ON c.codeid = n.codeid
  WHERE c.dtcode IN ('GAI', 'SIT')
    AND c.status = 'A'
    AND n.status = 'A'
    AND n.codelang = 'la'
") |>
  mutate(key = str_to_lower(str_squish(fullname)))

preferred <- eppo_names |>
  filter(preferred == 1) |>
  distinct(codeid, .keep_all = TRUE) |>
  select(codeid, eppo_name = fullname)

# --- 3. Match ----------------------------------------------------------------
# A key can hit several codes (homonyms, or synonym of one + preferred of
# another). Prefer: preferred name > synonym, species (GAI) > group (SIT).
pick_best <- function(df) {
  df |>
    arrange(key, desc(preferred), dtcode != "GAI") |>
    group_by(key) |>
    summarise(codeid = first(codeid), eppocode = first(eppocode),
              dtcode = first(dtcode), matched_name = first(fullname),
              matched_preferred = first(preferred),
              n_candidate_codes = n_distinct(codeid), .groups = "drop")
}
eppo_best <- pick_best(eppo_names)

m_exact <- art |>
  inner_join(eppo_best, by = "key") |>
  mutate(match_type = if_else(matched_preferred == 1, "exact", "synonym"))

m_binom <- art |>
  filter(!name %in% m_exact$name, n_words > 2) |>
  inner_join(eppo_best, by = c("key_binom" = "key")) |>
  mutate(match_type = "binomial_of_trinomial")

matched <- bind_rows(m_exact, m_binom)

# Genus-level hit for names with no species match (useful for tier C later)
genus_codes <- eppo_names |>
  filter(dtcode == "SIT", str_detect(eppocode, "G$"), preferred == 1) |>
  distinct(key, .keep_all = TRUE) |>
  transmute(genus_key = key, genus_codeid = codeid, genus_eppocode = eppocode)

# --- 4. EPPO taxonomy (walk up t_links; rank = last letter of SIT code) ------
art_genus <- art |>
  mutate(genus_key = str_to_lower(word(name, 1))) |>
  inner_join(genus_codes, by = "genus_key")
ids <- unique(c(matched$codeid, art_genus$genus_codeid))
dbWriteTable(con, "tmp_ids", data.frame(codeid = ids), temporary = TRUE,
             overwrite = TRUE)
anc <- dbGetQuery(con, "
  WITH RECURSIVE up(leaf, id, depth) AS (
    SELECT codeid, codeid, 0 FROM tmp_ids
    UNION ALL
    SELECT up.leaf, l.codeid_parent, up.depth + 1
    FROM up JOIN t_links l ON l.codeid = up.id
    WHERE l.status = 'A' AND up.depth < 25
  )
  SELECT up.leaf AS codeid, c.eppocode AS anc_code, n.fullname AS anc_name
  FROM up
  JOIN t_codes c ON c.codeid = up.id
  JOIN t_names n ON n.codeid = up.id AND n.preferred = 1 AND n.codelang = 'la'
  WHERE c.dtcode = 'SIT' AND up.depth > 0
")
dbDisconnect(con)

rank_map <- c(K = "kingdom", P = "phylum", C = "class", O = "order",
              F = "family", G = "genus")
taxo <- anc |>
  mutate(rank = rank_map[str_sub(anc_code, -1)]) |>
  filter(!is.na(rank)) |>
  distinct(codeid, rank, .keep_all = TRUE) |>
  select(codeid, rank, anc_name) |>
  pivot_wider(names_from = rank, values_from = anc_name,
              names_prefix = "eppo_")
eppo_rank_cols <- intersect(paste0("eppo_", rank_map), names(taxo))

# --- 5. Assemble one row per article name ------------------------------------
res <- art |>
  left_join(matched |> select(name, codeid, eppocode, dtcode, matched_name,
                              match_type, n_candidate_codes),
            by = "name") |>
  left_join(preferred, by = "codeid") |>
  mutate(genus_key = str_to_lower(word(name, 1))) |>
  left_join(genus_codes, by = "genus_key") |>
  # taxonomy from the species code, else from the genus code
  mutate(taxo_id = coalesce(codeid, genus_codeid),
         taxo_source = case_when(!is.na(codeid) ~ "species",
                                 !is.na(genus_codeid) ~ "genus")) |>
  left_join(taxo, by = c("taxo_id" = "codeid")) |>
  mutate(
    match_type = coalesce(match_type,
                          if_else(!is.na(genus_eppocode), "genus_only",
                                  "none")),
    is_insect = case_when(eppo_class == "Insecta" ~ TRUE,
                          art_class == "Insecta" ~ TRUE,
                          !is.na(eppo_class) | !is.na(art_class) ~ FALSE,
                          str_detect(art_group, "Lepidoptera") ~ TRUE,
                          TRUE ~ NA)
  ) |>
  select(name, art_group, art_class, art_order, all_of(flag_cols),
         match_type, eppocode, dtcode, eppo_name, matched_name,
         n_candidate_codes, genus_eppocode, taxo_source,
         all_of(eppo_rank_cols), is_insect) |>
  arrange(name)

dir.create(dirname(out_csv), showWarnings = FALSE)
write_csv(res, out_csv, na = "")

# --- 6. Short summary --------------------------------------------------------
cat("\nMatch type, all names:\n")
print(count(res, match_type))
cat("\nInsects only (is_insect TRUE):\n")
print(res |> filter(is_insect %in% TRUE) |> count(match_type))
cat("\nMatched insects by EPPO order:\n")
print(res |> filter(is_insect %in% TRUE, !is.na(eppocode)) |>
        count(eppo_order, sort = TRUE), n = 20)
cat("\nis_insect (FALSE / TRUE / NA):\n")
print(table(res$is_insect, useNA = "always"))
cat("\nTaxonomy source:\n")
print(table(res$taxo_source, useNA = "always"))
cat(sprintf("\nUnknown class (no EPPO match, not in inv_trim): %d\n",
            sum(is.na(res$is_insect))))
cat(sprintf("Ambiguous keys (>1 EPPO code): %d\n",
            sum(res$n_candidate_codes > 1, na.rm = TRUE)))
cat("Written:", out_csv, "\n")
