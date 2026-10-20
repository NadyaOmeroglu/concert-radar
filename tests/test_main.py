"""Full collector runs with fake sources: one broken source must not break the run."""
import json
from datetime import datetime, timezone

from collector import main
from collector.models import RawEvent, SourceUnavailable

NOW = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)


def good_event(artist, city="Paris", country="FR", source="ticketmaster"):
    return RawEvent(source=source, artist=artist, url=f"https://t/{artist}/{city}", date="2027-05-01",
                    city=city, country=country, source_id=f"{source}:{artist}:{city}")


class Good:
    def fetch(self, artist, regions):
        return [good_event(artist.name), good_event(artist.name, "London", "GB")]


class Crashes:
    def fetch(self, artist, regions):
        raise ConnectionError("boom")


class Down:
    def fetch(self, artist, regions):
        raise SourceUnavailable("blocked by anti-bot page")


class CrashesForAdele:
    def fetch(self, artist, regions):
        if artist.name == "Adele":
            raise ValueError("bad page")
        return [good_event(artist.name, "Dubai", "AE", source="platinumlist")]


def read(project, name):
    return json.loads((project / "docs" / "data" / name).read_text())


def test_failing_sources_do_not_stop_the_run(project):
    result = main.run(project, now=NOW, sources={"ticketmaster": Good(), "official": Crashes(),
                                                 "platinumlist": Down()})
    assert result.events_written and not result.total_failure
    events = read(project, "events.json")
    assert sorted(e["artist"] for e in events) == ["Adele", "Coldplay"]  # London filtered out
    meta = read(project, "meta.json")
    assert meta["sources"]["platinumlist"]["status"] == "unavailable"
    assert meta["sources"]["official"]["status"] == "skipped"  # nobody has an official_tour_url here


def test_one_artist_failing_keeps_the_others(project):
    result = main.run(project, now=NOW, sources={"platinumlist": CrashesForAdele()})
    meta = read(project, "meta.json")
    assert meta["sources"]["platinumlist"]["status"] == "partial"
    assert [e["artist"] for e in meta["errors"]] == ["Adele"]
    assert [e["city"] for e in read(project, "events.json")] == ["Dubai"]
    assert result.changes["new"][0]["artist"] == "Coldplay"


def test_total_failure_keeps_previous_events(project):
    main.run(project, now=NOW, sources={"ticketmaster": Good()})
    before = (project / "docs" / "data" / "events.json").read_text()
    result = main.run(project, now=NOW, sources={"ticketmaster": Down(), "platinumlist": Down()})
    assert result.total_failure and not result.events_written
    assert (project / "docs" / "data" / "events.json").read_text() == before
    assert read(project, "meta.json")["totalFailure"] is True


def test_events_of_a_down_source_are_not_removed(project):
    main.run(project, now=NOW, sources={"ticketmaster": Good(), "platinumlist": CrashesForAdele()})
    for _ in range(3):
        main.run(project, now=NOW, sources={"ticketmaster": Good(), "platinumlist": Down()})
    assert "Dubai" in {e["city"] for e in read(project, "events.json")}


def test_single_artist_run_keeps_other_artists(project):
    main.run(project, now=NOW, sources={"ticketmaster": Good()})
    for _ in range(3):
        main.run(project, now=NOW, sources={"ticketmaster": Good()}, only_artist="Coldplay")
    assert sorted(e["artist"] for e in read(project, "events.json")) == ["Adele", "Coldplay"]


def test_api_key_never_reaches_meta(project, monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "SECRET-TEST-KEY")

    class Leaky:
        def fetch(self, artist, regions):
            raise RuntimeError("GET https://x/events.json?apikey=SECRET-TEST-KEY failed")

    main.run(project, now=NOW, sources={"ticketmaster": Leaky(), "platinumlist": CrashesForAdele()})
    assert "SECRET-TEST-KEY" not in (project / "docs" / "data" / "meta.json").read_text()
