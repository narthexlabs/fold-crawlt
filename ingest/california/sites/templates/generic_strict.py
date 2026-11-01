"""generic_strict: the strict reader (common.Reader) over the main content of each schedule page.

Used directly for builders with no template of their own, and as the fallback inside every template.
"""
from __future__ import annotations

from . import common as C

NAME = "generic_strict"


def detect(html: str, url: str) -> bool:
    return True


def parse_full(pages, names=None, template: str = NAME, main_selectors=(), drop=()):
    own = set().union(*[C.name_words(n) for n in names]) if names else set()
    rows, dropped = C.read_pages(pages, own, template, main_selectors, drop, names=names)
    return rows, dropped


def parse(pages, names=None) -> list[dict]:
    return C.public(parse_full(pages, names)[0])
