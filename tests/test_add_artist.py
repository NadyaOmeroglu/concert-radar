import pytest

from collector.add_artist import add_artist
from collector.config import load_artists
from tests.conftest import ROOT


@pytest.fixture
def artists_file(tmp_path):
    path = tmp_path / "artists.yaml"
    path.write_text((ROOT / "config" / "artists.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_adds_artist_with_tour_page(artists_file):
    add_artist(artists_file, "  Florence + the   Machine ", "https://example.org/tour")
    added = load_artists(artists_file)[-1]
    assert added.name == "Florence + the Machine"
    assert added.official_tour_url == "https://example.org/tour"
    assert added.ticketmaster_id is None and added.active
    assert len(load_artists(artists_file)) == 41
    assert artists_file.read_text(encoding="utf-8").startswith("# ")  # comments kept


def test_tricky_names_stay_strings(artists_file):
    add_artist(artists_file, 'NO')  # would be a boolean in bare YAML
    add_artist(artists_file, 'Guns N\' Roses: "Live"')
    names = [a.name for a in load_artists(artists_file)]
    assert names[-2:] == ["NO", 'Guns N\' Roses: "Live"']


@pytest.mark.parametrize("name", ["Coldplay", "coldplay", "Pink", "Beyonce", "Rosé"])
def test_duplicates_are_rejected(artists_file, name):
    with pytest.raises(ValueError, match="already"):
        add_artist(artists_file, name)


@pytest.mark.parametrize("name,url", [("", ""), ("Someone", "javascript:alert(1)"), ("Someone", "coldplay.com")])
def test_bad_input_is_rejected(artists_file, name, url):
    with pytest.raises(ValueError):
        add_artist(artists_file, name, url)
