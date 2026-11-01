"""ParishSoft: not a site builder in this crawl.

`cms_guess == "parishsoft"` (59 California sites) comes from parse_site.CMS_RULES matching "parishsoft.com" anywhere in
the home page, ahead of the builder rules. In the cached pages every one of those matches is a giving or Family Suite
link (giving.parishsoft.com, connectnowgiving.parishsoft.com, <diocese>.parishsoftfamilysuite.com); the sites themselves
are WordPress (19), Wix (10), Weebly (7), Squarespace (3), Edlio (2), GoDaddy, eCatholic, Google Sites, Drupal and
unidentified builders. No page carries ParishSoft website markup or a ParishSoft generator tag, so there is no
ParishSoft schedule widget to read. Those sites are routed to their real builder's template (templates.platform_of
ignores giving links); this template matches only a page that is itself served by ParishSoft, and reads it with
generic_strict.
"""
from __future__ import annotations

import re

from . import generic_strict

NAME = "parishsoft"
# a page served by ParishSoft itself (none in the 2026-10-31 cache); giving / Family Suite links do not count
DETECT = re.compile(r"<meta[^>]+generator[^>]+parishsoft|powered by parishsoft websites|\.parishsoft\.app/", re.I)


def detect(html: str, url: str) -> bool:
    return bool(DETECT.search(html or "")) or bool(re.search(r"//[^/]*parishsoft\.(?:app|site)/", url or ""))


def parse_full(pages, names=None):
    return generic_strict.parse_full(pages, names, NAME)


def parse(pages, names=None) -> list[dict]:
    from . import common as C
    return C.public(parse_full(pages, names)[0])
