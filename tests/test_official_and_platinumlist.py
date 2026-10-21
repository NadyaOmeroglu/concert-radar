import pytest

from collector.models import Artist, SourceNote, SourceUnavailable
from collector.net import RobotsUnreachable
from collector.sources import official_site, platinumlist
from tests.conftest import FakeHttp, FakeResponse, fixture_text

TOUR = "https://example.org/tour"


def official_artist(**kw):
    return Artist(name="Coldplay", aliases=["Coldplay"], official_tour_url=TOUR, **kw)


def test_official_jsonld(regions):
    http = FakeHttp({TOUR: FakeResponse(200, fixture_text("official_jsonld.html"))})
    events = official_site.fetch(official_artist(), regions, http=http)
    by_city = {e.city: e for e in events}
    # other performer's show is skipped; event without address is kept but has no city
    assert set(by_city) == {"München", "Yas Island", None}
    munich = by_city["München"]
    assert (munich.date, munich.time, munich.country) == ("2027-06-01", "19:30", "DE")
    assert munich.url == "https://example.org/tickets/munich"
    assert (munich.status, munich.price_min, munich.currency) == ("onsale", 89.0, "EUR")
    yas = by_city["Yas Island"]
    assert yas.country == "AE" and yas.status == "postponed" and yas.url == TOUR


def test_official_widget_is_only_detected(regions):
    http = FakeHttp({TOUR: FakeResponse(200, fixture_text("official_widget.html"))})
    with pytest.raises(SourceNote, match="Bandsintown"):
        official_site.fetch(official_artist(), regions, http=http)


def test_official_custom_selectors(regions):
    sel = {"item": "li.show", "date": ".d", "date_format": "%d %b %Y", "city": ".c", "venue": ".v",
           "link": "a.t", "country_code": "ES"}
    http = FakeHttp({TOUR: FakeResponse(200, fixture_text("official_selectors.html"))})
    events = official_site.fetch(official_artist(selectors=sel), regions, http=http)
    assert events[0].date == "2027-06-01" and events[0].url == "https://example.org/tickets/madrid"
    assert events[1].date is None  # "TBA" is not turned into a guessed date


def test_official_respects_robots(regions):
    with pytest.raises(SourceNote, match="robots.txt"):
        official_site.fetch(official_artist(), regions, http=FakeHttp(robots_allowed=False))


def test_official_without_url_does_nothing(regions, coldplay):
    assert official_site.fetch(coldplay, regions, http=FakeHttp()) == []


LISTINGS = [{"url": "https://dubai.pl.test/concert", "city": "Dubai", "country": "AE"},
            {"url": "https://muscat.pl.test/concert", "city": "Muscat", "country": "OM"}]


@pytest.fixture(autouse=True)
def fresh_platinumlist_cache():
    platinumlist.reset_cache()
    yield
    platinumlist.reset_cache()


def listing_http(page1, page2=None, muscat=None):
    def dubai(url, params):
        if url.endswith("page=2"):
            return FakeResponse(200, page2 or "<html></html>")
        return FakeResponse(200, page1)
    return FakeHttp({"dubai.pl.test": dubai,
                     "muscat.pl.test": muscat or FakeResponse(404, "")})


def test_platinumlist_reads_city_listings_once_for_all_artists(coldplay, regions):
    http = listing_http(fixture_text("platinumlist_search.html"))
    events = platinumlist.fetch(coldplay, regions, http=http, listings=LISTINGS)
    assert [e.city for e in events] == ["Dubai", "Sharjah"]  # tribute + safari skipped; region filter is later
    dubai = events[0]
    assert dubai.url == "https://dubai.platinumlist.net/event-tickets/test-coldplay"
    assert (dubai.price_min, dubai.currency, dubai.status) == (350.0, "AED", "onsale")
    calls = len(http.calls)
    assert platinumlist.fetch(Artist(name="Adele", aliases=["Adele"]), regions, http=http, listings=LISTINGS) == []
    assert len(http.calls) == calls  # second artist uses the pages already read


