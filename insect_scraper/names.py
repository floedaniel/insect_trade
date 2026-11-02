"""Name dictionary: scientific names, synonyms, EPPO names and GBIF common names.

Built from the R pipeline outputs (2_processed_data/species_scope_state.csv and
species_accepted_state.csv). All in-scope plant-pest species are searched,
including those currently labelled dead: a live listing upgrades them.

Matching works on normalised word n-grams:
  - scientific names: exact "genus species" (also from URL slugs)
  - common names (GBIF vernacular names, 11 languages): 1-4 words, plurals
    tried as singular. A common name that points to several species of ONE
    genus becomes a genus target ("Pomacea spp."); names spread over
    several genera, and generic words ("beetle", "mosquito"), are dropped.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

import httpx
import pandas as pd

from . import config

LANGS = {"eng", "nor", "nob", "nno", "deu", "swe", "dan", "nld", "fra", "spa", "pol"}
GENERIC = {
    "ant", "beetle", "snail", "cricket", "butterfly", "moth", "worm", "roach",
    "cockroach", "mantis", "fly", "bee", "wasp", "bug", "grasshopper", "locust",
    "slug", "mite", "caterpillar", "stick insect", "leaf insect", "weevil",
    "termite", "scarab", "isopod", "mealworm beetle", "darkling beetle",
    "rhinoceros beetle", "stag beetle", "praying mantis", "giant snail",
    "land snail", "mosquito", "springtail", "tarantula", "scorpion",
    "centipede", "millipede", "earwig", "aphid", "lacewing", "ladybird",
    "ladybug", "firefly", "cicada", "dragonfly", "hornet", "louse", "flea",
    "tick", "spider", "gnat", "midge", "mantid", "phasmid", "katydid", "sawfly",
    "leafhopper", "planthopper", "thrip", "whitefly", "scale insect",
    "mealybug", "bumblebee", "honeybee", "honey bee", "silkmoth", "hawkmoth",
    "skipper", "tropical white", "fire ant", "fire ants", "red ant", "black ant", "common", "giant", "black", "white", "red",
    "maur", "bille", "snegl", "sommerfugl", "ameise", "kafer", "schnecke",
    "schmetterling", "fourmi", "escargot", "papillon", "hormiga", "caracol",
}

# one-word common names only count if the word itself is clearly an animal
# word ("superworm", "waxworm", "hornworm"); "soldier", "leopard", "sulphur"
# alone gave false hits
ONE_WORD_OK = re.compile(
    r"(worm|wurm|fly|flies|fliege|flue|moth|motte|roach|schabe|beetle|kafer|bille|"
    r"bug|wanze|snail|schnecke|snegl|slug|mite|milbe|hopper|wasp|wespe|veps|"
    r"cricket|grille|sirisse|mantis|larve|larva|larvae|maur|ameise|fourmi|ant|"
    r"bee|biene|bie|weevil|kafer|skalbagge|myra|myre|termite|locust|caterpillar|"
    r"raupe|chafer|borer|miner|sawfly|aphid|laus|lus)$")

_TRANS = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "å": "a",
                        "Å": "a", "ß": "ss", "œ": "oe", "ł": "l", "Ł": "l"})


def norm(text: str | None) -> str:
    """Lowercase, strip accents, keep letters as space-separated words."""
    if not text:
        return ""
    t = str(text).translate(_TRANS)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"%20|[^a-z]+", " ", t)
    return " ".join(t.split())


def _singulars(gram: str) -> set[str]:
    out = {gram}
    m = re.match(r"^(.*[a-z]{3})(es|s)$", gram)
    if m:
        out.add(m.group(1))
        out.add(gram[:-1])
    return out


@dataclass
class NameIndex:
    scientific: dict[str, str] = field(default_factory=dict)  # key -> accepted
    common: dict[str, str] = field(default_factory=dict)      # key -> target
    regulated: set[str] = field(default_factory=set)
    genera: set[str] = field(default_factory=set)        # all known genera
    scope_genera: set[str] = field(default_factory=set)  # genera of in-scope species
    info: pd.DataFrame | None = None                          # per accepted name

    _cues: set[str] | None = None

    def quick(self, text: str | None) -> bool:
        """Cheap pre-check: does the text contain any word that starts a name?"""
        if self._cues is None:
            cues = {k.split()[0] for k in self.scientific}
            for k in self.common:
                cues.update(w for w in k.split() if len(w) >= 5)
                cues.update(s for w in k.split() for s in _singulars(w + "s") if len(s) >= 5)
            self._cues = cues
        words = norm(text).split()
        return any(w in self._cues or w.rstrip("s") in self._cues for w in words)

    def find(self, text: str | None) -> list[tuple[str, str]]:
        """Return [(target, 'scientific name'|'common name'), ...]."""
        words = norm(text).split()
        if not words:
            return []
        grams: set[str] = set()
        for n in (1, 2, 3, 4):
            for i in range(len(words) - n + 1):
                grams.add(" ".join(words[i:i + n]))
        sci = {self.scientific[g] for g in grams if g in self.scientific}
        com: set[str] = set()
        for g in grams:
            for s in _singulars(g):
                if s in self.common:
                    com.add(self.common[s])
        com -= sci
        return ([(s, "scientific name") for s in sorted(sci)]
                + [(c, "common name") for c in sorted(com)])


def _fetch_vernaculars(keys: list[str]) -> None:
    need = [k for k in keys if not (config.VERN_CACHE / f"{k}.json").exists()]
    if not need:
        return
    print(f"Fetching GBIF vernacular names for {len(need)} species keys")
    with httpx.Client(timeout=30, headers={"User-Agent": config.USER_AGENT}) as c:
        for i, k in enumerate(need, 1):
            try:
                r = c.get(f"https://api.gbif.org/v1/species/{k}/vernacularNames",
                          params={"limit": 300})
                if r.status_code == 200:
                    (config.VERN_CACHE / f"{k}.json").write_text(r.text, encoding="utf-8")
            except httpx.HTTPError:
                pass
            if i % 250 == 0:
                print(f"  {i}/{len(need)}")


def build_index(include_dead: bool = True, fetch_missing: bool = True) -> NameIndex:
    sp = pd.read_csv(config.DATA_DIR / "species_scope_state.csv", dtype=str,
                     low_memory=False)
    acc = pd.read_csv(config.DATA_DIR / "species_accepted_state.csv", dtype=str,
                      low_memory=False)
    acc["regulated"] = acc["regulated"].str.upper().eq("TRUE")
    acc["in_scope"] = acc["in_scope"].str.upper().eq("TRUE")
    cand = acc[acc["in_scope"]]
    if not include_dead:
        cand = cand[cand["life_state"] != "dead"]
    cand_names = set(cand["accepted_name"])

    # scientific aliases -> accepted name
    sp["accepted"] = sp["gbif_accepted"].fillna(sp["name"])
    pairs = (list(zip(sp["name"], sp["accepted"]))
             + list(zip(sp["gbif_accepted"], sp["gbif_accepted"]))
             + list(zip(cand["eppo_name"], cand["accepted_name"]))
             + list(zip(cand["accepted_name"], cand["accepted_name"])))
    sci: dict[str, set[str]] = {}
    for alias, accepted in pairs:
        if accepted not in cand_names or not isinstance(alias, str):
            continue
        key = " ".join(norm(alias).split()[:2])
        if key.count(" ") == 1 and len(key) >= 7:
            sci.setdefault(key, set()).add(accepted)
    scientific = {k: next(iter(v)) for k, v in sci.items() if len(v) == 1}

    # common names from GBIF vernacular names
    keys = cand["gbif_key"].dropna().astype(str).str.replace(r"\.0$", "", regex=True)
    key_to_name = dict(zip(keys, cand.loc[keys.index, "accepted_name"]))
    if fetch_missing:
        _fetch_vernaculars(sorted(key_to_name))
    com: dict[str, set[str]] = {}
    for k, accepted in key_to_name.items():
        f = config.VERN_CACHE / f"{k}.json"
        if not f.exists():
            continue
        for r in json.loads(f.read_text(encoding="utf-8")).get("results", []):
            if r.get("language") not in LANGS:
                continue
            key = norm(r.get("vernacularName"))
            if len(key) < 6 or key.count(" ") > 3 or key in GENERIC or key in scientific:
                continue
            if " " not in key and not ONE_WORD_OK.search(key):
                continue
            com.setdefault(key, set()).add(accepted)
    common: dict[str, str] = {}
    for key, members in com.items():
        genera = {m.split()[0] for m in members}
        if len(genera) != 1:
            continue
        common[key] = next(iter(members)) if len(members) == 1 else f"{genera.pop()} spp."

    regulated = set(cand.loc[cand["regulated"], "accepted_name"])
    for key, target in common.items():
        if target.endswith(" spp."):
            members = com[key]
            if members and all(m in regulated for m in members):
                regulated.add(target)

    genera = {n.split()[0] for n in sp["name"].dropna()} | \
             {n.split()[0] for n in sp["gbif_accepted"].dropna()}
    scope_genera = {n.split()[0] for n in cand_names}
    idx = NameIndex(scientific=scientific, common=common, regulated=regulated,
                    genera=genera, scope_genera=scope_genera,
                    info=cand.set_index("accepted_name")[
                        ["eppocode", "eppo_name", "gbif_key", "pest_group",
                         "life_state", "regulated", "eppo_list", "eu_list", "no_list"]])
    print(f"Name index: {len(scientific)} scientific keys, {len(common)} common names, "
          f"{len(cand_names)} species ({len(regulated)} regulated targets)")
    return idx
