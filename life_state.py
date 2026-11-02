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
TABLES_DIR  = PROJECT_DIR / "5_outputs" / "tables"      # results: xlsx only
DATA_DIR    = PROJECT_DIR / "2_processed_data"          # working csv files
EPPO_DB     = PROJECT_DIR / "1_raw_data" / "eppo db" / "eppocodes.sqlite"
TABLES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Keyword patterns live in one shared file, also read by the R scripts:
# 3_metadata/life_state_terms.csv (columns: state = dead|live, pattern = regex).
# Dead terms win over live ones inside the same phrase ("ex pupa" is a
# reared-then-mounted adult).
TERMS_FILE = PROJECT_DIR / "3_metadata" / "life_state_terms.csv"
with open(TERMS_FILE, newline="", encoding="utf-8") as _f:
    _rows = list(csv.DictReader(_f))
_DEAD = [r["pattern"] for r in _rows if r["state"] == "dead"]
_LIVE = [r["pattern"] for r in _rows if r["state"] == "live"]
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
