"""
Free business discovery via OpenStreetMap - no API key, no quota, no cost.
Uses Nominatim to geocode a city name to a bounding box, then Overpass to
query tagged businesses within that box.

Tradeoffs vs Google Places (be honest with yourself about these before
relying on OSM-only data):
  - No ratings or review counts at all - OSM doesn't track that.
  - Coverage is volunteer-mapped, not business-claimed, so completeness
    varies a lot by region: often solid in US/Australia metro areas,
    noticeably sparser in India, and phone/website tags are frequently
    just missing even when the business itself is mapped.
  - Category coverage depends on OSM's tagging schema having a matching
    tag; where it doesn't (see CATEGORY_OSM_TAGS below), this falls back
    to a name-keyword search, which is noisier.

Results are normalized into the same shape Google's Place Details
response uses (formatted_phone_number, website, formatted_address,
rating, user_ratings_total, business_status) so the rest of the app's
scoring/dedup/storage pipeline doesn't need to know which source a lead
came from.
"""

import logging
import threading
import time

import requests

from core import config

# Maps our category strings (core/categories.py) to OpenStreetMap tags.
# Categories with no clean OSM equivalent (e.g. "AC repair", "Pest control",
# "Home cleaning service") aren't listed here and rely entirely on the
# name-keyword fallback in _build_overpass_query.
CATEGORY_OSM_TAGS = {
    "Real estate agency": [("office", "estate_agent")],
    "Law firm": [("office", "lawyer")],
    "Accounting firm": [("office", "accountant")],
    "Insurance agency": [("office", "insurance")],
    "Financial advisor": [("office", "financial_advisor")],
    "Tax consultant": [("office", "tax_advisor")],
    "Dental clinic": [("amenity", "dentist")],
    "Medical clinic": [("amenity", "clinic"), ("amenity", "doctors")],
    "Physiotherapy clinic": [("healthcare", "physiotherapist")],
    "Hair salon": [("shop", "hairdresser")],
    "Spa": [("leisure", "spa"), ("shop", "beauty")],
    "Gym": [("leisure", "fitness_centre")],
    "Chiropractor": [("healthcare", "chiropractor")],
    "Clothing store": [("shop", "clothes")],
    "Restaurant": [("amenity", "restaurant")],
    "Cafe": [("amenity", "cafe")],
    "Bakery": [("shop", "bakery")],
    "Grocery store": [("shop", "supermarket"), ("shop", "grocery")],
    "Furniture store": [("shop", "furniture")],
    "Jewelry store": [("shop", "jewelry")],
    "Electrician": [("craft", "electrician")],
    "Plumber": [("craft", "plumber")],
    "Painter": [("craft", "painter")],
    "Carpenter": [("craft", "carpenter")],
    "Roofing contractor": [("craft", "roofer")],
    "Driving school": [("amenity", "driving_school")],
    "Auto repair shop": [("shop", "car_repair")],
    "Car dealer": [("shop", "car")],
    "Tire shop": [("shop", "tyres")],
    "Car wash": [("amenity", "car_wash")],
    "Auto parts store": [("shop", "car_parts")],
    "Banquet hall": [("amenity", "events_venue")],
    "Catering service": [("craft", "caterer")],
    "Party supply store": [("shop", "party")],
    "Web design agency": [("office", "it")],
    "Graphic design studio": [("office", "graphic_design")],
    "Marketing agency": [("office", "advertising_agency")],
    "Photography studio": [("shop", "photo")],
}

_geocode_cache = {}
_geocode_lock = threading.Lock()
_last_nominatim_call = [0.0]
_last_overpass_call = [0.0]

OVERPASS_RETRYABLE_STATUS = {429, 502, 503, 504}


def _throttle(last_call_holder, min_interval):
    """Both Nominatim and Overpass's public instances ask that free/anonymous
    use stay well under 1 request/second - be a good citizen."""
    elapsed = time.time() - last_call_holder[0]
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    last_call_holder[0] = time.time()


