#!/usr/bin/env python3
# pipedream add-package curl-cffi
# pipedream add-package beautifulsoup4
"""
BCN long-term rental watcher — Pipedream & local dual-mode.

Watches Barcelona LONG-TERM rentals matching your criteria on:
  agencies : Housfy, ShBarcelona (yearly dept), Loca Barcelona (long-term)
  portals  : Fotocasa, Habitaclia, Pisos.com

New matches are sent to Telegram as links. Dedupe state lives in a Pipedream
Data Store (cloud) or state.json (local). Zero AI tokens — deterministic
filtering only.

PIPEDREAM: paste this whole file into a Python code step. Trigger: Schedule
(every 5 hours). Add a Data Store prop named "data_store" and set env vars
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID. Done.

LOCAL:  bot.py [--setup | --dry-run | --chat-id | --test]
        --setup = interactive wizard that writes config.json (your criteria:
        price, size, rooms, zones, furnished, pets, rental type, deal-breakers)
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from html import escape
from typing import List, Optional

from bs4 import BeautifulSoup
from curl_cffi import requests

# ----------------------------- configuration --------------------------------
# All user preferences live in config.json (create/update it interactively
# with `bot.py --setup`). Env vars override — that's how cloud runs tweak
# things without touching the file.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "max_price": 1000,           # EUR / month
    "min_size": 30,              # m2
    "max_rooms": 1,              # 0 = studio only, 1 = up to 1 bedroom, ...
    "rental_type": "long_term",  # "long_term" (>= 1 year only) or "any"
    "zones": [],                 # e.g. ["gracia", "eixample"]; [] = anywhere in BCN
    "require_furnished": False,  # True = only listings that mention furnished
    "pets_info": True,           # tag listings that mention pets are allowed
    "avoid_platforms": [         # short/mid-term aggregators to always skip
        "uniplaces", "renteazily", "spotahome", "housinganywhere", "badi"],
    "avoid_keywords": [],        # personal deal-breakers, plain text
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    path = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("config.json ignored (%s)" % e, file=sys.stderr)
    for env, key in (("MAX_PRICE", "max_price"), ("MIN_SIZE", "min_size"),
                     ("MAX_ROOMS", "max_rooms"), ("RENTAL_TYPE", "rental_type")):
        if os.environ.get(env):
            v = os.environ[env]
            cfg[key] = int(v) if v.isdigit() else v
    return cfg


CFG = load_config()
MAX_PRICE = int(CFG["max_price"])
MIN_SIZE = int(CFG["min_size"])
MAX_ROOMS = int(CFG["max_rooms"])
LONG_TERM_ONLY = CFG.get("rental_type", "long_term") != "any"
MAX_MSGS_PER_RUN = int(os.environ.get("MAX_MSGS_PER_RUN", "30"))  # TG flood safety
MAX_SEEN_IDS = 1500                                    # dedupe memory cap
# check each NEW listing's detail page and drop expired/rented ones
VERIFY_ALIVE = os.environ.get("VERIFY_ALIVE", "1") != "0"


def _norm(s: str) -> str:
    """lowercase + strip accents, for zone matching (Gràcia == gracia)."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


# Seasonal / temporary / touristic markers (skipped if rental_type == "any").
SEASONAL_PATTERNS = [
    r"temporada", r"temporal",
    # month-capped contracts (the 11-month seasonal loophole and friends);
    # 12+ months is a normal yearly lease so we only match 1-11.
    r"\b(1[01]|[1-9])\s*mes(es|os)?\s*(m[aá]xim|de\s+estancia|de\s+contrato)",
    r"m[aá]xim[oa]?\s*(de\s*)?(1[01]|[1-9])\s*mes",
    r"contrato\s+de\s+(1[01]|[1-9])\s*mes", r"estancia\s+m[aá]xima",
    r"corta\s*estancia", r"media\s*estancia", r"estancia\s*m[ií]nima",
    r"estancias\s*(de|por)", r"estades",
    r"por\s*meses", r"mes\s*a\s*mes", r"per\s*mesos",
    r"vacacional", r"tur[ií]stic",
    r"short[\s-]*term", r"mid[\s-]*term", r"seasonal", r"month[\s-]*to[\s-]*month",
]
# Shared flats / rooms — always unwanted (this bot finds whole homes).
SHARED_PATTERNS = [
    r"compartid", r"compartit", r"co-?living",
    r"habitaci[oó]n\s+en\s+piso", r"room\s+in\s+a",
]
# Known spelling variants of avoidable platforms.
PLATFORM_PATTERNS = {
    "uniplaces": r"uniplace",
    "renteazily": r"rente\s*az[iy]ly|rente\s*asily",
    "spotahome": r"spot\s*a\s*home|spotahome",
    "housinganywhere": r"housing\s*anywhere|housanywhere",
    "badi": r"\bbadi\b",
}


