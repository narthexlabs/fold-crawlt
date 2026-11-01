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
from urllib.parse import urlencode, urljoin

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


# ================================================================================================ JS finders (2026-11-01)
# Parish finders that draw their list with JavaScript. Each reader reads the finder page's HTML and inline scripts (no
# JS is executed), finds the public data request the page itself makes, fetches it once, and keeps name, address, city,
# state, zip, coordinates, website and the institutional office phone / e-mail. Description, pastor, staff, clergy and
# Mass-time fields are never read. Only requests the page sends unauthenticated are made; a finder whose data needs a
# POST body, a signed token (Wix data, Squarespace ?format=json is robots-disallowed) or a challenge (batchgeo) is
# recorded as needs_js / needs_post in directory_status.json and left alone. Records carry _conf="api".
#
#   js_bundle    a finder bundle served by the diocese (Liferay /o/<org>.maps.web/...js) whose code says
#                fetch("/o/<org>.maps.web/api/parishes.json") or names https://api.<host>/parishes  (Joliet, Chicago)
#   wpsl         WP Store Locator: admin-ajax.php?action=store_search&autoload=1 around the page's own start point
#   slp          Store Locator Plus: admin-ajax.php?action=csl_ajax_onload around the page's own map centre
#   mymaps_kml   a Google My Maps embed (maps/d/embed?mid=..): its public KML export, Point placemarks only
#   elfsight     Elfsight Google Maps widget: core.service.elfsight.com/p/boot/?w=<widget id> (what platform.js loads)
#   locator_list server-rendered .parish-info cards that the locator script only filters (Buildable, Wichita)
#   sqs_items    a Squarespace gallery/list whose items are printed as JSON in the page (title + description)

JS_FINDER_PAGES = {
    "diocese-of-joliet": "https://www.diojoliet.org/parishes/",
    "archdiocese-of-chicago": "https://www.archchicago.org/parish-map",
    "diocese-of-buffalo": "https://www.buffalodiocese.org/parish-finder/",
    "diocese-of-camden": "https://www.camdendiocese.org/psl/",
    "diocese-of-norwich": "https://www.norwichdiocese.org/Find/Parish-Communities",
    "diocese-of-lubbock": "https://catholiclubbock.church/Parishes.html",
    "diocese-of-rapid-city": "https://www.rapidcitydiocese.org/parishes/",
    "diocese-of-wichita": "https://catholicdioceseofwichita.org/parishes/",
    "diocese-of-spokane": "https://www.dioceseofspokane.org/parish-directory",
}
API_LABEL = "diocese-directory-api"


def _finder(see, probe, pol, refresh, budget=8):
    u = JS_FINDER_PAGES.get(see["slug"]) or probe.get("directory_page")
    if not u:
        return None, None, None
    base = "https://" + CF.host_of(u)
    F = DL.Fetcher(see["slug"], base, pol, refresh, budget=budget)
    r = F.get(u)
    if r["class"] != "ok":
        return F, None, u
    return F, r, r.get("final_url") or u


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x


def _ll(lat, lng):
    lat, lng = _f(lat), _f(lng)
    if lat is None or lng is None or not (17 < lat < 72 and -180 < lng < -60):
        return None, None
    return lat, lng


def _zip5(z):
    m = re.match(r"\s*(\d{5})", str(z or ""))
    return m.group(1) if m else None


def _api(recs, base, reader, src, cms, page):
    if len(recs) < 5:
        return None
    return {"records": recs, "base": base, "reader": "api:" + reader,
            "log": {"cms_guess": cms, "vendor": reader, "finder_page": page, "lists": [{"url": src, "entries": len(recs)}]}}


