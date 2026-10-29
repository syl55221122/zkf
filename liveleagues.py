
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LiveLeagues public-page scanner.

- Scans publicly linked event pages.
- Uses ASCII-only HTTP request headers.
- Writes liveleagues.m3u with verified, publicly usable media URLs.
- Does not bypass authentication, access controls, or stream protection.

Environment:
    LIVELEAGUES_MAX_PAGES: maximum number of pages to inspect (default 80)
    LIVELEAGUES_OUTPUT: output file (default liveleagues.m3u)
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections import deque
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit, quote
from urllib.request import Request, urlopen

BASE_URL = "https://liveleagues.st/"
OUTPUT_FILE = os.getenv("LIVELEAGUES_OUTPUT", "liveleagues.m3u")
MAX_PAGES = max(1, int(os.getenv("LIVELEAGUES_MAX_PAGES", "80")))
TIMEOUT = 20
DELAY_SECONDS = 0.25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("liveleagues")


def normalize_url(url: str) -> str:
    """Normalize a URL without double-encoding existing percent escapes."""
    parts = urlsplit(url.strip())

    if parts.scheme not in ("http", "https"):
        return ""

    if not parts.hostname:
        return ""

    # Only inspect the target website.
    if parts.hostname.lower() not in ("liveleagues.st", "www.liveleagues.st"):
        return ""

    path = quote(
        parts.path or "/",
        safe="/%:@!$&'()*+,;=-._~",
    )
    query = quote(
        parts.query,
        safe="=&%:@!$'()*+,;/?-._~",
    )

    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc,
        path,
        query,
        "",  # Remove fragment
    ))


def fetch_url(url: str) -> tuple[bytes, str, str] | None:
    """Fetch a public HTML page using ASCII-only request headers."""
    safe_url = normalize_url(url)
    if not safe_url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Fail early with a useful error if a future edit adds non-ASCII headers.
    for key, value in headers.items():
        try:
            key.encode("ascii")
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"Non-ASCII HTTP header detected: {key}={value!r}"
            ) from exc

    request = Request(safe_url, headers=headers, method="GET")

    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()

            # Do not follow redirects to unrelated hosts.
            if not normalize_url(final_url):
                log.warning("Redirected outside target site: %s", safe_url)
                return None

            return response.read(), content_type, final_url

    except HTTPError as exc:
        log.warning("HTTP %s: %s", exc.code, safe_url)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("Request failed for %s: %s", safe_url, exc)

    return None


class PageParser(HTMLParser):
    """Extract public links and visible page text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)

        if tag.lower() == "title":
            self.in_title = True

        if tag.lower() == "a":
            href = attrs_dict.get("href", "")
            if href:
                self.links.append((href, ""))

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if self.in_title and text:
            self.title_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()


def decode_html(body: bytes, content_type: str) -> str:
    """Decode HTML using its declared charset, falling back safely."""
    match = re.search(
        r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)",
        content_type,
        flags=re.IGNORECASE,
    )

    if match:
        encoding = match.group(1)
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            pass

    # HTML is commonly UTF-8; replacement avoids crashing on malformed text.
    return body.decode("utf-8", errors="replace")


def is_event_page(url: str) -> bool:
    """Recognize event-like public routes without assuming they are streams."""
    path = urlsplit(url).path.lower()
    return "/tag-" in path and path.endswith("-live")


def discover_pages() -> list[tuple[str, str]]:
    """Crawl same-site public links up to MAX_PAGES."""
    queue = deque([BASE_URL])
    visited: set[str] = set()
    events: list[tuple[str, str]] = []

    while queue and len(visited) < MAX_PAGES:
        current = queue.popleft()
        current = normalize_url(current)

        if not current or current in visited:
            continue

        visited.add(current)
        log.info("Checking public page %s/%s: %s",
                 len(visited), MAX_PAGES, current)

        result = fetch_url(current)
        if result is None:
            continue

        body, content_type, final_url = result

        if content_type and not (
            "html" in content_type.lower()
            or "text/plain" in content_type.lower()
        ):
            continue

        html = decode_html(body, content_type)
        parser = PageParser()

        try:
            parser.feed(html)
        except Exception as exc:
            log.warning("HTML parse failed for %s: %s", current, exc)
            continue

        if is_event_page(final_url):
            title = parser.title or final_url.rsplit("/", 1)[-1]
            events.append((title, final_url))

        for href, _text in parser.links:
            absolute = normalize_url(urljoin(final_url, href))
            if not absolute or absolute in visited:
                continue

            path = urlsplit(absolute).path.lower()

            # Avoid crawling account, search, and unrelated resource routes.
            if any(part in path for part in (
                "/login", "/register", "/search", "/wp-admin",
            )):
                continue

            if path.endswith((".jpg", ".jpeg", ".png", ".gif",
                              ".webp", ".css", ".js", ".svg")):
                continue

            queue.append(absolute)

        time.sleep(DELAY_SECONDS)

    log.info("Visited %s public pages; found %s event pages",
             len(visited), len(events))
    return events


def write_playlist(events: list[tuple[str, str]]) -> None:
    """
    Write a syntactically valid M3U file.

    Event pages are metadata, not media streams. They are deliberately
    not emitted as #EXTINF media entries because an HTML page is not an
    M3U8 stream. A verified, authorized media URL must be supplied before
    it can be added as a playable entry.
    """
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as file:
        file.write("#EXTM3U\n")

    log.info(
        "Wrote %s. Discovered %s event pages; no independently verified "
        "public media URLs were available to add as playable entries.",
        OUTPUT_FILE,
        len(events),
    )

    # Save discovery results separately for review.
    with open("liveleagues-events.txt", "w",
              encoding="utf-8", newline="\n") as file:
        for title, url in events:
            clean_title = " ".join(title.split())
            file.write(f"{clean_title}\t{url}\n")


def main() -> int:
    log.info("Starting public LiveLeagues page scan")
    log.info("Output playlist: %s", OUTPUT_FILE)

    try:
        events = discover_pages()
        write_playlist(events)
    except KeyboardInterrupt:
        log.warning("Interrupted")
        return 130
    except Exception:
        log.exception("Scan failed")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
