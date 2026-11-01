"""Diocese of Stockton: the diocesan site (www.stocktondiocese.org) answers a Cloudflare challenge ("Just a moment...") to our client, including
/robots.txt and /wp-json/. Nothing is collected; the evidence is written to data/real/california/dioceses/stockton/.

    python -I ingest/california/directories/stockton.py
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
    print(json.dumps(C.blocked_diocese("stockton", "Diocese of Stockton", ["www.stocktondiocese.org"], LAPTOP_NOTE, "stockton.py"), indent=2))