def _build_blacklist():
    pats = list(SHARED_PATTERNS)
    if LONG_TERM_ONLY:
        pats += SEASONAL_PATTERNS
    for p in CFG.get("avoid_platforms") or []:
        pats.append(PLATFORM_PATTERNS.get(p.lower(), re.escape(p)))
    for kw in CFG.get("avoid_keywords") or []:
        pats.append(re.escape(kw))
    return re.compile("|".join(pats), re.IGNORECASE)


BLACKLIST_RE = _build_blacklist()
FURNISHED_RE = re.compile(r"amueblad|moblat|furnished", re.IGNORECASE)
PETS_RE = re.compile(
    r"(se\s+)?(admiten?|aceptan?)\s+mascotas|mascotas\s+(s[ií]|permitidas|bienvenidas)|"
    r"pet[\s-]*friendly|pets?\s+(allowed|welcome)|apto\s+(para\s+)?mascotas",
    re.IGNORECASE)
ZONES_NORM = [_norm(z) for z in (CFG.get("zones") or []) if z]


def screen_blob(l) -> str:
    """All text a listing should be blacklist-screened against."""
    return " ".join(str(l.get(k) or "") for k in ("title", "desc", "screen"))

# Signals that a detail page is dead / already rented (ES + CA + EN).
DEAD_RE = re.compile(
    r"ya no est[aá] disponible|no se encuentra disponible|anuncio no disponible|"
    r"anuncio (ha sido )?dado de baja|anuncio (ha )?caducado|anuncio finalizado|"
    r"ja no est[aà] disponible|no longer available|has been rented|"
    r">\s*Alquilado\s*<|>\s*Reservado\s*<|>\s*Llogat\s*<", re.IGNORECASE)

# Non-residential markers (agency feeds mix in commercial units).
COMMERCIAL_RE = re.compile(
    r"local\s+comercial|commercial\s+premises|oficina\b|office\s+for|"
    r"plaza\s+de\s+(garaje|parking)|parking\s+space|trastero|storage\s+room|"
    r"nave\s+industrial|\+\s*iva", re.IGNORECASE)

# ----------------------------- helpers -------------------------------------

