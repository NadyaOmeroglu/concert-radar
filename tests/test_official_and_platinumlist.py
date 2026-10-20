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


def test_platinumlist_parsing_filters_tributes_and_other_events(coldplay, regions):
    http = FakeHttp({"search": FakeResponse(200, fixture_text("platinumlist_search.html"))})
    events = platinumlist.fetch(coldplay, regions, http=http, search_url="https://pl.test/search?q={query}")
    assert [e.city for e in events] == ["Dubai", "Sharjah"]  # region filter happens later
    dubai = events[0]
    assert dubai.url == "https://dubai.platinumlist.net/event-tickets/test-coldplay"
    assert (dubai.price_min, dubai.currency, dubai.status) == (350.0, "AED", "onsale")
    assert http.calls[0][0] == "https://pl.test/search?q=Coldplay"


@pytest.mark.parametrize("http", [
    FakeHttp({"search": FakeResponse(403, "<html>Just a moment...</html>")}),
    FakeHttp(robots_allowed=False),
    FakeHttp(robots_allowed=RobotsUnreachable("could not read robots.txt")),
])
def test_platinumlist_blocked_is_unavailable_not_bypassed(http, coldplay, regions):
    with pytest.raises(SourceUnavailable):
        platinumlist.fetch(coldplay, regions, http=http, search_url="https://pl.test/search?q={query}")
    assert len(http.calls) <= 1  # no retries with other tricks
