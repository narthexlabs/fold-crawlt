"""Weebly (Square Online classic): free-form. generic_strict on #wsite-content (the page body), without the header."""
from __future__ import annotations

import re

from . import generic_strict

NAME = "weebly"
DETECT = re.compile(r"editmysite\.com|weebly\.com/|weeblycloud|class=\"wsite-", re.I)
MAIN = ["#wsite-content", ".wsite-section-wrap", "#main", "main"]
DROP = ["#header", ".wsite-header", "#nav", ".wsite-menu-default", "footer", ".wsite-footer", "#footer"]


def detect(html: str, url: str) -> bool:
    return bool(DETECT.search(html or ""))


def parse_full(pages, names=None):
    return generic_strict.parse_full(pages, names, NAME, MAIN, DROP)


def parse(pages, names=None) -> list[dict]:
    from . import common as C
    return C.public(parse_full(pages, names)[0])