def _json_items(js, own, src, kind):
    """Generic mapping for a JSON list of parish objects (field names vary by vendor)."""
    if isinstance(js, dict):
        js = js.get("results") or js.get("data") or js.get("items") or js.get("response") or js.get("parishes") or []
    out = []
    for it in js if isinstance(js, list) else []:
        if not isinstance(it, dict):
            continue
        it = dict(it)
        locs = it.get("locations")
        if isinstance(locs, list) and locs and isinstance(locs[0], dict):
            it["location"] = locs[0]
        f = _flat(it)
        nm = _pick(f, r"name|title|store|sl_store", r"location\.name|locations")
        if not nm:
            continue
        lat, lng = _ll(_pick(f, r"lat|latitude|sl_latitude"), _pick(f, r"lng|lon|longitude|sl_longitude"))
        web = _pick(f, r"website|url|sl_url|web", r"pages_url|image|icon|thumb|map")
        street = DL.clean(" ".join(str(x) for x in (_pick(f, r"address1?|sl_address"), _pick(f, r"address2|sl_address2")) if x))
        typ = _pick(f, r"type|descrip|category")
        out.append(_rec(re.sub(r"\s*\[\d{4}\]\s*", " ", DL.clean(str(nm))).strip(), str(web or ""), own,
                        address=DL.scrub(street[:120]) or None, city=_pick(f, r"city|sl_city"),
                        state=_pick(f, r"state|sl_state"), zip=_zip5(_pick(f, r"zip|zipcode|postal_code|sl_zip")),
                        lat=lat, lng=lng, office_phone=DL.norm_phone(_pick(f, r"phone|phonenumber|telephone|sl_phone")),
                        office_email=DL.office_email_ok(_pick(f, r"email|sl_email")), source_url=src,
                        type_in_source=f"{kind}:{typ}" if typ else kind, _conf="api"))
    return out


def js_bundle_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r:
        return None
    body = r["body"].decode("utf-8", "replace")
    own = {CF.host_of(page)}
    base = "https://" + CF.host_of(page)
    for s in re.findall(r'<script[^>]+src="([^"]*/o/[^"]*maps[^"]*\.js)', body):
        js = pol.get(CF.https_only(urljoin(page, s.replace("&amp;", "&"))), accept="*/*")
        if js["class"] not in ("ok", "not_html") or not js.get("body"):
            continue
        code = js["body"].decode("utf-8", "replace")
        eps = re.findall(r'fetch\("(/o/[^"]+/api/parishes\.json)"', code) + \
            re.findall(r'(https://api\.[a-z0-9.-]+/parishes)\b', code)
        for ep in dict.fromkeys(eps):
            x = F.get(ep, accept="application/json")
            try:
                data = json.loads(x["body"]) if x.get("body") else None
            except ValueError:
                data = None
            recs = _json_items(data, own, x.get("url") or ep, "finder-api")
            if len(recs) >= 5:
                return _api(recs, base, "js_bundle", x.get("url") or ep, "liferay (maps portlet)", page)
    return None


def wpsl_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r or b"wp-store-locator" not in r["body"]:
        return None
    body = r["body"].decode("utf-8", "replace")
    m = re.search(r'"startLatlng":"(-?[\d.]+),(-?[\d.]+)"', body)
    root = re.search(r'(https?:\\?/\\?/[^"]+?)\\?/wp-admin\\?/admin-ajax\.php', body)
    ajax = (root.group(1).replace("\\/", "/") if root else "https://" + CF.host_of(page)) + "/wp-admin/admin-ajax.php"
    lat, lng = (m.group(1), m.group(2)) if m else (None, None)
    if lat is None:
        return None
    q = urlencode({"action": "store_search", "lat": lat, "lng": lng, "max_results": 500, "search_radius": 500, "autoload": 1})
    x = F.get(f"{ajax}?{q}", accept="application/json")
    try:
        data = json.loads(x["body"]) if x.get("body") else None
    except ValueError:
        return None
    recs = _json_items(data, {CF.host_of(page)}, x.get("url"), "wpsl-store")
    return _api(recs, "https://" + CF.host_of(page), "wpsl", x.get("url"), "wordpress (WP Store Locator)", page)


