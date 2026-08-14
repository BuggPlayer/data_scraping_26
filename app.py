"""
Unified lead-gen tool: scrapes Google Places, enriches with an email guess
from each business's website, and stores everything in SQLite so re-runs
never duplicate or re-contact the same lead. Replaces the three near-
identical standalone scripts now archived under legacy/ (amc_leads.py,
profixer_leads.py, lead_generator_all.py) with one configurable tool.

Run:
    cp .env.example .env   # add your Google Maps API key
    pip install -r requirements.txt
    python3 app.py
    open http://localhost:8000       # scrape leads
    open http://localhost:8000/leads # browse/call/email dashboard
"""

import io
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Dict, List

import pandas as pd
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS

from core import ai_enricher, config, db, email_finder, indiamart_source, osm_source, usage_tracker
from core.categories import CATEGORY_GROUPS, LEGACY_PRESETS, categories_for_groups
from core.locations import COUNTRY_CITY_LISTS, get_locations_by_selection

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)
CORS(app)
db.init_db()

progress = {"status": "idle", "message": "", "percent": 0}

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runs")
os.makedirs(RUNS_DIR, exist_ok=True)


def _make_batch_id(categories: List[str], locations: List[dict]) -> str:
    """One new directory per scrape run: data/runs/<batch_id>/, so each
    run's output stays separate on disk - merge multiple runs together
    later via the batch export endpoints, without losing that separation."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    hint = categories[0] if categories else (locations[0]["city"] if locations else "run")
    slug = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-")[:30]
    return f"{timestamp}_{slug}" if slug else timestamp


def calculate_lead_score(result: dict, biz_name: str, biz_types: list, amc_mode: bool) -> int:
    score = 0
    phone = result.get("formatted_phone_number")
    website = result.get("website")
    address = result.get("formatted_address", "")
    rating = result.get("rating", 0)
    reviews = result.get("user_ratings_total", 0)
    status = result.get("business_status", "")
    name_lower = biz_name.lower()

    if status == "OPERATIONAL":
        score += config.SCORE_OPERATIONAL
    if phone:
        score += config.SCORE_HAS_PHONE
    if not website:
        score += config.SCORE_NO_WEBSITE
    elif any(d in website for d in config.SOCIAL_MEDIA_DOMAINS):
        score += config.SCORE_SOCIAL_MEDIA_ONLY
    if config.REVIEWS_MODERATE_MIN <= reviews <= config.REVIEWS_MODERATE_MAX:
        score += config.SCORE_REVIEWS_MODERATE
    elif reviews > config.REVIEWS_MODERATE_MAX:
        score += config.SCORE_REVIEWS_MANY
    if rating >= config.RATING_GOOD_THRESHOLD:
        score += config.SCORE_RATING_GOOD
    if any(kw in address.lower() for kw in config.COMMERCIAL_ADDRESS_KEYWORDS):
        score += config.SCORE_COMMERCIAL_ADDRESS
    if phone and not website:
        score += config.SCORE_PHONE_NO_WEBSITE_BONUS

    if amc_mode:
        if "amc" in name_lower or "annual maintenance" in name_lower:
            score += config.SCORE_AMC_NAME_MATCH
        if any(t in config.AMC_TYPE_KEYWORDS for t in biz_types):
            score += config.SCORE_AMC_TYPE_MATCH

    return score


def extract_locality(result: dict) -> str:
    components = result.get("address_components", [])
    for wanted in ("sublocality_level_1", "sublocality", "neighborhood", "locality"):
        for comp in components:
            if wanted in comp.get("types", []):
                return comp.get("short_name", "")
    return ""


def _resolve_categories(data: dict) -> List[str]:
    seen = []
    for c in categories_for_groups(data.get("group_keys", [])):
        if c not in seen:
            seen.append(c)
    for c in LEGACY_PRESETS.get(data.get("legacy_preset", ""), []):
        if c not in seen:
            seen.append(c)
    for c in data.get("custom_categories", []):
        c = c.strip()
        if c and c not in seen:
            seen.append(c)
    return seen


def _resolve_locations(data: dict) -> List[dict]:
    locations = get_locations_by_selection(data.get("selected_cities", []))
    for loc in data.get("custom_locations", []):
        loc = loc.strip()
        if loc:
            locations.append({"city": loc, "region": "", "country": "", "query_string": loc})
    return locations


def _resolve_sources(data: dict) -> List[str]:
    sources = [s for s in data.get("sources", ["google"]) if s in ("google", "osm", "indiamart")]
    return sources or ["google"]


def _resolve_max_leads(data: dict):
    """None means no per-run cap (still subject to MAX_QUERIES_PER_RUN)."""
    raw = data.get("max_leads")
    if raw in (None, "", 0, "0"):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def scrape_leads(api_key: str, categories: List[str], locations: List[dict], amc_mode: bool,
                  find_emails: bool, sources: List[str], max_leads=None, batch_id: str = None):
    """max_leads caps Google Places only - it's the source that costs money
    past the free quota. OpenStreetMap and IndiaMART are free, so they
    always collect everything found for the selected categories/locations,
    uncapped (still bounded by MAX_QUERIES_PER_RUN on categories x locations,
    which is a separate politeness/safety cap, not a cost cap)."""
    global progress
    progress.update(status="running", message="Searching...", percent=0)

    session = requests.Session()
    # Each item: place_id/name/category/types/region/country/city, plus either
    # needs_details=True (Google - still needs a Details call) or a fully
    # normalized result dict already in hand (OSM/IndiaMART - already
    # complete, shaped like a Google Details response).
    all_items = []
    seen_ids = set()
    google_count = 0
    google_capped = False
    # IndiaMART has no reliable per-city URL filter - one page load per
    # category returns suppliers across cities, filtered by city in Python
    # afterward. Cache the raw per-category fetch so N selected cities for
    # the same category don't trigger N slow Playwright page loads.
    indiamart_cache = {}
    query_pairs = [(cat, loc) for cat in categories for loc in locations]
    search_steps = len(query_pairs) * len(sources) or 1
    step = 0

    def add_item(item):
        if item["place_id"] not in seen_ids:
            seen_ids.add(item["place_id"])
            all_items.append(item)
            return True
        return False

    try:
        for category, loc in query_pairs:
            if "google" in sources and not google_capped:
                step += 1
                params = {"query": f"{category} in {loc['query_string']}", "key": api_key}
                while True:
                    r = session.get(config.GOOGLE_PLACES_SEARCH_URL, params=params,
                                     timeout=config.PLACE_REQUEST_TIMEOUT_SECONDS)
                    usage_tracker.record_text_search_calls(1)
                    r.raise_for_status()
                    data = r.json()
                    for p in data.get("results", []):
                        if add_item({
                            "place_id": p["place_id"], "name": p["name"],
                            "category": category, "types": p.get("types", []),
                            "region": loc["region"], "country": loc["country"], "city": loc["city"],
                            "needs_details": True, "result": None, "source": "google_maps",
                        }):
                            google_count += 1
                        if max_leads and google_count >= max_leads:
                            google_capped = True
                            break
                    if google_capped:
                        break
                    next_token = data.get("next_page_token")
                    if not next_token:
                        break
                    time.sleep(config.NEXT_PAGE_TOKEN_DELAY_SECONDS)
                    params = {"pagetoken": next_token, "key": api_key}
                progress["percent"] = int((step / search_steps) * 40)
                progress["message"] = f"[Google] Searching '{category}' in {loc['query_string']}... ({google_count} found)"

            if "osm" in sources:
                step += 1
                osm_places = osm_source.search_places(category, loc)
                for p in osm_places:
                    add_item({
                        "place_id": p["place_id"], "name": p["name"],
                        "category": category, "types": [],
                        "region": loc["region"], "country": loc["country"], "city": loc["city"],
                        "needs_details": False, "result": p, "source": "openstreetmap",
                    })
                progress["percent"] = int((step / search_steps) * 40)
                progress["message"] = f"[OpenStreetMap] Searching '{category}' in {loc['query_string']}... ({len(all_items)} found)"

            if "indiamart" in sources:
                step += 1
                if category not in indiamart_cache:
                    progress["message"] = f"[IndiaMART] Loading suppliers for '{category}'..."
                    indiamart_cache[category] = indiamart_source.fetch_category(category)
                raw = indiamart_cache[category]
                city_matches = [r for r in raw if loc["city"].lower() in r["formatted_address"].lower()] if loc.get("city") else raw
                for p in city_matches:
                    add_item({
                        "place_id": p["place_id"], "name": p["name"],
                        "category": category, "types": [],
                        "region": loc["region"], "country": loc["country"], "city": loc["city"],
                        "needs_details": False, "result": p, "source": "indiamart",
                    })
                progress["percent"] = int((step / search_steps) * 40)
                progress["message"] = f"[IndiaMART] Searching '{category}' in {loc['query_string']}... ({len(all_items)} found)"

        unique = list({item["place_id"]: item for item in all_items}.values())
        total = len(unique) or 1
        saved = 0

        for i, item in enumerate(unique):
            source_label = item["source"]
            if item["needs_details"]:
                try:
                    r = session.get(config.GOOGLE_PLACES_DETAIL_URL, params={
                        "place_id": item["place_id"], "fields": ",".join(config.PLACE_DETAIL_FIELDS), "key": api_key,
                    }, timeout=config.PLACE_REQUEST_TIMEOUT_SECONDS)
                    usage_tracker.record_details_calls(1)
                    r.raise_for_status()
                    result = r.json().get("result", {})
                except requests.RequestException as e:
                    logging.error(f"Place details failed for {item['name']}: {e}")
                    result = {}
            else:
                result = item["result"]

            biz_name = result.get("name", item["name"])
            biz_types = result.get("types", item.get("types", []))
            score = calculate_lead_score(result, biz_name, biz_types, amc_mode)
            qualification = ("Hot" if score >= config.HOT_THRESHOLD
                              else "Warm" if score >= config.WARM_THRESHOLD
                              else "Cold")
            website = result.get("website", "")

            email = ""
            if find_emails and website:
                progress["message"] = f"Looking up email for {biz_name}..."
                try:
                    email = email_finder.find_email(website)
                except Exception as e:
                    logging.warning(f"Email lookup failed for {website}: {e}")

            lead_id = db.upsert_lead({
                "place_id": item["place_id"],
                "business_name": biz_name,
                "category": item.get("category", ""),
                "locality": extract_locality(result) or item.get("city", ""),
                "region": item.get("region", ""),
                "country": item.get("country", ""),
                "address": result.get("formatted_address", ""),
                "phone": result.get("formatted_phone_number", ""),
                "website": website,
                "email": email,
                "rating": result.get("rating") or None,
                "reviews": result.get("user_ratings_total") or None,
                "business_status": result.get("business_status", ""),
                "maps_url": result.get("url", ""),
                "lead_score": score,
                "qualification": qualification,
                "source": source_label,
                "batch_id": batch_id or "",
            })
            saved += 1

            progress["percent"] = 40 + int((i + 1) / total * 60)
            progress["message"] = f"Saved {saved}/{total}: {biz_name}"
            time.sleep(config.PER_LEAD_THROTTLE_SECONDS)

        if batch_id:
            _finalize_batch(batch_id)

        progress.update(status="completed", percent=100, message=f"Done. {saved} leads processed, stored in data/leads.db")

    except Exception as e:
        progress.update(status="error", message=str(e))


def _finalize_batch(batch_id: str):
    """Writes this run's own snapshot export into data/runs/<batch_id>/ and
    records the final lead count - the 'new directory per scrape' this
    batch actually produced (leads found by an earlier run and merely
    re-touched this run don't count as newly discovered by it)."""
    rows = db.export_rows(include_do_not_contact=True, batch_ids=[batch_id])
    batch_dir = os.path.join(RUNS_DIR, batch_id)
    os.makedirs(batch_dir, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_excel(os.path.join(batch_dir, "leads.xlsx"), index=False)
    db.set_batch_lead_count(batch_id, len(rows))


@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.json or {}
    categories = _resolve_categories(data)
    locations = _resolve_locations(data)
    sources = _resolve_sources(data)
    max_leads = _resolve_max_leads(data)
    total_queries = len(categories) * len(locations)
    low, high = config.ESTIMATE_LOW_MULTIPLIER, config.ESTIMATE_HIGH_MULTIPLIER

    use_google = "google" in sources
    use_osm = "osm" in sources
    use_indiamart = "indiamart" in sources

    # max_leads caps Google only (it's the source that costs money) - free
    # sources are never capped, so their estimate contribution ignores it.
    google_businesses_low = google_businesses_high = 0
    if use_google:
        google_businesses_point = total_queries * config.AVG_RESULTS_PER_QUERY
        google_businesses_low = google_businesses_point * low
        google_businesses_high = google_businesses_point * high
        if max_leads:
            google_businesses_low = min(google_businesses_low, max_leads)
            google_businesses_high = min(google_businesses_high, max_leads)

    free_businesses_point = 0
    if use_osm:
        free_businesses_point += total_queries * config.OSM_AVG_RESULTS_PER_QUERY
    if use_indiamart:
        free_businesses_point += total_queries * config.INDIAMART_AVG_RESULTS_PER_QUERY

    businesses_low = google_businesses_low + free_businesses_point * low
    businesses_high = google_businesses_high + free_businesses_point * high

    cost_low = cost_high = 0.0
    quota = None
    if use_google:
        search_requests_point = total_queries * config.AVG_PAGES_PER_QUERY
        # A max_leads cap makes Google's search stop early once enough are
        # found, so the search-request volume shrinks roughly proportionally
        # to how much the business count itself got capped.
        uncapped_low = total_queries * config.AVG_RESULTS_PER_QUERY * low
        uncapped_high = total_queries * config.AVG_RESULTS_PER_QUERY * high
        cap_ratio_low = min(1.0, google_businesses_low / uncapped_low) if uncapped_low > 0 else 1.0
        cap_ratio_high = min(1.0, google_businesses_high / uncapped_high) if uncapped_high > 0 else 1.0

        usage = usage_tracker.get_usage()
        search_free_left = usage["text_search_free_remaining"]
        details_free_left = usage["details_free_remaining"]
        quota = {
            "month": usage["month"],
            "text_search_free_remaining": search_free_left,
            "details_free_remaining": details_free_left,
        }

        def cost(search_requests, business_count):
            billable_search = max(0, search_requests - search_free_left)
            billable_details = max(0, business_count - details_free_left)
            return (billable_search / 1000 * config.TEXT_SEARCH_COST_PER_1000
                    + billable_details / 1000 * config.DETAILS_COST_PER_1000)

        cost_low = cost(search_requests_point * low * cap_ratio_low, google_businesses_low)
        cost_high = cost(search_requests_point * high * cap_ratio_high, google_businesses_high)

    free_source_names = []
    if use_osm:
        free_source_names.append("OpenStreetMap")
    if use_indiamart:
        free_source_names.append("IndiaMART")

    if use_google:
        note = ("Estimate accounts for your remaining free monthly Google Places quota "
                "(5,000 Text Search + 5,000 Place Details/month, Pro SKU) based on calls "
                "this app has already made this month. Verify against your actual Google "
                "Cloud billing page before relying on it.")
        if free_source_names:
            note += f" {' and '.join(free_source_names)} results (also included above) are free."
    elif free_source_names:
        verb = "is" if len(free_source_names) == 1 else "are"
        note = f"{' and '.join(free_source_names)} {verb} free - no quota, no API cost."
    else:
        note = ""

    return jsonify({
        "categories": len(categories),
        "locations": len(locations),
        "sources": sources,
        "total_queries": total_queries,
        "max_queries_allowed": config.MAX_QUERIES_PER_RUN,
        "within_cap": total_queries <= config.MAX_QUERIES_PER_RUN,
        "estimated_businesses_low": int(businesses_low),
        "estimated_businesses_high": int(businesses_high),
        "estimated_cost_low": round(cost_low, 2),
        "estimated_cost_high": round(cost_high, 2),
        "quota": quota,
        "note": note,
    })


@app.route("/start", methods=["POST"])
def start_scrape():
    data = request.json or {}
    sources = _resolve_sources(data)

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if "google" in sources and not api_key:
        return jsonify({"error": "GOOGLE_MAPS_API_KEY not set on the server (.env). "
                                  "Uncheck Google Places, or add a key, to continue."}), 400

    categories = _resolve_categories(data)
    locations = _resolve_locations(data)
    if not categories:
        return jsonify({"error": "Select at least one category group, legacy preset, or custom category"}), 400
    if not locations:
        return jsonify({"error": "Select at least one country or custom location"}), 400

    total_queries = len(categories) * len(locations)
    if total_queries > config.MAX_QUERIES_PER_RUN:
        return jsonify({
            "error": f"This run would make {total_queries} searches, over the {config.MAX_QUERIES_PER_RUN}-query "
                     f"safety cap (set MAX_QUERIES_PER_RUN in .env to raise it). Narrow your categories/countries, "
                     f"or raise the cap if you really mean to run this big."
        }), 400

    find_emails = bool(data.get("find_emails", True))
    amc_mode = data.get("legacy_preset") == "amc"
    max_leads = _resolve_max_leads(data)

    if progress["status"] == "running":
        return jsonify({"error": "A scrape is already running"}), 409

    batch_id = _make_batch_id(categories, locations)
    db.create_batch(
        batch_id=batch_id,
        label=f"{categories[0]}{' +' + str(len(categories) - 1) if len(categories) > 1 else ''} "
              f"in {locations[0]['city']}{' +' + str(len(locations) - 1) if len(locations) > 1 else ''}",
        sources=",".join(sources),
        categories_summary=", ".join(categories[:5]) + ("..." if len(categories) > 5 else ""),
        locations_summary=", ".join(loc["city"] for loc in locations[:5]) + ("..." if len(locations) > 5 else ""),
        output_dir=os.path.join("data", "runs", batch_id),
    )

    thread = threading.Thread(
        target=scrape_leads,
        args=(api_key, categories, locations, amc_mode, find_emails, sources, max_leads, batch_id),
        daemon=True,
    )
    thread.start()
    return jsonify({"message": "Scraping started", "total_queries": total_queries,
                     "max_leads": max_leads, "batch_id": batch_id})


@app.route("/progress")
def get_progress():
    return jsonify(progress)


@app.route("/leads")
def leads_dashboard():
    return render_template("dashboard.html")


@app.route("/api/leads")
def list_leads():
    rows = db.export_rows(include_do_not_contact=True)
    return jsonify(rows)


@app.route("/api/batches")
def list_batches():
    return jsonify(db.get_batches())


@app.route("/api/leads/<int:lead_id>/status", methods=["POST"])
def update_status(lead_id):
    data = request.json or {}
    if "call_status" in data:
        db.set_call_status(lead_id, data["call_status"], data.get("call_notes", ""))
    if "email_status" in data:
        db.set_email_status(lead_id, data["email_status"])
    if data.get("do_not_contact"):
        db.set_do_not_contact(lead_id, data.get("reason", ""))
    return jsonify({"ok": True})


@app.route("/api/ai/status")
def ai_status():
    return jsonify({"available": ai_enricher.is_available(), "model": config.OLLAMA_MODEL})


@app.route("/api/leads/<int:lead_id>/generate-pitch", methods=["POST"])
def generate_pitch(lead_id):
    lead = db.get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    pitch = ai_enricher.generate_pitch(lead)
    if not pitch:
        return jsonify({
            "error": "Couldn't reach Ollama. Make sure it's running locally "
                     f"(`ollama serve`) with the model pulled (`ollama pull {config.OLLAMA_MODEL}`)."
        }), 503

    db.set_ai_pitch(lead_id, pitch)
    return jsonify({"pitch": pitch})


def _export(rows: List[Dict], filename: str):
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _batch_ids_from_request():
    raw = request.args.get("batches", "")
    ids = [b.strip() for b in raw.split(",") if b.strip()]
    return ids or None


@app.route("/export/calls")
def export_calls():
    country = request.args.get("country") or None
    return _export(db.export_rows(callable_only=True, country=country, batch_ids=_batch_ids_from_request()), "call_list.xlsx")


@app.route("/export/emails")
def export_emails():
    country = request.args.get("country") or None
    return _export(db.export_rows(emailable_only=True, country=country, batch_ids=_batch_ids_from_request()), "email_list.xlsx")


@app.route("/export/all")
def export_all():
    country = request.args.get("country") or None
    return _export(db.export_rows(country=country, batch_ids=_batch_ids_from_request()), "all_leads.xlsx")


@app.route("/")
def index():
    return render_template(
        "index.html",
        category_groups=CATEGORY_GROUPS,
        legacy_presets=LEGACY_PRESETS,
        country_cities=COUNTRY_CITY_LISTS,
        max_queries=config.MAX_QUERIES_PER_RUN,
    )


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=8000)
