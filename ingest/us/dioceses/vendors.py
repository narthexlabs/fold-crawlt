"""Vendor-specific diocese-directory readers (lane G4b), added as they were met in the 2026-11-01 pass.

Each reader returns {"records", "log", "base", "reader"} or None; directory_pages.py / directory_html.py try them
first and fall back to the generic engine (dirlib.read_see) when none yields >= 5 records.

  dpi_gis    Diocesan Publications ("diocesanweb.org" hosting; Orlando, Hartford, St. Petersburg, Venice, Wilmington...):
             the parish finder prints every point as <li class="dpi_gis-poi-list-item" data-module="parishes"
             data-name data-address data-city data-statecode data-zipcode data-latitude data-longitude data-website>.
             data-phone / data-email are the parish office's (kept as office_phone / office_email, Matthew 2026-11-01);
             data-mass-times is not read here.
  data_attrs any other list whose elements carry data-name + data-website (or data-url) attributes.
  wpgmza     WP Go Maps marker REST (/wp-json/wpgmza/v1/markers): title, address, lat/lng, link.
  inline_json  map plugins that print every point as JSON inside a <script> (WP Google Map Pro/Gold `"places":[...]`
             with location.lat/lng/city/postal_code and extra_fields.website; Store Locator Plus, Agile Store Locator,
             and similar): any JSON array of >= 5 objects with a title/name and coordinates. Only name, address, city,
             state, zip, coordinates and a website-named field are read; phone, email, pastor, staff are skipped.

Only name, address, city, state, zip, coordinates, website and the source URL are kept.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

import common_fetch as CF
import dirlib as DL

DPI_PAGES = ["/parishes/", "/find-a-parish/", "/parish-finder/", "/parish-directory/", "/our-parishes/", "/locations/"]


def _rec(name, website, own, **kw):
    w = DL.clean_href(website or "")
    if w and "://" not in w and re.match(r"^(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}", w, re.I):
        w = "https://" + w
    ok = DL.website_ok(w, own)
    return dict(name=DL.clean(name), website=DL.norm_site(w) if ok else None, website_basis="vendor-field" if ok else None,
                **kw)


def _pages(probe, see):
    base = probe.get("base") or "https://" + CF.host_of(see["website"])
    out = []
    if probe.get("directory_page"):
        out.append(CF.https_only(probe["directory_page"]))
    for h in (probe.get("doors") or {}).get("wp_search_parish", []) or []:
        u = h.get("url") or ""
        if re.search(r"/(?:find-a-parish|parishes|parish-directory|our-parishes|parishes-2|parish-finder|places|"
                     r"directory|map-of-parishes|parishes-contacts)/?$", u):
            out.append(CF.https_only(u if u.startswith("http") else base + u))
    out += [base + p for p in DPI_PAGES]
    return base, list(dict.fromkeys(out))


def data_attr_reader(see, probe, pol, refresh=False):
    base, pages = _pages(probe, see)
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=12)
    own = {CF.host_of(base)}
    tried = []
    for u in pages:
        r = F.get(u)
        tried.append({"url": u, "class": r.get("class")})
        if r["class"] != "ok" or b"data-" not in r["body"]:
            continue
        s = DL.soup_of(r["body"])
        if s is None:
            continue
        own.add(CF.host_of(r.get("final_url") or u))
        dpi = s.select('[data-module="parishes"][data-name]') or [x for x in s.select(".dpi_gis-poi-list-item[data-name]")
                                                                  if (x.get("data-module") or "parishes") in ("parishes", "parish")]
        reader = "dpi_gis" if dpi else "data_attrs"
        els = dpi or [x for x in s.find_all(attrs={"data-name": True}) if x.get("data-website") or x.get("data-url")]
        recs = []
        for e in els:
            nm = e.get("data-name") or ""
            if not nm or (not dpi and not DL.name_ok(nm)):
                continue
            lat = lng = None
            try:
                lat, lng = float(e.get("data-latitude") or e.get("data-lat")), float(e.get("data-longitude") or e.get("data-lng"))
                if not (17 < lat < 72 and -180 < lng < -60):
                    lat = lng = None
            except (TypeError, ValueError):
                pass
            recs.append(_rec(nm, e.get("data-website") or e.get("data-url"), own,
                             address=DL.scrub(e.get("data-address")), city=e.get("data-city") or None,
                             state=e.get("data-statecode") or e.get("data-state") or None,
                             zip=(e.get("data-zipcode") or e.get("data-zip") or None), lat=lat, lng=lng,
                             office_phone=DL.norm_phone(e.get("data-phone")), office_email=DL.office_email_ok(e.get("data-email")),
                             source_url=r.get("final_url") or u, type_in_source=e.get("data-module"), _conf="vendor"))
        if len(recs) >= 5:
            cms = DL.cms_guess(r["body"])
            if dpi:
                cms = "diocesan (dpi_gis)"
            return {"records": recs, "base": base, "reader": reader,
                    "log": {"cms_guess": cms, "lists": [{"url": u, "entries": len(recs)}], "vendor": reader,
                            "requests_net": F.n_net, "requests_cached": F.n_cache}}
    return None


def wpgmza_reader(see, probe, pol, refresh=False):
    ns = ((probe.get("doors") or {}).get("wp-json") or {}).get("namespaces") or []
    if "wpgmza/v1" not in ns:
        return None
    base = probe.get("base")
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=6)
    r = F.get(f"{base}/wp-json/wpgmza/v1/markers?" + urlencode({"filter": "{}"}), accept="application/json")
    if r["class"] != "ok":
        r = F.get(f"{base}/wp-json/wpgmza/v1/markers", accept="application/json")
    try:
        js = json.loads(r["body"]) if r["class"] == "ok" else None
    except ValueError:
        js = None
    if isinstance(js, dict):
        js = js.get("data") or js.get("markers") or list(js.values())
    if not isinstance(js, list):
        return None
    own = {CF.host_of(base)}
    recs = []
    for m in js:
        if not isinstance(m, dict):
            continue
        nm = DL.clean(re.sub(r"<[^>]+>", " ", str(m.get("title") or "")))
        if not nm:
            continue
        link = m.get("link") or ""
        if not link:
            mm = re.search(r"href=[\"'](https?://[^\"']+)", str(m.get("description") or ""))
            link = mm.group(1) if mm else ""
        addr = DL.address_from(str(m.get("address") or ""))
        try:
            lat, lng = float(m.get("lat")), float(m.get("lng"))
        except (TypeError, ValueError):
            lat = lng = None
        recs.append(_rec(nm, link, own, address=addr["address"] or DL.scrub(str(m.get("address") or "")[:120]) or None,
                         city=addr["city"], state=addr["state"], zip=addr["zip"], lat=lat, lng=lng,
                         source_url=f"{base}/wp-json/wpgmza/v1/markers", type_in_source="wpgmza-marker", _conf="rest"))
    if len(recs) < 5:
        return None
    return {"records": recs, "base": base, "reader": "wpgmza",
            "log": {"cms_guess": "wordpress (WP Go Maps)", "lists": [{"url": f"{base}/wp-json/wpgmza/v1/markers",
                                                                      "entries": len(recs)}], "vendor": "wpgmza"}}


def _arrays_in(text: str):
    """Yield JSON arrays of objects embedded in script text (balanced-bracket scan from each '[{')."""
    i = 0
    n = 0
    while n < 40:
        i = text.find("[{", i)
        if i < 0:
            return
        depth, j, ins, esc = 0, i, False, False
        while j < len(text) and j - i < 3_000_000:
            c = text[j]
            if ins:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    ins = False
            elif c == '"':
                ins = True
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        chunk = text[i:j + 1]
        i = j + 1 if j > i else i + 2
        if len(chunk) < 200:
            continue
        n += 1
        try:
            arr = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(arr, list) and len(arr) >= 5 and all(isinstance(x, dict) for x in arr[:5]):
            yield arr


def _flat(d, pre=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, pre + k + "."))
        elif not isinstance(v, list):
            out[pre + k] = v
    return out


def _pick(f: dict, rx: str, not_rx: str = r"$^"):
    for k, v in f.items():
        last = k.rsplit(".", 1)[-1]
        if re.fullmatch(rx, last, re.I) and not re.search(not_rx, k, re.I) and v not in (None, "", False):
            return v
    return None


def inline_json_reader(see, probe, pol, refresh=False):
    base, pages = _pages(probe, see)
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=12)
    own = {CF.host_of(base)}
    for u in pages:
        r = F.get(u)
        if r["class"] != "ok":
            continue
        body = r["body"].decode("utf-8", "replace")
        if "[{" not in body:
            continue
        own.add(CF.host_of(r.get("final_url") or u))
        best = []
        for arr in _arrays_in(body):
            recs = []
            for it in arr:
                f = _flat(it)
                nm = _pick(f, r"title|name|store|post_title|location_name|label")
                lat = _pick(f, r"lat|latitude")
                lng = _pick(f, r"lng|lon|long|longitude")
                if not nm or lat is None or lng is None:
                    continue
                try:
                    lat, lng = float(lat), float(lng)
                except (TypeError, ValueError):
                    continue
                if not (17 < lat < 72 and -180 < lng < -60):
                    lat = lng = None
                web = _pick(f, r"website|web|url|site|homepage|link|permalink", r"icon|image|img|photo|logo|facebook|map")
                nm = DL.clean(re.sub(r"<[^>]+>", " ", str(nm)))
                addr = DL.clean(re.sub(r"<[^>]+>", " ", str(_pick(f, r"address|street|address1|addr") or "")))
                a2 = DL.address_from(addr + ", " + str(_pick(f, r"city") or "") + ", " + str(_pick(f, r"state") or "") +
                                     " " + str(_pick(f, r"postal_code|zip|zipcode|postcode") or ""))
                zp = str(_pick(f, r"postal_code|zip|zipcode|postcode") or "")[:5] or a2["zip"]
                recs.append(_rec(nm, str(web or ""), own, address=a2["address"] or (DL.scrub(addr[:120]) or None),
                                 office_phone=DL.norm_phone(_pick(f, r"phone|telephone|tel")),
                                 office_email=DL.office_email_ok(_pick(f, r"email|e-mail|mail")),
                                 city=_pick(f, r"city") or a2["city"], state=_pick(f, r"state") or a2["state"],
                                 zip=zp if re.fullmatch(r"\d{5}", zp or "") else None, lat=lat, lng=lng,
                                 source_url=r.get("final_url") or u, type_in_source="map-json", _conf="vendor"))
            if len(recs) > len(best):
                best = recs
        if len(best) >= 5:
            plug = "wp-google-map-pro" if "wpgmp" in body else ("store-locator" if "wpsl" in body or "asl_" in body else "map-json")
            return {"records": best, "base": base, "reader": "inline_json:" + plug,
                    "log": {"cms_guess": DL.cms_guess(r["body"]), "lists": [{"url": u, "entries": len(best)}],
                            "vendor": "inline_json:" + plug, "requests_net": F.n_net}}
    return None


def ssf_reader(see, probe, pol, refresh=False):
    """Super Store Finder for WordPress: the map loads /wp-content/plugins/superstorefinder-wp/ssf-wp-xml.php
    (<item><location> <address> <latitude> <longitude> <website>). Telephone/email/fax elements are not read."""
    base, pages = _pages(probe, see)
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=12)
    for u in pages:
        r = F.get(u)
        if r["class"] != "ok" or b"superstorefinder-wp" not in r["body"]:
            continue
        m = re.search(rb"(https?://[^\"' ]+?)/wp-content/plugins/superstorefinder-wp", r["body"])
        root = m.group(1).decode() if m else base
        x = F.get(root + "/wp-content/plugins/superstorefinder-wp/ssf-wp-xml.php", accept="application/xml,text/xml,*/*")
        if x["class"] != "ok":
            return None
        own = {CF.host_of(base), CF.host_of(root)}
        recs = []
        for it in re.findall(rb"<item>(.*?)</item>", x["body"], re.S):
            def g(tag):
                mm = re.search(rb"<" + tag + rb">(.*?)</" + tag + rb">", it, re.S)
                return DL.clean(re.sub(r"<!\[CDATA\[|\]\]>", "", mm.group(1).decode("utf-8", "replace"))) if mm else ""
            nm = g(b"location")
            if not nm:
                continue
            a = DL.address_from(g(b"address"))
            try:
                lat, lng = float(g(b"latitude")), float(g(b"longitude"))
            except ValueError:
                lat = lng = None
            recs.append(_rec(nm, g(b"website"), own, address=a["address"], city=a["city"], state=a["state"], zip=a["zip"],
                             office_phone=DL.norm_phone(g(b"telephone")), office_email=DL.office_email_ok(g(b"email")),
                             lat=lat, lng=lng, source_url=r.get("final_url") or u, type_in_source="ssf-store", _conf="vendor"))
        if len(recs) >= 5:
            return {"records": recs, "base": base, "reader": "superstorefinder",
                    "log": {"cms_guess": "wordpress (Super Store Finder)", "vendor": "superstorefinder",
                            "lists": [{"url": x["url"], "entries": len(recs)}]}}
        return None
    return None


READERS = [data_attr_reader, wpgmza_reader, inline_json_reader, ssf_reader]


def try_vendor(see: dict, probe: dict, pol, refresh: bool = False) -> dict | None:
    best = None
    for fn in READERS:
        try:
            v = fn(see, probe, pol, refresh)
        except Exception as e:  # noqa: BLE001
            v = None
            print(f"    vendor {fn.__name__} failed on {see['slug']}: {e!r}"[:200])
        if v and (best is None or sum(1 for r in v["records"] if r.get("website")) >
                  sum(1 for r in best["records"] if r.get("website"))):
            best = v
    return best