def slp_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r or b"store-locator-le" not in r["body"]:
        return None
    body = r["body"].decode("utf-8", "replace")
    lat = re.search(r'"map_center_lat":"(-?[\d.]+)"', body)
    lng = re.search(r'"map_center_lng":"(-?[\d.]+)"', body)
    root = re.search(r'"ajaxurl":"([^"]+)"', body)
    ajax = root.group(1).replace("\\/", "/") if root else "https://" + CF.host_of(page) + "/wp-admin/admin-ajax.php"
    lat, lng = (lat.group(1), lng.group(1)) if lat and lng else (None, None)
    if lat is None:   # no centre printed: the first location's own coordinates are not known yet; use the page's map
        mm = re.search(r'"(?:default_)?(?:lat|latitude)":"(-?\d+\.\d+)".{0,80}?"(?:lng|longitude)":"(-?\d+\.\d+)"', body)
        if not mm:
            return None
        lat, lng = mm.group(1), mm.group(2)
    q = urlencode({"action": "csl_ajax_onload", "lat": lat, "lng": lng, "radius": 500,
                   "options[initial_results_returned]": 500})
    x = F.get(f"{ajax}?{q}", accept="application/json")
    try:
        data = json.loads(x["body"]) if x.get("body") else None
    except ValueError:
        return None
    items = (data or {}).get("response") if isinstance(data, dict) else None
    recs = _json_items([{k: v for k, v in it.items() if k not in ("description", "data", "hours")} for it in items or []],
                       {CF.host_of(page)}, x.get("url"), "slp-location")
    return _api(recs, "https://" + CF.host_of(page), "slp", x.get("url"), "wordpress (Store Locator Plus)", page)


def mymaps_kml_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r:
        return None
    mids = re.findall(rb"google\.com/maps/d/(?:u/\d+/)?(?:embed|viewer)\?mid=([A-Za-z0-9_-]+)", r["body"])
    for mid in dict.fromkeys(m.decode() for m in mids):
        G = DL.Fetcher(see["slug"], "https://www.google.com", pol, refresh, budget=2)
        x = G.get(f"https://www.google.com/maps/d/kml?mid={mid}&forcekml=1",
                  accept="application/vnd.google-earth.kml+xml,application/xml,*/*")
        kml = (x.get("body") or b"").decode("utf-8", "replace")
        recs = []
        for pm in re.findall(r"<Placemark>(.*?)</Placemark>", kml, re.S):
            pt = re.search(r"<Point>\s*<coordinates>\s*(-?[\d.]+),(-?[\d.]+)", pm)
            if not pt and not re.search(r"<address>", pm):
                continue        # polygons are boundaries / counties, not parishes (address-only points are kept)
            ed = {k.lower(): DL.clean(re.sub(r"<!\[CDATA\[|\]\]>", "", v)) for k, v in
                  re.findall(r'<Data name="([^"]+)">\s*<value>(.*?)</value>', pm, re.S)}
            nm = ed.get("church") or ed.get("parish name") or ed.get("name") or \
                DL.clean(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(r"<name>(.*?)</name>", pm, re.S) or [None, ""])[1]))
            nm = nm.split("\n")[0] if nm else nm
            nm = re.split(r"\s{2,}|\s\d{1,6}\s", nm)[0].strip() if nm else nm
            lat, lng = _ll(pt.group(2), pt.group(1)) if pt else (None, None)
            web = ed.get("website") or ed.get("web site") or ed.get("url") or ""
            if not web:
                mw = re.search(r"https?://[^\s<\]]+", re.sub(r"<description>.*?</description>", "", pm, flags=re.S))
                web = mw.group(0) if mw and "google" not in mw.group(0) and "usercontent" not in mw.group(0) else ""
            street = DL.clean(" ".join(x for x in (ed.get("address 1"), ed.get("address 2")) if x))
            a = DL.address_from(DL.clean(re.sub(r"<[^>]+>", " ", (re.search(r"<address>(.*?)</address>", pm, re.S) or [None, ""])[1])))
            recs.append(_rec(nm, web, {CF.host_of(page)}, address=DL.scrub(street) or a["address"],
                             city=ed.get("city") or a["city"], state=ed.get("state") or a["state"],
                             zip=_zip5(ed.get("zip code") or ed.get("zip")) or a["zip"], lat=lat, lng=lng,
                             office_phone=DL.norm_phone(ed.get("telephone") or ed.get("phone")),
                             source_url=x.get("url"), type_in_source="mymaps-placemark", _conf="api"))
        if len(recs) >= 5:
            return _api(recs, "https://" + CF.host_of(page), "mymaps_kml", x.get("url"), DL.cms_guess(r["body"]), page)
    return None