def test_platinumlist_paging_and_city_fallback(coldplay, regions):
    page2 = ('<script type="application/ld+json">{"@type": "MusicEvent", "name": "Coldplay - Encore night", '
             '"startDate": "2027-02-01T20:00", "url": "https://dubai.platinumlist.net/event-tickets/x2"}</script>')
    http = listing_http(fixture_text("platinumlist_search.html"), page2)
    events = platinumlist.fetch(coldplay, regions, http=http, listings=LISTINGS)
    encore = [e for e in events if e.date == "2027-02-01"][0]
    assert (encore.city, encore.country) == ("Dubai", "AE")  # no address in JSON-LD -> the listing's city
    urls = [u for u, _ in http.calls]
    assert "https://dubai.pl.test/concert?page=2" in urls and "https://dubai.pl.test/concert?page=3" in urls


def test_platinumlist_without_structured_data_is_a_note(coldplay, regions):
    http = listing_http("<html><body>no json-ld</body></html>")
    with pytest.raises(SourceNote, match="no structured"):
        platinumlist.fetch(coldplay, regions, http=http, listings=LISTINGS)


@pytest.mark.parametrize("http", [
    FakeHttp({"pl.test": FakeResponse(403, "<html>Just a moment...</html>")}),
    FakeHttp(robots_allowed=False),
    FakeHttp(robots_allowed=RobotsUnreachable("could not read robots.txt")),
])
def test_platinumlist_blocked_is_unavailable_not_bypassed(http, coldplay, regions):
    with pytest.raises(SourceUnavailable):
        platinumlist.fetch(coldplay, regions, http=http, listings=LISTINGS)
    assert len(http.calls) <= 1  # no retries with other tricks


def test_platinumlist_artist_page(regions):
    artist = Artist(name="Coldplay", aliases=["Coldplay"], platinumlist_url="https://pl.test/artist/coldplay")
    http = FakeHttp({"artist/coldplay": FakeResponse(200, fixture_text("platinumlist_search.html"))})
    assert [e.city for e in platinumlist.fetch(artist, regions, http=http)] == ["Dubai", "Sharjah"]


LISTING_LINKS = """<html><body>
<a href="/event-tickets/101/coldplay-live-in-dubai">Coldplay Live in Dubai</a>
<a href="https://dubai.platinumlist.net/event-tickets/102/desert-safari?ref=x">Desert Safari</a>
<a href="/event-tickets/103/a-tribute-to-coldplay">Candlelight: A Tribute to Coldplay</a>
<a href="/about">About us</a></body></html>"""
EVENT_PAGE = """<script type="application/ld+json">{"@type": "MusicEvent", "name": "Coldplay Live in Dubai",
 "startDate": "2027-01-15T20:00:00+04:00",
 "location": {"@type": "Place", "name": "Coca-Cola Arena", "address": {"addressLocality": "Dubai", "addressCountry": "AE"}}}
</script>"""


def test_platinumlist_follows_only_matching_event_links(coldplay, regions):
    http = listing_http(LISTING_LINKS)
    http.routes = {"/event-tickets/101/": FakeResponse(200, EVENT_PAGE),
                   "/event-tickets/103/": FakeResponse(200, EVENT_PAGE.replace("Coldplay Live", "Candlelight: A Tribute to Coldplay")),
                   **http.routes}
    events = platinumlist.fetch(coldplay, regions, http=http, listings=LISTINGS)
    assert [(e.city, e.venue, e.date) for e in events] == [("Dubai", "Coca-Cola Arena", "2027-01-15")]
    assert events[0].url == "https://dubai.pl.test/event-tickets/101/coldplay-live-in-dubai"
    opened = [u for u, _ in http.calls if "/event-tickets/" in u]
    assert opened == ["https://dubai.pl.test/event-tickets/101/coldplay-live-in-dubai"]  # no safari, no tribute
    # another artist with no matching link: no extra requests, no events
    before = len(http.calls)
    assert platinumlist.fetch(Artist(name="Adele", aliases=["Adele"]), regions, http=http, listings=LISTINGS) == []
    assert len(http.calls) == before


def test_platinumlist_event_page_without_data_is_a_note(coldplay, regions):
    http = listing_http(LISTING_LINKS)
    http.routes = {"/event-tickets/": FakeResponse(200, "<html>no data</html>"), **http.routes}
    with pytest.raises(SourceNote, match="event page has no structured"):
        platinumlist.fetch(coldplay, regions, http=http, listings=LISTINGS)
