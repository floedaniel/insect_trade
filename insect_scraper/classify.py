"""Page type, live/dead, ships-to and seller-country classification."""

from __future__ import annotations

import re
import sys

from . import config

sys.path.insert(0, str(config.PROJECT_DIR))
from life_state import classify_life_state  # noqa: E402  (shared keyword file)

# -- page type -----------------------------------------------------------------
INFO_RX = re.compile(r"/blogs?/|care[- ]?sheet|care[- ]?guide|/guide|/pages/|/faq|archives|"
                     r"/tag/|/news/|/article|/wiki|how[- ]to|/forum", re.I)
ACCESSORY_RX = re.compile(
    r"\b(food|diet|chow|jelly|gel|feeding block|substrate|leaf litter|bedding|cages?|"
    r"enclosure|terrarium|habitat kit|repellent|killer|trap|bait|insecticide|books?|"
    r"poster|sticker|shirt|mug|plush|toy|figure|dish|bowl|heat ?mat|thermometer|net|"
    r"incubator|cup ?lids?|deli ?cups?|gift card|futter|fôr|nourriture|comida|"
    r"pokarm|bodengrund|substrat|"
    # pest-control products (traps, lures, pheromones, products "against" a pest)
    r"pheromones?|ph[ée]romones?|pheromon\w*|feromon\w*|pi[èe]ges?|lures?|falle|fallen|"
    r"felle|f[äa]lla|pułapk\w*|trampa|k[öo]der|appâts?|contre|against|gegen|anti[- ]\w+|"
    r"bek[äa]mpf\w*|repuls\w*|r[ée]pulsif|bouclier|schutz|protection|monitoring|"
    # pesticides, traps, lab products, dead items
    r"insectici\w*|insettici\w*|insektizid\w*|pesticid\w*|plaguicid\w*|fungicid\w*|"
    r"pflanzenschutz\w*|spinosad|deltamethrin|pyrethr\w*|trappol\w*|trampas?|"
    r"pcr|qpcr|assay|primers?|antibod\w*|reagents?|elisa|"
    r"taxiderm\w*|paperweight|keyrings?|keychains?|magnets?|stickers?|"
    r"neem|ppm|control|kontrolle|lutte|controllo|bestrijding|zwalczani\w*|"
    r"milky spore|kairomon\w*|diffuseur|exterminator|val kit|\w*hoornaarval|lokstof|vallen|"
    r"fly fishing|fishing flies)\b", re.I)

# biocontrol products: a pest named in the title is usually the target
BIOCONTROL_RX = re.compile(
    r"nematod\w*|steinernema|heterorhabditis|trichogramma|parasit\w*|predator\w*|"
    r"bacillus|beauveria|metarhizium|biocontrol|beneficial|n[üu]tzling\w*|auxiliaires?", re.I)
CATEGORY_RX = re.compile(r"/collections?/(?!.*/products/)|/categor(y|ies)/|/product-category/|"
                         r"/c/|/kategori|/kategorie", re.I)


def page_type(url: str, title: str, is_product: bool) -> str:
    path = re.sub(r"^https?://[^/]+", "", url or "")
    t = f"{title or ''} {re.sub(r'[-_]', ' ', path)}"
    if INFO_RX.search(path) or re.search(r"care sheet|archives|blog", title or "", re.I):
        return "info"
    if ACCESSORY_RX.search(t):
        return "accessory"
    if not is_product and CATEGORY_RX.search(path):
        return "category"
    return "product" if is_product else "page"


def life_state(*texts: str) -> tuple[str, str]:
    return classify_life_state(*texts)


# -- ships to ------------------------------------------------------------------
NORWAY = r"(norway|norge|noreg|norwegen|norv[eè]ge|noruega|norwegia|norvegia|noorwegen)"
NEG = (r"(not|no|never|cannot|can't|unable|except|excluding|excl\.?|outside|"
       r"nicht|kein|keine|ausgenommen|au(ss|ß)er|ikke|unntatt|inte|undantag|"
       r"pas|sauf|excepto|salvo|nie|opr[oó]cz|niet|behalve|non)")
SHIP = (r"(ship|shipping|deliver|delivery|send|versand|versenden|liefern|lieferung|"
        r"frakt|sender|levering|leverans|skickar|livraison|envoi|env[ií]o|enviamos|"
        r"spedizione|wysy[lł]ka|verzend)")


