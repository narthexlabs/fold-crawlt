"""Diocese of Monterey in California: the diocesan site (www.dioceseofmonterey.org) answers a Cloudflare challenge ("Just a moment...") to our client, including
/robots.txt and /wp-json/. Nothing is collected; the evidence is written to data/real/california/dioceses/monterey/.

    python -I ingest/california/directories/monterey.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

LAPTOP_NOTE = (
    "the same Cloudflare challenge page (HTTP 403, 'Just a moment...') for /robots.txt at about 08:17 UTC; not retried."
)

if __name__ == "__main__":
    print(json.dumps(C.blocked_diocese("monterey", "Diocese of Monterey in California", ["www.dioceseofmonterey.org"], LAPTOP_NOTE, "monterey.py"), indent=2))
