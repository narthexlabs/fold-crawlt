"""Wix: free-form text boxes. generic_strict on the schedule page's page container (#PAGES_CONTAINER / #SITE_PAGES),
without the site header and footer (#SITE_HEADER, #SITE_FOOTER) that repeat on every page."""
from __future__ import annotations

import re

from . import generic_strict

NAME = "wix"
DETECT = re.compile(r"static\.parastorage\.com|wixstatic\.com|content=\"wix\.com website builder|x-wix-", re.I)
MAIN = ["#PAGES_CONTAINER", "#SITE_PAGES", "main"]
DROP = ["#SITE_HEADER", "#SITE_FOOTER", "header", "footer"]


def detect(html: str, url: str) -> bool:
    return bool(DETECT.search(html or ""))


def parse_full(pages, names=None):
    return generic_strict.parse_full(pages, names, NAME, MAIN, DROP)


def parse(pages, names=None) -> list[dict]:
    from . import common as C
    return C.public(parse_full(pages, names)[0])