def ships_to(text: str) -> dict:
    """Classify shipping text. Returns norway (yes/no/unclear), scope, evidence."""
    t = " ".join((text or "").split())
    low = t.lower()
    out = {"ships_to_norway": "unclear", "ship_scope": "unknown",
           "live_shipping_note": "", "ship_evidence": ""}

    neg_no = re.search(rf"{NEG}[^.!?\n]{{0,60}}{NORWAY}|{NORWAY}[^.!?\n]{{0,25}}{NEG}\b", low)
    pos_no = re.search(rf"{NORWAY}", low)
    nonEU = re.search(r"(only|nur|kun|endast|uniquement|solo|tylko|alleen)[^.!?]{0,40}"
                      r"\b(eu|european union|europ[äa]ischen union|eu-l[äa]nder|eu countries)", low)
    if neg_no:
        out["ships_to_norway"] = "no"
        out["ship_evidence"] = t[max(0, neg_no.start() - 40):neg_no.end() + 40]
    elif pos_no:
        out["ships_to_norway"] = "yes (mentioned)"
        out["ship_evidence"] = t[max(0, pos_no.start() - 60):pos_no.end() + 60]
    elif nonEU:
        out["ships_to_norway"] = "no (EU only)"
        out["ship_evidence"] = t[max(0, nonEU.start() - 20):nonEU.end() + 40]

    if re.search(r"worldwide|world-wide|international(ly)? (shipping|delivery)|ship internationally|"
                 r"weltweit|verden over|hele verden|i hela v[äa]rlden|dans le monde entier|"
                 r"a todo el mundo|na ca[lł]y [sś]wiat", low):
        out["ship_scope"] = "worldwide"
    elif re.search(rf"{SHIP}[^.!?]{{0,40}}\b(eu|europe|europa|european union|eu-l[äa]nder|"
                   r"eu countries|europaweit|eu-weit)\b|europaweit|eu-weit", low):
        out["ship_scope"] = "EU/Europe"
    elif re.search(r"(only|exclusively|nur|kun|endast|uniquement|solo|tylko)[^.!?]{0,30}"
                   r"(within|innerhalb|innen|inom|en|dentro|w obr[eę]bie)?[^.!?]{0,20}"
                   r"(the )?(us|usa|united states|uk|united kingdom|germany|deutschland|"
                   r"norge|norway|sverige|sweden|danmark|denmark|france|espa[nñ]a|polska|"
                   r"nederland|österreich|austria|schweiz)|lower 48|contiguous (us|united states)|"
                   r"do not ship (outside|internationally)|no international shipping", low):
        out["ship_scope"] = "domestic only"
    live = re.search(r"(live|lebende?|levende|levande|vivant|vivo|[żz]yw)[^.!?]{0,40}"
                     r"(animals?|tiere?|dyr|djur|insect|insekt)[^.!?]{0,80}", low)
    if live and re.search(SHIP, live.group(0)):
        out["live_shipping_note"] = t[live.start():live.end()][:200]
    return out


# -- seller country -----------------------------------------------------------
TLD = {"no": "NO", "se": "SE", "dk": "DK", "fi": "FI", "is": "IS", "de": "DE", "at": "AT",
       "ch": "CH", "nl": "NL", "be": "BE", "fr": "FR", "es": "ES", "it": "IT", "pl": "PL",
       "cz": "CZ", "sk": "SK", "hu": "HU", "pt": "PT", "ie": "IE", "uk": "GB", "lt": "LT",
       "lv": "LV", "ee": "EE", "ro": "RO", "bg": "BG", "gr": "GR", "si": "SI", "hr": "HR",
       "ca": "CA", "au": "AU", "nz": "NZ", "jp": "JP", "ru": "RU", "ua": "UA", "us": "US",
       "lu": "LU", "za": "ZA", "br": "BR", "mx": "MX", "in": "IN", "cn": "CN", "tw": "TW",
       "my": "MY", "id": "ID", "ph": "PH", "th": "TH", "vn": "VN", "sg": "SG", "hk": "HK"}
CURRENCY = {"NOK": "NO", "SEK": "SE", "DKK": "DK", "PLN": "PL", "CZK": "CZ", "HUF": "HU",
            "GBP": "GB", "CHF": "CH", "USD": "US", "CAD": "CA", "AUD": "AU", "NZD": "NZ",
            "JPY": "JP", "RUB": "RU", "UAH": "UA", "ZAR": "ZA"}
COUNTRY_WORDS = {
    "NO": r"norway|norge|norwegen", "SE": r"sweden|sverige|schweden", "DK": r"denmark|danmark|d[äa]nemark",
    "FI": r"finland|suomi|finnland", "DE": r"germany|deutschland", "AT": r"austria|[öo]sterreich",
    "CH": r"switzerland|schweiz|suisse", "NL": r"netherlands|nederland|niederlande",
    "BE": r"belgium|belgi[eë]|belgique|belgien", "FR": r"france|frankreich", "ES": r"spain|espa[nñ]a|spanien",
    "IT": r"italy|italia|italien", "PL": r"poland|polska|polen", "CZ": r"czech republic|czechia|[čc]esko|tschechien",
    "GB": r"united kingdom|england|scotland|wales", "IE": r"ireland", "PT": r"portugal",
    "US": r"united states|usa\b", "CA": r"canada", "AU": r"australia", "LT": r"lithuania|lietuva",
    "HU": r"hungary|magyarorsz[aá]g", "SK": r"slovakia|slovensko", "SI": r"slovenia|slovenija",
}


def seller_country(domain: str, currency: str = "", imprint_text: str = "") -> tuple[str, str]:
    """Best guess (country, basis). Imprint text beats TLD beats currency."""
    if imprint_text:
        low = imprint_text.lower()
        # country named right after a postcode, as in an address line
        for cc, rx in COUNTRY_WORDS.items():
            if re.search(rf"\b\d{{3,5}}\b[^\n]{{0,40}}\b({rx})\b", low):
                return cc, "imprint address"
    tld = domain.rsplit(".", 1)[-1].lower()
    if domain.lower().endswith(".co.uk"):
        tld = "uk"
    if tld in TLD:
        return TLD[tld], "domain"
    if currency and currency.upper() in CURRENCY:
        return CURRENCY[currency.upper()], "currency"
    if currency and currency.upper() == "EUR":
        return "EU (country unknown)", "currency"
    return "", ""