def elfsight_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r or b"elfsightcdn.com/platform.js" not in r["body"]:
        return None
    for wid in dict.fromkeys(re.findall(rb'class="elfsight-app-([0-9a-f-]{36})', r["body"])):
        wid = wid.decode()
        E = DL.Fetcher(see["slug"], "https://core.service.elfsight.com", pol, refresh, budget=2)
        x = E.get(f"https://core.service.elfsight.com/p/boot/?w={wid}", accept="application/json")
        try:
            d = json.loads(x["body"])["data"]["widgets"][wid]["data"]
        except (ValueError, KeyError, TypeError):
            continue
        if d.get("app") != "google-maps":
            continue
        recs = []
        for mk in (d.get("settings") or {}).get("markers") or []:
            nm = DL.clean(mk.get("infoTitle"))
            if not nm:
                continue
            a = DL.address_from(mk.get("infoAddress") or mk.get("position") or "")
            ll = (mk.get("coordinates") or "").split(",")
            lat, lng = _ll(*ll) if len(ll) == 2 else (None, None)
            ph = DL.PHONE_ONE.search(mk.get("infoPhone") or "")
            recs.append(_rec(nm, mk.get("infoSite") or mk.get("linkUrl") or "", {CF.host_of(page)},
                             address=a["address"], city=a["city"], state=a["state"], zip=a["zip"], lat=lat, lng=lng,
                             office_phone=DL.norm_phone(ph.group(0)) if ph else None,
                             office_email=DL.office_email_ok(mk.get("infoEmail")), source_url=x.get("url"),
                             type_in_source="elfsight-marker", _conf="api"))
        return _api(recs, "https://" + CF.host_of(page), "elfsight", x.get("url"), DL.cms_guess(r["body"]), page)
    return None


def locator_list_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r or b"parish-info" not in r["body"]:
        return None
    s = DL.soup_of(r["body"])
    recs = []
    for c in s.select(".parishes-list .parish-info") if s else []:
        h = c.select_one(".name h4, .name")
        nm = DL.clean(h.get_text(" ", strip=True)) if h else ""
        ad = c.select_one(".address")
        a = DL.address_from(ad.get_text(" ", strip=True) if ad else "")
        w = c.select_one(".website a[href]")
        ph = c.select_one(".phone-number")
        recs.append(_rec(nm, w["href"] if w else "", {CF.host_of(page)}, address=a["address"],
                         city=c.get("data-city") or a["city"], state=a["state"], zip=a["zip"], lat=None, lng=None,
                         office_phone=DL.norm_phone(ph.get_text(" ", strip=True)) if ph else None,
                         source_url=page, type_in_source="locator-card", _conf="api"))
    return _api(recs, "https://" + CF.host_of(page), "locator_list", page, DL.cms_guess(r["body"]), page)


def sqs_items_reader(see, probe, pol, refresh=False):
    F, r, page = _finder(see, probe, pol, refresh)
    if not r or b"squarespace" not in r["body"]:
        return None
    import html as _h
    text = _h.unescape(r["body"].decode("utf-8", "replace"))
    best = []
    for arr in _arrays_in(text):
        recs = []
        for it in arr:
            if not isinstance(it, dict) or not it.get("title") or "description" not in it:
                continue
            desc = str(it.get("description") or "")
            plain = DL.clean(re.sub(r"<[^>]+>", " ", desc))
            a = DL.address_from(plain)
            if not (a["zip"] or a["address"]):
                continue
            hrefs = re.findall(r'href="(https?://[^"]+)"', desc)
            ph = DL.PHONE_ONE.search(plain)
            nm = DL.clean(it["title"])
            recs.append(_rec(nm, hrefs[0] if hrefs else "", {CF.host_of(page)}, address=a["address"],
                             city=a["city"], state=a["state"], zip=a["zip"], lat=None, lng=None,
                             office_phone=DL.norm_phone(ph.group(0)) if ph else None,
                             source_url=page, type_in_source="sqs-item", _conf="api"))
        if len(recs) > len(best):
            best = recs
    return _api(best, "https://" + CF.host_of(page), "sqs_items", page, "squarespace", page)


API_READERS = [js_bundle_reader, wpsl_reader, slp_reader, mymaps_kml_reader, elfsight_reader, locator_list_reader,
               sqs_items_reader]
READERS += API_READERS
