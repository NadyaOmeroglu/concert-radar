"""Polite HTTP client: identifies itself, waits between requests, retries 429/5xx, honours robots.txt."""
from __future__ import annotations

import logging
import re
import time
from urllib import robotparser
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

USER_AGENT = "ConcertRadar/1.0 (personal concert tracker; +https://github.com/NadyaOmeroglu/concert-radar)"
RETRY_STATUSES = {429, 500, 502, 503, 504}

_SECRET_PARAM = re.compile(r"(apikey|api_key|token|key)=[^&\s'\"]+", re.IGNORECASE)


def redact(text: str, *secrets: str | None) -> str:
    """Remove API keys from text that may end up in logs or in the public meta.json."""
    text = _SECRET_PARAM.sub(r"\1=***", str(text))
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


class RobotsUnreachable(Exception):
    """robots.txt could not be read (network error or server error) — we do not crawl then."""


class PoliteSession:
    def __init__(self, min_interval: float, max_retries: int = 5, session=None, sleep=time.sleep,
                 timeout: float = 30):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.sleep = sleep
        self.timeout = timeout
        self._last = 0.0
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    def _wait(self):
        delta = time.monotonic() - self._last
        if delta < self.min_interval:
            self.sleep(self.min_interval - delta)
        self._last = time.monotonic()

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            self._wait()
            resp = self.session.get(url, params=params, timeout=self.timeout,
                                    headers={"User-Agent": USER_AGENT})
            if resp.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                return resp
            retry_after = resp.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else delay
            log.info("HTTP %s from %s, retrying in %.0fs", resp.status_code, urlsplit(url).netloc, wait)
            self.sleep(wait)
            delay *= 2
        return resp  # pragma: no cover

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = robotparser.RobotFileParser()
            try:
                resp = self.get(base + "/robots.txt")
            except requests.RequestException:
                resp = None
            if resp is None or resp.status_code >= 500:
                # robots.txt unreachable: be conservative and treat the site as off-limits.
                self._robots[base] = None
            elif resp.status_code >= 400:
                parser.parse([])  # no robots.txt -> everything allowed
                self._robots[base] = parser
            else:
                parser.parse(resp.text.splitlines())
                self._robots[base] = parser
        parser = self._robots[base]
        if parser is None:
            raise RobotsUnreachable(f"could not read {base}/robots.txt")
        return parser.can_fetch(USER_AGENT, url)


def looks_like_bot_challenge(resp: requests.Response) -> bool:
    """Cloudflare / anti-bot pages. We never try to get around them."""
    if resp.status_code in (401, 403, 429, 503):
        return True
    head = resp.text[:3000].lower() if resp.text else ""
    return ("just a moment" in head and "cloudflare" in head) or "cf-chl" in head
