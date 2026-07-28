library(DBI)
library(RSQLite)
library(dplyr)
library(ggplot2)
library(stringr)
library(scales)

# dtcode reference:
#   GAI  = individual animal species
#   SIT  = animal taxonomic group (order / family / genus nodes)
#   GAF  = microorganism individual
#   PFL  = plant individual
#   SPT / SFT = plant / microorganism taxonomic group
# Only status = 'A' (active) records are used throughout.

DB_PATH <- r"(C:\Users\dafl\OneDrive - Folkehelseinstituttet\insect_trade\eppo db\eppocodes.sqlite)"
con <- dbConnect(RSQLite::SQLite(), DB_PATH)

# --- 1. All active animal species (GAI) --------------------------------------

species_df <- dbGetQuery(con, "
  SELECT DISTINCT c.codeid, n.fullname AS species
  FROM t_codes c
  JOIN t_names n ON c.codeid = n.codeid
  WHERE c.dtcode    = 'GAI'
    AND c.status    = 'A'
    AND n.codelang  = 'la'
    AND n.preferred = 1
    AND n.status    = 'A'
  ORDER BY n.fullname
")

cat(sprintf("Active GAI animal species in EPPO: %d\n", nrow(species_df)))

# --- 2. Walk up hierarchy to find SIT (taxonomic group) ancestors ------------
# For each species, traverse t_links upward up to depth 12.
# Shallowest SIT ancestor -> order-level group
# Deepest   SIT ancestor  -> family-level group

ancestor_sql <- "
WITH RECURSIVE up(leaf_id, anc_id, depth) AS (
  SELECT l.codeid        AS leaf_id,
         l.codeid_parent AS anc_id,
         1               AS depth
  FROM t_links l
  JOIN t_codes c ON l.codeid = c.codeid
  WHERE c.dtcode = 'GAI'
    AND c.status = 'A'
    AND l.status = 'A'

  UNION ALL

  SELECT u.leaf_id,
         l.codeid_parent,
         u.depth + 1
  FROM up u
  JOIN t_links l ON l.codeid = u.anc_id
  WHERE l.status = 'A'
    AND u.depth  < 12
)
SELECT u.leaf_id AS codeid,
       u.anc_id,
       u.depth,
       n.fullname AS group_name
FROM up u
JOIN t_codes c ON u.anc_id = c.codeid
JOIN t_names n ON c.codeid  = n.codeid
WHERE c.dtcode    = 'SIT'
  AND c.status    = 'A'
  AND n.codelang  = 'la'
  AND n.preferred = 1
  AND n.status    = 'A'
"

cat("Fetching ancestor groups (may take ~30 s)...\n")
ancestors <- dbGetQuery(con, ancestor_sql)
dbDisconnect(con)

order_lookup <- ancestors |>
  group_by(codeid) |>
  slice_min(depth, n = 1, with_ties = FALSE) |>
  ungroup() |>
  select(codeid, order_name = group_name)

family_lookup <- ancestors |>
  group_by(codeid) |>
  slice_max(depth, n = 1, with_ties = FALSE) |>
  ungroup() |>
  select(codeid, family_name = group_name)

# --- 3. Build data frame -----------------------------------------------------

df <- species_df |>
  filter(str_count(species, "\\S+") >= 2) |>
  filter(!str_ends(species, fixed(" sp.")),
         !str_ends(species, fixed(" spp."))) |>
  left_join(order_lookup,  by = "codeid") |>
  left_join(family_lookup, by = "codeid") |>
  mutate(genus = word(species, 1))

cat(sprintf("After cleaning: %d species | %d genera | %d order groups\n",
            nrow(df), n_distinct(df$genus),
            n_distinct(df$order_name, na.rm = TRUE)))

# --- Plot 1: top 25 order-level groups by species count ----------------------

order_counts <- df |>
  filter(!is.na(order_name)) |>
  count(order_name, name = "n_species") |>
  slice_max(n_species, n = 25) |>
  arrange(desc(n_species))

p1 <- ggplot(order_counts, aes(x = reorder(order_name, n_species), y = n_species)) +
  geom_col(fill = "#2C6E9E", width = 0.75) +
  geom_text(aes(label = comma(n_species)), hjust = -0.1, size = 3.0) +
  coord_flip() +
  scale_y_continuous(labels = comma, expand = expansion(mult = c(0, 0.15))) +
  labs(title    = "Top 25 EPPO animal groups by species count",
       subtitle = sprintf("All active GAI entries - %s species total", comma(nrow(df))),
       x = NULL, y = "Species") +
  theme_minimal(base_size = 12) +
  theme(panel.grid.major.y = element_blank())

print(p1)
ggsave("eppo_orders.png", p1, width = 9, height = 7, dpi = 150)
cat("Saved: eppo_orders.png\n")

# --- Plot 2: top 40 genera coloured by order ---------------------------------

genus_counts <- df |>
  filter(!is.na(order_name)) |>
  count(genus, order_name, name = "n_species") |>
  slice_max(n_species, n = 40) |>
  arrange(desc(n_species))

p2 <- ggplot(genus_counts,
             aes(x = reorder(genus, n_species), y = n_species, fill = order_name)) +
  geom_col(width = 0.75) +
  geom_text(aes(label = n_species), hjust = -0.15, size = 2.8) +
  coord_flip() +
  scale_y_continuous(expand = expansion(mult = c(0, 0.12))) +
  scale_fill_viridis_d(option = "turbo") +
  labs(title = "Top 40 genera by species count (EPPO animals)",
       x = NULL, y = "Species", fill = "Order group") +
  theme_minimal(base_size = 11) +
  theme(panel.grid.major.y = element_blank(),
        legend.position    = "bottom",
        legend.text        = element_text(size = 8))

print(p2)
ggsave("eppo_genera_top40.png", p2, width = 9, height = 11, dpi = 150)
cat("Saved: eppo_genera_top40.png\n")

# --- Plot 3: histogram of genus sizes ----------------------------------------

genus_size <- df |> count(genus, name = "n_species")

p3 <- ggplot(genus_size, aes(x = n_species)) +
  geom_histogram(binwidth = 1, fill = "#2C6E9E", color = "white", linewidth = 0.2) +
  scale_x_continuous(breaks = c(1, 5, 10, 25, 50, 100, 200), limits = c(0, NA)) +
  scale_y_continuous(labels = comma) +
  labs(title    = "Distribution of genus sizes (EPPO active animal species)",
       subtitle = sprintf("%s genera total", comma(n_distinct(genus_size$genus))),
       x = "Species per genus", y = "Number of genera") +
  theme_minimal(base_size = 12)

print(p3)
ggsave("eppo_genus_size_distribution.png", p3, width = 8, height = 5, dpi = 150)
cat("Saved: eppo_genus_size_distribution.png\n")

# --- Summary tables ----------------------------------------------------------

cat("\n--- Top 25 order-level groups ---\n")
print(order_counts, n = Inf)

cat("\n--- Top 20 genera ---\n")
print(head(arrange(genus_counts, desc(n_species)), 20))
