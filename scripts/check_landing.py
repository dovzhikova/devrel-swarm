#!/usr/bin/env python3
"""Static checks for landing/index.html against spec success criteria."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

LANDING = Path(__file__).resolve().parent.parent / "landing" / "index.html"

# From spec — slop blocklist applied to ALL text content (lowercase compare).
BANNED_PHRASES: tuple[str, ...] = (
    "revolutionary",
    "game-changing",
    "unleash",
    "supercharge",
    "leverage",
    "ai-powered",
    "reimagine",
    "transform",
    "the future of",
    "intelligent",
    "cutting-edge",
    "paradigm-shift",
    "world-class",
    "best-in-class",
)

MAX_BYTES = 30 * 1024  # 30 KB target from spec


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, _attrs):
        if tag in {"style", "script", "code", "pre"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script", "code", "pre"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def check() -> list[str]:
    failures: list[str] = []

    if not LANDING.is_file():
        return [f"file missing: {LANDING}"]

    raw = LANDING.read_bytes()
    text_html = raw.decode("utf-8")

    if len(raw) > MAX_BYTES:
        failures.append(f"file weight {len(raw)} bytes > {MAX_BYTES} cap")

    parser = TextExtractor()
    parser.feed(text_html)
    visible = parser.text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in visible:
            failures.append(f"banned phrase in visible copy: {phrase!r}")

    if not re.search(r"<html[^>]*\blang=", text_html):
        failures.append("missing <html lang=...> attribute")
    h1_count = len(re.findall(r"<h1\b", text_html))
    if h1_count != 1:
        failures.append(f"expected exactly 1 <h1>, found {h1_count}")
    if "<main" not in text_html:
        failures.append("missing <main> landmark")

    if "prefers-color-scheme" not in text_html:
        failures.append("missing prefers-color-scheme media query")

    external = re.findall(
        r"""<(?:link|script|img|iframe)[^>]*\b(?:src|href)=["']https?://""",
        text_html,
    )
    if external:
        failures.append(f"external resource(s) detected: {len(external)}")

    return failures


def main() -> int:
    failures = check()
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK ({LANDING.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
