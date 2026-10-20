import pytest

from collector.models import AmbiguousArtist, Artist, SourceUnavailable
from collector.sources import ticketmaster
from tests.conftest import FakeHttp, FakeResponse, fixture_text


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "SECRET-TEST-KEY")


def test_exact_match_and_event_mapping(coldplay, regions):
    http = FakeHttp({
        "attractions.json": FakeResponse(200, fixture_text("ticketmaster_attractions_exact.json")),
        "events.json": FakeResponse(200, fixture_text("ticketmaster_events.json")),
    })
    events = ticketmaster.fetch(coldplay, regions, http=http)

    assert coldplay.ticketmaster_id == "K8vZ9171ob7"  # tribute band with a longer name is ignored
    assert [e.source_id for e in events] == ["ticketmaster:TM-MUNICH", "ticketmaster:TM-LONDON", "ticketmaster:TM-OSLO"]
    munich = events[0]
    assert (munich.date, munich.time, munich.city, munich.country) == ("2027-06-01", "20:00", "Munich", "DE")
    assert munich.url == "https://www.ticketmaster.de/event/test-munich"
    assert munich.status == "onsale" and munich.onsale_date == "2026-10-24T08:00:00Z"
    assert (munich.price_min, munich.currency) == (79.5, "EUR")
    oslo = events[2]
    assert oslo.time is None and oslo.status == "cancelled"
    # the key is sent to the API but only as a query parameter
    assert all(params.get("apikey") == "SECRET-TEST-KEY" for _, params in http.calls)


def test_known_id_skips_lookup(regions):
    artist = Artist(name="Coldplay", aliases=["Coldplay"], ticketmaster_id="K8vZ9171ob7")
    http = FakeHttp({"events.json": FakeResponse(200, fixture_text("ticketmaster_events.json"))})
    ticketmaster.fetch(artist, regions, http=http)
    assert not any("attractions" in url for url, _ in http.calls)


def test_ambiguous_name_is_not_guessed(regions):
    http = FakeHttp({"attractions.json": FakeResponse(200, fixture_text("ticketmaster_attractions_ambiguous.json"))})
    artist = Artist(name="Sia", aliases=["Sia"])
    with pytest.raises(AmbiguousArtist) as exc:
        ticketmaster.fetch(artist, regions, http=http)
    assert {c["id"] for c in exc.value.candidates} == {"K8vZ91AAAA1", "K8vZ91AAAA2"}
    assert artist.ticketmaster_id is None


def test_missing_key_makes_source_unavailable(monkeypatch, coldplay, regions):
    monkeypatch.delenv("TICKETMASTER_API_KEY")
    with pytest.raises(SourceUnavailable):
        ticketmaster.fetch(coldplay, regions, http=FakeHttp())


@pytest.mark.parametrize("status", [401, 429])
def test_auth_and_quota_errors(status, coldplay, regions):
    http = FakeHttp({"attractions.json": FakeResponse(status, "{}")})
    with pytest.raises(SourceUnavailable) as exc:
        ticketmaster.fetch(coldplay, regions, http=http)
    assert "SECRET-TEST-KEY" not in str(exc.value)


def test_paging_stops_at_deep_paging_limit(regions):
    artist = Artist(name="Coldplay", aliases=["Coldplay"], ticketmaster_id="X")
    page = '{"_embedded": {"events": []}, "page": {"totalPages": 50}}'
    http = FakeHttp({"events.json": FakeResponse(200, page)})
    ticketmaster.fetch(artist, regions, http=http)
    assert len(http.calls) == 5  # 5 pages * 200 = 1000 items
