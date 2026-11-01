"""GoDaddy Website Builder: free-form sections. generic_strict on the page sections ([data-ux=Page] / main), without
the [data-ux=Header] and [data-ux=Footer] sections."""
from __future__ import annotations

import re

from . import generic_strict

NAME = "godaddy"
DETECT = re.compile(r"img1\.wsimg\.com|godaddy website builder|websites\.godaddy|content=\"starfield", re.I)
MAIN = ["[data-ux=Page]", "main", "#layout"]
# A content card repeats its heading in copies without data-aid (editor / layout residue): only the rendered one counts.
DROP = ["[data-ux=ContentCardHeading]:not([data-aid])", "[data-ux=Header]", "[data-ux=Footer]", "[data-ux=HeaderMedia]", "header", "footer", "[data-aid=HEADER_SECTION]",
        "[data-aid=FOOTER_SECTION]"]


def detect(html: str, url: str) -> bool:
    return bool(DETECT.search(html or ""))


def parse_full(pages, names=None):
    return generic_strict.parse_full(pages, names, NAME, MAIN, DROP)


def parse(pages, names=None) -> list[dict]:
    from . import common as C
    return C.public(parse_full(pages, names)[0])
