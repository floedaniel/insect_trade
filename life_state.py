"""
life_state.py
=============
Shared helpers for the shop/listing scripts.

classify_life_state(text) labels a listing (title + snippet) as
    "live"    live animals or live stages (eggs, larvae, pupae, colonies ...)
    "dead"    pinned, papered, mounted, framed, dried, ex-pupa/ex-larva specimens,
              jewellery, figures ...
    "check"   both live and dead terms found, needs a look
    "unknown" no terms found
Only live material matters for plant-pest risk. Dead listings are labelled,
not dropped, so they can be counted and filtered later.

Also: project paths and a CSV helper that upgrades old headers.
"""

import csv
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
TABLES_DIR  = PROJECT_DIR / "5_outputs" / "tables"
EPPO_DB     = PROJECT_DIR / "1_raw_data" / "eppo db" / "eppocodes.sqlite"
TABLES_DIR.mkdir(parents=True, exist_ok=True)

# Dead first: "ex pupa" / "ex larva" are reared-then-mounted adults.
_DEAD = [
    r"ex[\s\.\-]*(pupa|pupae|larva|larvae|ovo|ova)",
    r"pinned", r"papered", r"in papers?", r"unmounted", r"mounted", r"spread",
    r"set specimen", r"framed?", r"riker", r"shadow ?box", r"display case",
    r"specimens?", r"taxiderm\w*", r"preserved", r"dried", r"dry\b",
    r"resin", r"lucite", r"acrylic", r"embedded",
    r"\bA1\b", r"\bA-1\b", r"\bA2\b",
    r"pendant", r"keychain", r"key ring", r"earrings?", r"necklace", r"brooch",
    r"figure", r"gashapon", r"toy", r"sticker", r"print", r"poster",
    r"wings? only", r"dead",
    # NO / SV / DA
    r"tørket", r"torkad", r"preparert", r"preparerad", r"innrammet",
    r"inramad", r"nålet", r"nålad", r"død",
    # DE / NL
    r"präpariert", r"praepariert", r"genadelt", r"gespannt", r"ungespannt",
    r"gerahmt", r"getrocknet", r"opgezet", r"ingelijst", r"gedroogd",
    # FR / ES / PL
    r"naturalisé", r"encadré", r"séché", r"enmarcado", r"disecado",
    r"seco\b", r"preparat\w*", r"suszon\w*", r"w ramce",
]
_LIVE = [
    r"live", r"alive", r"living", r"culture", r"starter",
    r"eggs?", r"ova\b", r"ootheca\w*", r"larva[e]?", r"larvae", r"grubs?",
    r"caterpillars?", r"pupa[e]?", r"chrysalis\w*", r"cocoons?", r"nymphs?",
    r"\bL[1-5]\b", r"colon(y|ies)", r"queens?", r"brood", r"feeders?",
    r"breeding", r"captive bred", r"\bCB\b",
    # NO / SV / DA
    r"levende", r"levande", r"egg\b", r"ägg", r"æg", r"larver?", r"larv\b",
    r"pupper?", r"puppor", r"kokong\w*", r"nymfer?", r"koloni\w*",
    r"dronning\w*", r"drottning\w*", r"avl", r"avel",
    # DE / NL
    r"lebend\w*", r"eier", r"larven", r"puppen", r"raupen?", r"kolonie\w*",
    r"königin\w*", r"nachzucht\w*", r"levend\w*", r"eieren", r"rupsen",
    # FR / ES / PL
    r"vivant\w*", r"oeufs?", r"œufs?", r"larves?", r"chrysalides?", r"colonie",
    r"reine", r"vivos?", r"vivas?", r"huevos?", r"larvas?", r"pupas?",
    r"colonia", r"reina", r"żyw\w*", r"jaja", r"jajka", r"larwy",
    r"poczwark\w*", r"kolonia", r"królow\w*",
]
_DEAD_RE = [re.compile(rf"(?<!\w){p}", re.IGNORECASE) for p in _DEAD]
_LIVE_RE = [re.compile(rf"(?<!\w){p}", re.IGNORECASE) for p in _LIVE]


def classify_life_state(*texts) -> tuple[str, str]:
    """Return (state, matched_terms) for the joined texts."""
    text = " ".join(t for t in texts if t)
    dead = sorted({m.group(0).lower() for r in _DEAD_RE for m in [r.search(text)] if m})
    # remove live hits that are only part of a dead phrase (e.g. "ex pupa")
    scrubbed = text
    for r in _DEAD_RE:
        scrubbed = r.sub(" ", scrubbed)
    live = sorted({m.group(0).lower() for r in _LIVE_RE for m in [r.search(scrubbed)] if m})
    if dead and live:
        state = "check"
    elif dead:
        state = "dead"
    elif live:
        state = "live"
    else:
        state = "unknown"
    terms = "; ".join([f"dead:{t}" for t in dead] + [f"live:{t}" for t in live])
    return state, terms


def append_csv(path: Path, rows: list, fields: list):
    """Append rows; if the file has an older header, rewrite it with the new fields."""
    path = Path(path)
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            header = next(csv.reader(f), [])
        if header != fields:
            with open(path, newline="", encoding="utf-8-sig") as f:
                old = list(csv.DictReader(f))
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(old)
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    tests = [
        "Papilio machaon A1 pair, papered, unmounted",
        "Aglais io ABERRATION ex pupa Italy (unmounted)",
        "Lasius niger colony with queen and brood",
        "Levende larver av Pachnoda marginata, 10 stk",
        "Lebende Raupen Saturnia pyri L2",
        "Morpho menelaus framed real butterfly",
        "Phyllium giganteum eggs (ova), captive bred",
        "Dynastes hercules male 150mm",
        "Live pupae, not pinned",
    ]
    for t in tests:
        print(f"{classify_life_state(t)[0]:8s} | {t}")
