"""Squarespace: free-form blocks. generic_strict on the page's <main> / #page / article (section blocks), without the
site header and footer."""
from __future__ import annotations

import re

from . import generic_strict

NAME = "squarespace"
DETECT = re.compile(r"static1\.squarespace\.com|squarespace-cdn\.com|<!-- this is squarespace|content=\"squarespace", re.I)
MAIN = ["main#page", "main", "#page", "article", "#sections", ".sqs-layout"]
DROP = ["header#header", "header", "footer", ".header-announcement-bar-wrapper"]


def detect(html: str, url: str) -> bool:
    return bool(DETECT.search(html or ""))


def parse_full(pages, names=None):
    return generic_strict.parse_full(pages, names, NAME, MAIN, DROP)


def parse(pages, names=None) -> list[dict]:
    from . import common as C
    return C.public(parse_full(pages, names)[0])
