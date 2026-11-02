"""Unit tests for the scraper's matching, classification and extraction."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from insect_scraper import classify, extract  # noqa: E402
from insect_scraper.crawl import _clean_life_text, _rows  # noqa: E402
from insect_scraper.names import NameIndex, norm  # noqa: E402


def idx():
    return NameIndex(
        scientific={"spodoptera frugiperda": "Spodoptera frugiperda",
                    "solenopsis invicta": "Solenopsis invicta",
                    "zophobas atratus": "Zophobas atratus"},
        common={"superworm": "Zophobas atratus", "fall armyworm": "Spodoptera frugiperda",
                "apple snail": "Pomacea spp.", "tropical white": "Glutophrissa drusilla"},
        regulated={"Spodoptera frugiperda", "Solenopsis invicta", "Pomacea spp."},
        genera={"Spodoptera", "Solenopsis", "Zophobas", "Collembola", "Glutophrissa", "Lasius"},
        scope_genera={"Spodoptera", "Solenopsis", "Zophobas", "Lasius"})


def test_norm_strips_accents_and_nordic_letters():
    assert norm("Levende maurdronning – Løpebille Ærfugl") == "levende maurdronning lopebille aerfugl"
    assert norm("solenopsis-invicta_colony%20") == "solenopsis invicta colony"


def test_find_scientific_from_slug_and_plural_common():
    i = idx()
    assert ("Solenopsis invicta", "scientific name") in i.find("/products/solenopsis-invicta-colony")
    assert ("Zophobas atratus", "common name") in i.find("Live Superworms 100 count")
    assert ("Pomacea spp.", "common name") in i.find("Golden apple snails for aquarium")


def test_scientific_beats_common_for_same_species():
    hits = idx().find("Zophobas atratus superworms")
    assert hits == [("Zophobas atratus", "scientific name")]


def test_common_name_dropped_when_title_names_other_genus():
    p = extract.Product(url="https://x.com/products/collembola-tropical-white",
                        title="Collembola sp. Tropical White Springtails")
    rows, _ = _rows(p, idx(), "x.com", "shopify")
    assert rows == []


def test_rows_regulated_and_live():
    p = extract.Product(url="https://x.com/products/fire-ant-colony",
                        title="Solenopsis invicta colony with queen", price="25")
    rows, _ = _rows(p, idx(), "x.com", "shopify")
    assert rows[0]["regulated"] and rows[0]["life_state"] == "live"
    assert rows[0]["page_type"] == "product"


def test_dead_on_arrival_counts_as_live():
    state, _ = classify.life_state(_clean_life_text("Dubia roaches. Dead on arrival guarantee"))
    assert state == "live"


def test_ships_to_norway_negation_and_positive():
    assert classify.ships_to("We do not ship live animals to Norway or Switzerland.")["ships_to_norway"] == "no"
    assert classify.ships_to("Wir versenden nicht nach Norwegen.")["ships_to_norway"] == "no"
    assert classify.ships_to("Vi sender til hele Norge med Posten.")["ships_to_norway"].startswith("yes")
    assert classify.ships_to("We ship only within the EU countries.")["ships_to_norway"] == "no (EU only)"
    assert classify.ships_to("Worldwide shipping available")["ship_scope"] == "worldwide"
    assert classify.ships_to("We only ship within the United States, lower 48")["ship_scope"] == "domestic only"


def test_seller_country():
    assert classify.seller_country("shop.de")[0] == "DE"
    assert classify.seller_country("insects.com", "NOK")[0] == "NO"
    assert classify.seller_country("x.com", "", "Musterstr. 1, 12345 Berlin, Germany")[0] == "DE"


def test_page_type():
    assert classify.page_type("https://x.com/blogs/care/mantis-care-sheet", "Mantis care", True) == "info"
    assert classify.page_type("https://x.com/products/silkworm-food", "Silkworm Food", True) == "accessory"
    assert classify.page_type("https://x.com/collections/feeders", "Feeders", False) == "category"


def test_extract_shopify_and_woocommerce():
    s = extract.from_shopify({"title": "Live Hornworms", "handle": "hornworms",
                              "body_html": "<p>Manduca sexta</p>", "product_type": "Feeders",
                              "tags": ["live"], "variants": [{"price": "9.99", "available": True},
                                                              {"price": "4.99", "available": False}]},
                             "https://x.com", "USD")
    assert s.url == "https://x.com/products/hornworms" and s.price == "4.99" and s.available == "in stock"
    w = extract.from_woocommerce({"name": "Lasius niger &amp; queen", "permalink": "https://y.de/p/lasius",
                                  "prices": {"price": "1999", "currency_code": "EUR",
                                             "currency_minor_unit": 2},
                                  "is_in_stock": True, "categories": [{"name": "Ameisen"}]})
    assert w.title == "Lasius niger & queen" and w.price == "19.99" and w.currency == "EUR"


def test_extract_html_jsonld():
    page = """<html><head><title>x</title><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Product","name":"Pomacea diffusa (live)",
     "description":"Mystery snail","offers":{"@type":"Offer","price":"3.50","priceCurrency":"EUR",
     "availability":"https://schema.org/InStock"}}</script></head><body><h1>x</h1></body></html>"""
    p = extract.from_html("https://z.nl/pomacea", page)
    assert p.is_product and p.title == "Pomacea diffusa (live)" and p.price == "3.50"
    assert p.currency == "EUR" and p.available == "in stock"


def test_new_latin_names_only_scope_genera_and_real_epithets():
    p = extract.Product(url="https://x.com/products/lasius", title="Lasius niger colony, Lasius larvae",
                        description="Collembola sp. and Lasius flavus")
    _, new = _rows(p, idx(), "x.com", "shopify")
    names = {n["name"] for n in new}
    assert names == {"Lasius niger", "Lasius flavus"}


def test_one_word_common_name_rule():
    from insect_scraper.names import ONE_WORD_OK
    assert ONE_WORD_OK.search("superworm") and ONE_WORD_OK.search("waxworm")
    assert not ONE_WORD_OK.search("soldier") and not ONE_WORD_OK.search("leopard")