def geocode_location(query_string: str):
    """Returns (south, west, north, east) bounding box, or None if not
    found/unreachable. Cached in-process since the same city list is
    reused across every category in a run."""
    with _geocode_lock:
        if query_string in _geocode_cache:
            return _geocode_cache[query_string]

        _throttle(_last_nominatim_call, 1.0)
        try:
            resp = requests.get(
                config.OSM_NOMINATIM_URL,
                params={"q": query_string, "format": "json", "limit": 1},
                headers={"User-Agent": config.OSM_USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json()
        except requests.RequestException as e:
            logging.warning(f"Nominatim geocoding failed for '{query_string}': {e}")
            _geocode_cache[query_string] = None
            return None

        if not results or not results[0].get("boundingbox"):
            _geocode_cache[query_string] = None
            return None

        south, north, west, east = (float(x) for x in results[0]["boundingbox"])
        bbox = (south, west, north, east)
        _geocode_cache[query_string] = bbox
        return bbox


def _build_overpass_query(category: str, bbox) -> str:
    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"
    clauses = [f'nwr["{key}"="{value}"]({bbox_str});' for key, value in CATEGORY_OSM_TAGS.get(category, [])]

    name_keyword = category.split()[0].lower()
    clauses.append(f'nwr["name"~"{name_keyword}",i]({bbox_str});')

    body = "\n  ".join(clauses)
    return f"[out:json][timeout:25];\n(\n  {body}\n);\nout center tags;"


def _query_overpass(query: str, context: str) -> list:
    """POSTs to Overpass with retry-with-backoff on transient failures
    (502/503/504/429, or a request exception) - Overpass's shared public
    instance returns these fairly often under load, and they almost always
    succeed on a retry a few seconds later. Returns [] only after
    exhausting retries."""
    max_retries = config.OSM_OVERPASS_MAX_RETRIES
    for attempt in range(max_retries + 1):
        _throttle(_last_overpass_call, 1.0)
        try:
            resp = requests.post(
                config.OSM_OVERPASS_URL,
                data={"data": query},
                headers={"User-Agent": config.OSM_USER_AGENT},
                timeout=30,
            )
            if resp.status_code in OVERPASS_RETRYABLE_STATUS and attempt < max_retries:
                wait = config.OSM_OVERPASS_RETRY_BACKOFF_SECONDS * (attempt + 1)
                logging.warning(f"Overpass returned {resp.status_code} for {context}, "
                                f"retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except requests.RequestException as e:
            if attempt < max_retries:
                wait = config.OSM_OVERPASS_RETRY_BACKOFF_SECONDS * (attempt + 1)
                logging.warning(f"Overpass request failed for {context} ({e}), "
                                f"retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(wait)
                continue
            logging.warning(f"Overpass query failed for {context} after {max_retries} retries: {e}")
            return []
    return []


def search_places(category: str, location: dict, max_results: int = 60):
    """Returns a list of dicts already shaped like a Google Place Details
    'result' (formatted_phone_number, website, formatted_address, rating,
    user_ratings_total, business_status) so the rest of the pipeline can
    treat OSM and Google results identically. Returns [] on any failure -
    a bad geocode or a flaky Overpass response (after retries) shouldn't
    crash the run."""
    bbox = geocode_location(location["query_string"])
    if not bbox:
        return []

    query = _build_overpass_query(category, bbox)
    elements = _query_overpass(query, context=f"'{category}' in {location['query_string']}")

    places = []
    for el in elements[:max_results]:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        osm_type = el.get("type", "node")
        osm_id = el.get("id")
        address_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:suburb", "") or location.get("city", ""),
        ]
        address = " ".join(p for p in address_parts if p).strip() or location.get("city", "")

        places.append({
            "place_id": f"osm:{osm_type}:{osm_id}",
            "name": name,
            "formatted_phone_number": tags.get("phone") or tags.get("contact:phone") or "",
            "website": tags.get("website") or tags.get("contact:website") or "",
            "formatted_address": address,
            "rating": 0,
            "user_ratings_total": 0,
            "business_status": "",
            "url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
            "types": [],
        })
    return places
