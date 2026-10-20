"""Shared test helpers. All data under tests/fixtures is fake and used only by tests."""
import json
import shutil
from pathlib import Path

import pytest

from collector.config import load_regions
from collector.models import Artist

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Stands in for net.PoliteSession: answers by URL substring, records calls."""

    def __init__(self, routes=None, robots_allowed=True):
        self.routes = routes or {}
        self.robots_allowed = robots_allowed
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        for part, resp in self.routes.items():
            if part in url:
                return resp(url, params) if callable(resp) else resp
        return FakeResponse(404, "not found")

    def allowed_by_robots(self, url):
        if isinstance(self.robots_allowed, Exception):
            raise self.robots_allowed
        return self.robots_allowed


@pytest.fixture
def regions():
    return load_regions(ROOT / "config" / "regions.yaml")


@pytest.fixture
def coldplay():
    return Artist(name="Coldplay", aliases=["Coldplay"])


@pytest.fixture
def project(tmp_path):
    """A throw-away copy of config/ and empty docs/data for full collector runs."""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    data = tmp_path / "docs" / "data"
    data.mkdir(parents=True)
    for name in ("events.json", "archive.json"):
        (data / name).write_text("[]")
    (tmp_path / "config" / "artists.yaml").write_text(
        'artists:\n'
        '  - name: "Coldplay"\n    aliases: ["Coldplay"]\n    official_tour_url: null\n'
        '    ticketmaster_id: null\n    active: true\n\n'
        '  - name: "Adele"\n    aliases: ["Adele"]\n    official_tour_url: null\n'
        '    ticketmaster_id: null\n    active: true\n', encoding="utf-8")
    return tmp_path
