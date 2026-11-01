"""WordPress: generic_strict scoped to the post / page content of common Catholic themes and page builders.

Content containers tried in order: .entry-content (classic and block themes), Elementor's page document
([data-elementor-type=wp-page] / wp-post / single-page), Divi (#et-main-area .et_pb_section), WPBakery (.wpb_wrapper
inside .entry-content), Beaver Builder (.fl-builder-content), then <main>/#content/article. Theme headers and footers
(Elementor header/footer documents, .site-header, #masthead, .site-footer, widget areas) are removed. Mass-times
plugins seen in the cache are plain HTML tables or lists inside the content, so the strict reader's table rules apply.
"""
from __future__ import annotations

import re

from . import generic_strict

NAME = "wordpress"
DETECT = re.compile(r"/wp-content/|/wp-includes/|wp-json|content=\"wordpress", re.I)
MAIN = ["[data-elementor-type=wp-page]", "[data-elementor-type=single-page]", "[data-elementor-type=wp-post]", ".entry-content",
        "#et-main-area", ".fl-builder-content", ".page-content", ".post-content", "main", "#content", "article", "#main"]
DROP = ["[data-elementor-type=header]", "[data-elementor-type=footer]", "[data-elementor-type=popup]", ".site-header", "#masthead",
        ".site-footer", "#colophon", ".widget-area", "#secondary", ".sidebar", ".et_pb_menu", ".elementor-location-header",
        ".elementor-location-footer", ".comments-area", "#comments", ".sharedaddy", ".post-navigation", ".nav-links"]


def detect(html: str, url: str) -> bool:
    return bool(DETECT.search(html or ""))


def parse_full(pages, names=None):
    return generic_strict.parse_full(pages, names, NAME, MAIN, DROP)


def parse(pages, names=None) -> list[dict]:
    from . import common as C
    return C.public(parse_full(pages, names)[0])
