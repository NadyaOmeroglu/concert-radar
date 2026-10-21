import pytest

from collector.config import load_regions, save_ticketmaster_ids, load_artists
from collector.models import RawEvent
from collector.normalize import city_key, country_code, event_id, merge_events, region_city, slug, to_event
from tests.conftest import ROOT

SCHENGEN = ["AT", "BE", "BG", "HR", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IS", "IT", "LV", "LI",
            "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH"]


def raw(**kw):
    base = dict(source="ticketmaster", artist="Coldplay", url="https://t/1", date="2027-06-01",
                city="Paris", country="FR")
    return RawEvent(**(base | kw))


def test_slugs():
    assert slug("P!nk") == slug("Pink") == "pink"
    assert slug("ROSÉ") == "rose" and slug("Beyoncé") == "beyonce"
    assert city_key("München") == city_key("Munich") == "munich"
    assert city_key("Wien") == city_key("Vienna")


def test_regions_file_has_all_schengen_countries_and_norway_is_not_lost(regions):
    assert set(SCHENGEN) <= regions.countries
    assert len(SCHENGEN) == 29
    assert {"SA", "OM"} <= regions.countries


@pytest.mark.parametrize("country,city,shown", [
    ("DE", "Berlin", "Berlin"), ("NO", "Oslo", "Oslo"), ("CH", "Zürich", "Zürich"),
    ("SA", "Riyadh", "Riyadh"), ("OM", "Muscat", "Muscat"),
    ("AE", "Dubai", "Dubai"), ("AE", "Abu Dhabi", "Abu Dhabi"), ("AE", "Yas Island", "Abu Dhabi"),
    ("AE", "Sharjah", None), ("GB", "London", None), ("IE", "Dublin", None), ("CY", "Limassol", None),
    ("US", "New York", None), ("TR", "Istanbul", None), (None, "Paris", None), ("FR", None, None),
])
def test_region_filter(regions, country, city, shown):
    assert region_city(regions, country, city) == shown


def test_country_codes():
    assert country_code("Germany") == "DE" and country_code("de") == "DE"
    assert country_code(False) == "NO"  # YAML's bare NO
    assert country_code("United Arab Emirates") == "AE"
    assert country_code("Atlantis") is None


@pytest.mark.parametrize("kw", [dict(url=None), dict(date=None), dict(date="2027"), dict(city=None), dict(country=None)])
def test_events_without_required_facts_are_dropped(regions, kw):
    assert to_event(raw(**kw), regions) is None


def test_dedup_merges_sources_with_priority(regions):
    tm = raw(city="Munich", country="DE", venue="Olympic Stadium", time="20:00", status="onsale",
             source_id="ticketmaster:1", price_min=80.0, currency="EUR")
    off = raw(source="official", url="https://official/tour", city="München", country="DE",
              venue="Olympiastadion", status="announced")
    pl = raw(source="platinumlist", url="https://pl/1", city="Munich", country="DE")
    other_day = raw(date="2027-06-02", city="Munich", country="DE")
    merged = merge_events([to_event(r, regions) for r in (tm, off, pl, other_day)])

    assert len(merged) == 2
    e = merged[0]
    assert e["id"] == event_id("Coldplay", "2027-06-01", "Munich")
    assert e["sources"] == ["official", "ticketmaster", "platinumlist"]
    assert [t["url"] for t in e["ticket_urls"]] == ["https://official/tour", "https://t/1", "https://pl/1"]
    assert e["venue"] == "Olympiastadion"  # official wins
    assert e["time"] == "20:00" and e["price_min"] == 80.0  # gaps filled from lower priority
    assert e["status"] == "onsale"  # concrete status beats "announced"


def test_save_ticketmaster_ids_keeps_comments(tmp_path):
    path = tmp_path / "artists.yaml"
    path.write_text((ROOT / "config" / "artists.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    before = {a.name: a.ticketmaster_id for a in load_artists(path)}
    save_ticketmaster_ids(path, {"P!nk": "K8vZTEST"})
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# ")
    after = {a.name: a.ticketmaster_id for a in load_artists(path)}
    assert after.pop("P!nk") == "K8vZTEST"
    before.pop("P!nk")
    assert after == before  # nobody else touched
    assert len(after) == 39


def test_main_sale_page_comes_before_vip_packages(regions):
    box = raw(url="https://t/box", title="Muse: Box seat in the Ticketmaster Suite", city="Berlin", country="DE")
    main_sale = raw(url="https://t/main", title="Muse - The Wow! Signal Tour", city="Berlin", country="DE")
    e = merge_events([to_event(box, regions), to_event(main_sale, regions)])[0]
    assert e["title"] == "Muse - The Wow! Signal Tour"
    assert [t["url"] for t in e["ticket_urls"]] == ["https://t/main", "https://t/box"]