def parse_int(text: Optional[str]) -> Optional[int]:
    """'1.000 €/mes' -> 1000. Returns None if no digits."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text.split(",")[0])
    return int(digits) if digits else None


def fetch(url: str, impersonate: str = "chrome") -> str:
    r = requests.get(
        url, impersonate=impersonate, timeout=30,
        headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
    )
    r.raise_for_status()
    return r.text


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")   # no lxml: Pipedream-friendly


def card_screen(el) -> str:
    """Full blacklist-screening text for a card element: visible text plus
    every img alt / title attribute (that's where badges like 'Temporada'
    and advertiser logos like 'Uniplaces' live)."""
    parts = [el.get_text(" ", strip=True)]
    for node in el.select("img, [title], [alt]"):
        parts.append(node.get("alt") or "")
        parts.append(node.get("title") or "")
    return " ".join(p for p in parts if p)


class Listing(dict):
    """Keys: id, source, url, title, price, size, rooms, desc."""


# ----------------------------- agency scrapers ------------------------------

def scrape_housfy() -> List[Listing]:
    # Housfy is 100% standard long-term (LAU) rental — no seasonal stock at all.
    html = fetch("https://housfy.com/alquiler-pisos/barcelona/barcelona")
    i = html.find('"properties":')
    if i < 0:
        raise RuntimeError("housfy: embedded properties JSON not found")
    arr, _ = json.JSONDecoder().raw_decode(html[i + len('"properties":'):])
    out = []
    for p in arr:
        if p.get("typeCode") in ("local", "oficina", "parking", "trastero"):
            continue
        if p.get("isVisible") is False or p.get("reservedDate"):
            continue
        price = p.get("price") or {}
        amount = price.get("amount")
        out.append(Listing(
            id="housfy:%s" % p.get("providerPropertyId"), source="Housfy",
            url=p.get("providerPropertyUrl") or "",
            title=p.get("providerPropertyTitle") or "Piso en Barcelona",
            price=int(amount / 100) if amount else None,   # amount is in cents
            size=p.get("size"), rooms=p.get("numberOfBedrooms"),
            desc=p.get("description") or "",
        ))
    return out


def scrape_shbarcelona() -> List[Listing]:
    # department "yearly" = ShBarcelona's long-term (1+ year) catalogue.
    html = fetch("https://www.shbarcelona.com/apartments-for-rent/long-term")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("shbarcelona: __NEXT_DATA__ not found")
    data = json.loads(m.group(1))
    items = data["props"]["pageProps"]["initialProperties"]["data"]
    # detail links are rendered as /l/<slug>--<id>
    href_by_id = {}
    for h in set(re.findall(r'href="(/l/[^"]+--(\d+))"', html)):
        href_by_id[h[1]] = h[0]
    out = []
    for it in items:
        if it.get("department_name") != "yearly" or not it.get("available", True):
            continue
        sid = str(it["id"])
        situation = it.get("situation") or ""
        texts = " ".join((t.get("title") or "") + " " + (t.get("description") or "")
                         for t in it.get("texts") or [])
        desc = re.sub(r"<[^>]+>", " ", texts)
        if COMMERCIAL_RE.search(situation + " " + desc):
            continue
        rooms = None
        for c in it.get("favourite_characteristics") or []:
            if c.get("description") == "bedrooms" and c.get("value"):
                rooms = c["value"]
        zone = (it.get("zone") or {}).get("description") or ""
        href = href_by_id.get(sid)
        out.append(Listing(
            id="shb:%s" % sid, source="ShBarcelona (agency)",
            url="https://www.shbarcelona.com" + href if href
                else "https://www.shbarcelona.com/apartments-for-rent/long-term",
            title="%s — %s (ref %s)" % (situation or "Piso", zone, it.get("reference")),
            price=it.get("price"), size=it.get("surface"), rooms=rooms, desc=desc,
        ))
    return out


def scrape_loca() -> List[Listing]:
    # WordPress category listing only long-term rentals.
    html = fetch("https://www.locabarcelona.com/en/property-status/long-term-rental/")
    out = []
    for art in soup_of(html).select("article.rh_list_card"):
        a = art.select_one('a[href*="/property/"]')
        if not a:
            continue
        url = a["href"]
        lid = art.get("data-propertyid") or url
        title_el = art.select_one("h3")
        title = title_el.get_text(" ", strip=True) if title_el else \
            url.rstrip("/").split("/")[-1].replace("-", " ")
        title = re.sub(r"\s+", " ", title)
        text = art.get_text(" ", strip=True)
        price = None
        m = re.search(r"([\d.,]+)\s*€|€\s*([\d.,]+)", text)
        if m:
            price = parse_int(m.group(1) or m.group(2))
        size = rooms = None
        m = re.search(r"(\d+)\s*m[²2]?\b", text)
        if m:
            size = int(m.group(1))
        m = re.search(r"(\d+)\s*(?:Bedroom|bed\b|Habitaci|Dormitor)", text, re.I)
        if m:
            rooms = int(m.group(1))
        if rooms is None and re.search(r"\bstudio\b|\bestudio\b", title, re.I):
            rooms = 0
        out.append(Listing(
            id="loca:%s" % lid, source="Loca Barcelona (agency)",
            url=url, title=title, price=price, size=size, rooms=rooms,
            desc=text[:600], screen=card_screen(art),
        ))
    return out


def scrape_teixidor() -> List[Listing]:
    # Finques Teixidor: classic BCN administrador de fincas, server-rendered
    # ColdFusion catalogue. All rentals are standard long-term.
    html = fetch("https://www.finquesteixidor.com/es/alquiler-barcelona.cfm")
    out = []
    for block in soup_of(html).select("div.pgl-property"):
        a = block.select_one('a[href*="/ID/"]')
        if not a:
            continue
        href = a["href"]
        m = re.search(r"/ID/(\d+)/", href)
        if not m:
            continue
        lid = m.group(1)
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        # card text starts with the property type: "Piso ALQUILER ...",
        # "Local ALQUILER ...", "Parking ALQUILER ..."
        ptype = text.split(" ")[0].lower() if text else ""
        if ptype in ("local", "parking", "plaza", "garaje", "trastero",
                     "oficina", "nave", "solar"):
            continue
        price = size = rooms = None
        pm = re.search(r"([\d.,]+)\s*€", text)
        if pm:
            try:
                price = int(float(pm.group(1).replace(",", ".")))  # "800.0" style
            except ValueError:
                price = parse_int(pm.group(1))
        sm = re.search(r"Sup\.?\s*:?\s*([\d.]+)", text)
        if sm:
            size = int(float(sm.group(1)))
        bed = block.select_one("i.icon-bedroom")
        if bed and bed.parent:
            bm = re.search(r"(\d+)", bed.parent.get_text())
            if bm:
                rooms = int(bm.group(1))
        title = " ".join(text.split(" ")[:8])
        out.append(Listing(
            id="teixidor:%s" % lid, source="Finques Teixidor (agency)",
            url="https://www.finquesteixidor.com" + href,
            title=title, price=price, size=size, rooms=rooms,
            desc=text[:600], screen=card_screen(block),
        ))
    return out


# ----------------------------- portal scrapers ------------------------------

def scrape_idealista() -> List[Listing]:
    # 'con-alquiler-de-larga-temporada' = idealista's own LONG-TERM-only filter.
    # DataDome blocks Chrome fingerprints (and most datacenter IPs): works
    # locally with the Safari fingerprint, expected to fail on Pipedream —
    # that failure is isolated and everything else keeps running.
    # NB: adding ?ordenado-por= to this filtered URL returns HTTP 400.
    lt = ",alquiler-de-larga-temporada" if LONG_TERM_ONLY else ""
    url = (
        "https://www.idealista.com/alquiler-viviendas/barcelona-barcelona/"
        "con-precio-hasta_{p},metros-cuadrados-mas-de_{s}{lt}/"
    ).format(p=MAX_PRICE, s=MIN_SIZE, lt=lt)
    html = fetch(url, impersonate="safari184")
    out = []
    for art in soup_of(html).select("article.item"):
        lid = art.get("data-element-id")
        a = art.select_one("a.item-link")
        if not lid or not a:
            continue
        title = (a.get("title") or a.get_text(" ", strip=True)).strip()
        price_el = art.select_one(".item-price")
        price = parse_int(price_el.get_text()) if price_el else None
        size = rooms = None
        for d in art.select(".item-detail"):
            t = d.get_text(" ", strip=True)
            m = re.search(r"(\d+)\s*m²", t)
            if m:
                size = int(m.group(1))
            m = re.search(r"(\d+)\s*hab", t)
            if m:
                rooms = int(m.group(1))
        if rooms is None and re.search(r"\bestudio\b", title, re.I):
            rooms = 0
        desc_el = art.select_one(".item-description")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        out.append(Listing(
            id="idealista:%s" % lid, source="Idealista",
            url="https://www.idealista.com" + a["href"],
            title=title, price=price, size=size, rooms=rooms, desc=desc,
            screen=card_screen(art),
        ))
    return out

def scrape_fotocasa() -> List[Listing]:
    # No URL filter excludes seasonal rentals and ~90% of new sub-1000EUR
    # listings are seasonal, so walk pages sorted by date and keep only
    # isTemporaryRental == false items.
    out = []
    for page in range(1, 6):
        url = (
            "https://www.fotocasa.es/es/alquiler/viviendas/barcelona-capital/"
            "todas-las-zonas/l{pg}?maxPrice={p}&minSurface={s}&sortType=publicationDate"
        ).format(pg="" if page == 1 else "/%d" % page, p=MAX_PRICE, s=MIN_SIZE)
        html = fetch(url)
        m = re.search(r'<script[^>]*id="__initial_props__"[^>]*>(.*?)</script>',
                      html, re.S)
        if not m:
            if page == 1:
                raise RuntimeError("fotocasa: __initial_props__ not found")
            break   # later page served a bad variant: keep what we have
        result = json.loads(m.group(1))["initialSearch"]["result"]
        items = (result.get("resultsV2") or {}).get("items") or []
        if not items:
            break
        desc_by_id, age_by_id = {}, {}
        for re_ in result.get("realEstates") or []:
            rid = str(re_.get("id"))
            desc_by_id[rid] = re_.get("description") or ""
            d = re_.get("date") or {}
            if d.get("diff") is not None:
                unit = {"MINUTES": "min", "HOURS": "h", "DAYS": "d"}.get(
                    d.get("unit"), (d.get("unit") or "").lower())
                age_by_id[rid] = "hace %s %s" % (d["diff"], unit)
        for it in items:
            if LONG_TERM_ONLY and it.get("isTemporaryRental"):  # seasonal flag
                continue
            if "IS_SHARED" in (it.get("dynamicFeatures") or []):
                continue
            pid = str(it.get("propertyId") or "")
            feats = it.get("features") or {}
            detail_url = it.get("detailUrl") or ""
            if not pid or not detail_url:
                continue
            out.append(Listing(
                id="fotocasa:%s" % pid, source="Fotocasa",
                url="https://www.fotocasa.es" + detail_url,
                title=(it.get("location") or {}).get("address") or "Piso en Barcelona",
                price=(it.get("price") or {}).get("amount"),
                size=feats.get("surface"), rooms=feats.get("rooms"),
                desc=desc_by_id.get(pid, ""), age=age_by_id.get(pid),
                screen=(it.get("publisher") or {}).get("name") or "",
            ))
        time.sleep(random.uniform(1, 2))
    return out


def scrape_habitaclia() -> List[Listing]:
    url = ("https://www.habitaclia.com/alquiler-barcelona.htm"
           "?pmax={p}&m2min={s}&ordenar=mas_recientes").format(p=MAX_PRICE, s=MIN_SIZE)
    html = fetch(url)
    out = []
    for art in soup_of(html).select("article.js-list-item"):
        lid = art.get("data-id")
        href = (art.get("data-href") or "").split("?")[0]
        if not lid or not href:
            continue
        title_el = art.select_one(".list-item-title") or art.select_one("h3")
        title = title_el.get_text(" ", strip=True) if title_el else href
        text = art.get_text(" ", strip=True)
        price_el = art.select_one('.list-item-price [itemprop="price"]') or \
            art.select_one(".list-item-price")
        price = parse_int(price_el.get_text()) if price_el else None
        size = rooms = None
        m = re.search(r"(\d+)\s*m[²2]?\b", text)
        if m:
            size = int(m.group(1))
        m = re.search(r"(\d+)\s*hab", text)
        if m:
            rooms = int(m.group(1))
        desc_el = art.select_one(".list-item-description")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        out.append(Listing(
            id="habitaclia:%s" % lid, source="Habitaclia",
            url=href, title=title, price=price, size=size, rooms=rooms, desc=desc,
            screen=card_screen(art),
        ))
    return out


def scrape_pisos() -> List[Listing]:
    url = ("https://www.pisos.com/alquiler/pisos-barcelona_capital/"
           "desde-{s}-m2/hasta-{p}/").format(p=MAX_PRICE, s=MIN_SIZE)
    html = fetch(url)
    out = []
    for div in soup_of(html).select("div.ad-preview[data-lnk-href]"):
        href = div.get("data-lnk-href") or ""
        lid = div.get("id") or href
        title_el = div.select_one(".ad-preview__title")
        title = title_el.get_text(" ", strip=True) if title_el else href
        price_el = div.select_one(".ad-preview__price")
        price = parse_int(price_el.get_text()) if price_el else None
        text = div.get_text(" ", strip=True)
        size = rooms = None
        m = re.search(r"(\d+)\s*m²", text)
        if m:
            size = int(m.group(1))
        m = re.search(r"(\d+)\s*hab", text)
        if m:
            rooms = int(m.group(1))
        desc_el = div.select_one(".ad-preview__description")
        desc = desc_el.get_text(" ", strip=True) if desc_el else ""
        out.append(Listing(
            id="pisos:%s" % lid, source="Pisos.com",
            url="https://www.pisos.com" + href,
            title=title, price=price, size=size, rooms=rooms, desc=desc,
            screen=card_screen(div),   # catches the 'Temporada' type badge
        ))
    return out


# agencies first (user priority), then portals
SCRAPERS = [scrape_housfy, scrape_shbarcelona, scrape_loca, scrape_teixidor,
            scrape_idealista, scrape_fotocasa, scrape_habitaclia, scrape_pisos]


def active_scrapers():
    """SOURCES env var picks which scrapers run. Examples:
    'all' (default) | 'idealista' | 'housfy,loca' | '-idealista' (all but).
    Lets Pipedream run everything except Idealista (blocked from cloud IPs)
    while a local on-demand run covers Idealista alone."""
    spec = os.environ.get("SOURCES", "all").strip().lower()
    if spec in ("", "all"):
        return SCRAPERS
    names = {n.strip() for n in spec.split(",") if n.strip()}
    if all(n.startswith("-") for n in names):        # exclusion mode
        excluded = {n.lstrip("-") for n in names}
        picked = [s for s in SCRAPERS
                  if s.__name__.replace("scrape_", "") not in excluded]
    else:
        picked = [s for s in SCRAPERS
                  if s.__name__.replace("scrape_", "") in names]
    if not picked:
        raise RuntimeError("SOURCES=%r matches no scraper" % spec)
    return picked

# ----------------------------- filtering -----------------------------------

def keep(l: Listing) -> bool:
    if l["price"] is None or not (0 < l["price"] <= MAX_PRICE):
        return False
    if l["size"] is not None and l["size"] < MIN_SIZE:
        return False
    if l["rooms"] is not None and l["rooms"] > MAX_ROOMS:
        return False
    blob = screen_blob(l)
    if BLACKLIST_RE.search(blob):
        return False
    if ZONES_NORM and not any(z in _norm(blob) for z in ZONES_NORM):
        return False           # user restricted the search to specific barrios
    if CFG.get("require_furnished") and not FURNISHED_RE.search(blob):
        return False
    return True

def is_alive(l) -> bool:
    """Open the listing's detail page and check it hasn't expired/been rented.
    Fail-open: when we can't tell (blocked, network trouble), keep the
    listing — better a dead link than a missed flat."""
    if l["id"].startswith("idealista:"):
        return True   # detail pages are DataDome-403'd; list presence ≈ alive
    try:
        r = requests.get(l["url"], impersonate="chrome", timeout=12,
                         headers={"Accept-Language": "es-ES,es;q=0.9"})
        if r.status_code in (404, 410):
            return False
        if r.status_code >= 400:
            return True           # blocked/erroring ≠ expired
        m = re.search(r"(\d{5,})", l["url"])
        if m and m.group(1) not in str(r.url):
            return False          # portal bounced us off the ad (it's gone)
        # fotocasa ships its full i18n dictionary (incl. expiry texts) in every
        # page's JS, so text markers always match; its redirect check above is
        # the reliable dead signal instead.
        if l["id"].startswith("fotocasa:"):
            return True
        return not DEAD_RE.search(r.text)
    except Exception:
        return True


# ----------------------------- telegram ------------------------------------

TG_API = "https://api.telegram.org/bot{token}/{method}"


def tg_call(method: str, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var not set")
    r = requests.post(TG_API.format(token=token, method=method),
                      data=params, timeout=30)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError("telegram %s failed: %s" % (method, body))
    return body["result"]


def tg_send(text: str):
    tg_call("sendMessage", chat_id=os.environ["TELEGRAM_CHAT_ID"],
            text=text, parse_mode="HTML")


def format_msg(l: dict) -> str:
    rooms = l.get("rooms")
    rooms_txt = ("estudio" if rooms == 0 else
                 "%d hab." % rooms if rooms is not None else "? hab.")
    size_txt = "%d m²" % l["size"] if l.get("size") else "? m²"
    age_txt = " · 📅 %s" % l["age"] if l.get("age") else ""
    pets_txt = " · 🐾 mascotas OK" if l.get("pets") else ""
    return ("🏠 <b>%s</b>\n💶 %s €/mes · 📐 %s · 🛏 %s%s%s\n🔎 %s\n%s"
            % (escape(l.get("title") or ""), l.get("price"), size_txt,
               rooms_txt, age_txt, pets_txt, l.get("source"), l.get("url")))

# ----------------------------- core run ------------------------------------

def run(state: dict, telegram: bool = True) -> dict:
    """state = {"seen": {id: iso_ts}, "pending": [listing dicts]}.
    Mutates and returns state; caller persists it."""
    seen = state.setdefault("seen", {})
    pending = state.setdefault("pending", [])
    now = datetime.now(timezone.utc).isoformat()
    log, errors = [], []

    scrapers = active_scrapers()
    for scraper in scrapers:
        try:
            found = scraper()
            kept = [l for l in found if keep(l)]
            new = [l for l in kept if l["id"] not in seen]
            for l in new:
                seen[l["id"]] = now
                rec = {k: l.get(k) for k in
                       ("id", "source", "url", "title", "price",
                        "size", "rooms", "age")}
                if CFG.get("pets_info") and PETS_RE.search(screen_blob(l)):
                    rec["pets"] = True
                pending.append(rec)
            log.append("%-20s %3d scraped, %2d match, %2d new"
                       % (scraper.__name__, len(found), len(kept), len(new)))
        except Exception as e:
            errors.append("%s: %s" % (scraper.__name__.replace("scrape_", ""), str(e)[:120]))
            log.append("%-20s ERROR: %s" % (scraper.__name__, str(e)[:140]))
        time.sleep(random.uniform(1.5, 2.5))   # politeness between sites

    # prune dedupe memory (oldest first) so cloud KV storage stays small
    if len(seen) > MAX_SEEN_IDS:
        for k in sorted(seen, key=seen.get)[:len(seen) - MAX_SEEN_IDS]:
            del seen[k]

    sent = expired = 0
    if telegram and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        while pending and sent < MAX_MSGS_PER_RUN:
            l = pending[0]
            # last-second expiry check right before notifying
            if VERIFY_ALIVE and not is_alive(l):
                pending.pop(0)
                expired += 1
                time.sleep(random.uniform(0.8, 1.5))
                continue
            tg_send(format_msg(l))          # raises on failure, keeps item queued
            pending.pop(0)
            sent += 1
            time.sleep(1.2)                 # Telegram rate-limit safety
        if pending:
            tg_send("…plus %d more pending listings (will follow next run)" % len(pending))
        if len(errors) >= len(scrapers) / 2:
            tg_send("⚠️ %d/%d sources failed this run: %s"
                    % (len(errors), len(scrapers), "; ".join(errors)[:600]))
    else:
        log.append("Telegram not configured — %d listing(s) queued as pending"
                   % len(pending))

    log.append("sent %d message(s), %d expired dropped, %d pending, %d ids remembered"
               % (sent, expired, len(pending), len(seen)))
    return {"state": state, "log": log, "errors": errors, "sent": sent}


# ----------------------------- Pipedream entrypoint ------------------------

def handler(pd):
    """Pipedream Python code step. Requires a Data Store prop named
    'data_store' and env vars TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
    The state key is namespaced so the data store can be shared with
    other workflows (e.g. hireme_bot) without collisions."""
    ds = pd.inputs["data_store"]
    state = ds.get("bcn_rentals_state") or {"seen": {}, "pending": []}
    result = run(state)
    ds["bcn_rentals_state"] = result["state"]
    for line in result["log"]:
        print(line)
    return {"sent": result["sent"], "errors": result["errors"],
            "pending": len(result["state"]["pending"])}


# ----------------------------- setup wizard --------------------------------

def _ask(prompt, default):
    raw = input("%s [%s]: " % (prompt, default)).strip()
    return raw if raw else default


def setup_wizard():
    """Interactive Q&A that writes config.json. Safe to re-run anytime."""
    print("\n🏠 BuscPiso Bot setup — Enter keeps the [current] value.\n")
    c = dict(CFG)
    c["max_price"] = int(_ask("Max price (€/month)", c["max_price"]))
    c["min_size"] = int(_ask("Min size (m²)", c["min_size"]))
    c["max_rooms"] = int(_ask("Max bedrooms (0 = studio only)", c["max_rooms"]))
    c["rental_type"] = "long_term" if _ask(
        "Long-term (1+ year) only? y/n", "y" if c["rental_type"] != "any" else "n"
    ).lower().startswith("y") else "any"
    zones = _ask("Zones/barrios, comma-separated (empty = all Barcelona)",
                 ",".join(c["zones"]) or "")
    c["zones"] = [z.strip() for z in zones.split(",") if z.strip()]
    c["require_furnished"] = _ask(
        "Only furnished flats? y/n", "y" if c["require_furnished"] else "n"
    ).lower().startswith("y")
    c["pets_info"] = _ask(
        "Tag listings that allow pets? y/n", "y" if c["pets_info"] else "n"
    ).lower().startswith("y")
    kws = _ask("Extra deal-breaker keywords, comma-separated (e.g. 'sin ascensor')",
               ",".join(c["avoid_keywords"]) or "")
    c["avoid_keywords"] = [k.strip() for k in kws.split(",") if k.strip()]
    path = os.path.join(BASE_DIR, "config.json")
    with open(path, "w") as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
    print("\nSaved to %s — next run uses these settings." % path)
    print("(Cloud runs pick it up after you commit & push config.json.)\n")


# ----------------------------- local entrypoint ----------------------------

def _load_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _main():
    _load_env()
    # STATE_FILE lets the cloud (GitHub Actions) keep its own dedupe memory,
    # separate from the Mac's local state.json.
    state_path = os.environ.get("STATE_FILE") or \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
    args = sys.argv[1:]
    if "--sources" in args:                      # e.g. --sources idealista
        os.environ["SOURCES"] = args[args.index("--sources") + 1]
    if "--setup" in args:
        setup_wizard()
        return
    print("--- run %s ---" % datetime.now().isoformat(timespec="seconds"))
    if "--chat-id" in args:
        chats = {}
        for u in tg_call("getUpdates"):
            # private/group messages, membership changes (bot added to group)
            for k in ("message", "my_chat_member", "channel_post"):
                chat = (u.get(k) or {}).get("chat") or {}
                if chat.get("id"):
                    chats[chat["id"]] = (chat.get("type", "?"),
                                         chat.get("title") or chat.get("first_name") or "")
        if not chats:
            print("No chats found. Message your bot (or add it to a group and "
                  "say something there), then rerun.")
        for cid, (ctype, name) in chats.items():
            print("chat_id = %s  [%s] %s -> TELEGRAM_CHAT_ID" % (cid, ctype, name))
        return
    if "--test" in args:
        tg_send("✅ BCN rental bot connected. Criteria: ≤%d€/mes, ≥%dm², ≤%d hab, long-term only."
                % (MAX_PRICE, MIN_SIZE, MAX_ROOMS))
        print("test message sent")
        return
    dry = "--dry-run" in args
    state = {"seen": {}, "pending": []}
    if not dry and os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    result = run(state, telegram=not dry)
    for line in result["log"]:
        print(line)
    if dry:
        for l in result["state"]["pending"]:
            print("  [%s] %s€ %sm² %shab %s\n      %s"
                  % (l["source"], l["price"], l["size"], l["rooms"],
                     (l["title"] or "")[:70], l["url"]))
    else:
        with open(state_path, "w") as f:
            json.dump(result["state"], f)


if __name__ == "__main__":
    _main()
