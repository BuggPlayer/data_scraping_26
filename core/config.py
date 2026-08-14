"""
All tunable constants live here - scoring weights, API endpoints, safety
caps, cost-estimate assumptions, email-finder settings. Nothing in this
file has logic; app.py/db.py/email_finder.py import from here instead of
hardcoding values inline, so you can tune behavior without touching logic.
"""

import os

# --- Google Places API ---
GOOGLE_PLACES_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_PLACES_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"
PLACE_DETAIL_FIELDS = [
    "name", "formatted_phone_number", "website", "formatted_address",
    "rating", "user_ratings_total", "business_status", "url",
    "address_components", "types",
]
PLACE_REQUEST_TIMEOUT_SECONDS = 15
NEXT_PAGE_TOKEN_DELAY_SECONDS = 2   # Google requires a short delay before a page token becomes valid
PER_LEAD_THROTTLE_SECONDS = 0.1

# --- Safety cap on run size (categories x locations) ---
MAX_QUERIES_PER_RUN = int(os.environ.get("MAX_QUERIES_PER_RUN", "400"))

# --- Cost-estimate assumptions ---
# Confirmed against Google's published Places API (Legacy) pricing docs
# (2026-08-14): both endpoints bill under the Pro SKU tier because
# PLACE_DETAIL_FIELDS requests contact-data fields (phone, website).
# Verify against your actual Google Cloud Console billing page if in doubt -
# Google can change these rates.
TEXT_SEARCH_COST_PER_1000 = 32.0
DETAILS_COST_PER_1000 = 17.0
# Free monthly quota per SKU, Pro tier. Renews every calendar month (not a
# one-time signup credit) - the old $200/month universal credit ended
# 2025-02-28 and no longer applies.
TEXT_SEARCH_FREE_QUOTA_MONTHLY = 5000
DETAILS_FREE_QUOTA_MONTHLY = 5000
AVG_PAGES_PER_QUERY = 1.5        # most category/city searches don't fill all 3 pages
AVG_RESULTS_PER_QUERY = 15       # rough average unique businesses per (category, city) search
ESTIMATE_LOW_MULTIPLIER = 0.4
ESTIMATE_HIGH_MULTIPLIER = 1.2

# --- OpenStreetMap (free source: no API key, no quota, no cost) ---
OSM_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSM_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_USER_AGENT = "LeadGenTool/1.0 (local lead research tool)"
# Lower than Google's assumption - OSM's volunteer-mapped tagging is
# generally sparser for small local businesses, especially outside the US/AU.
OSM_AVG_RESULTS_PER_QUERY = 6

# --- IndiaMART (free public category pages, India-only, product-keyword search) ---
# One page load returns ~20-30 suppliers total across whatever cities are
# shown; after filtering to one specific city the yield is much smaller.
INDIAMART_AVG_RESULTS_PER_QUERY = 4

# --- Lead scoring weights ---
SCORE_OPERATIONAL = 10
SCORE_HAS_PHONE = 10
SCORE_NO_WEBSITE = 20
SCORE_SOCIAL_MEDIA_ONLY = 15          # website exists but is just a Facebook/Instagram page
SCORE_REVIEWS_MODERATE = 10           # REVIEWS_MODERATE_MIN <= reviews <= REVIEWS_MODERATE_MAX
SCORE_REVIEWS_MANY = 5                # reviews > REVIEWS_MODERATE_MAX
SCORE_RATING_GOOD = 5                 # rating >= RATING_GOOD_THRESHOLD
SCORE_COMMERCIAL_ADDRESS = 5
SCORE_PHONE_NO_WEBSITE_BONUS = 25     # strongest buy signal: reachable but no site at all
SCORE_AMC_NAME_MATCH = 15
SCORE_AMC_TYPE_MATCH = 10

REVIEWS_MODERATE_MIN = 3
REVIEWS_MODERATE_MAX = 20
RATING_GOOD_THRESHOLD = 4.0
COMMERCIAL_ADDRESS_KEYWORDS = ("shop", "commercial", "office")
SOCIAL_MEDIA_DOMAINS = ("facebook.com", "instagram.com")
AMC_TYPE_KEYWORDS = {"electronics_repair", "appliance_repair", "home_goods_store", "electrician", "plumber"}

HOT_THRESHOLD = 45
WARM_THRESHOLD = 30

# --- Email finder ---
EMAIL_FINDER_USER_AGENT = "Mozilla/5.0 (compatible; LeadResearchBot/1.0; +mailto:you@yourcompany.com)"
EMAIL_FINDER_TIMEOUT_SECONDS = 8
EMAIL_FINDER_CANDIDATE_PATHS = ("", "/contact", "/contact-us", "/about", "/about-us")
EMAIL_JUNK_DOMAINS = ("wixpress.com", "sentry.io", "example.com", "godaddy.com", "yourdomain.com")
EMAIL_ROLE_PREFIXES = ("info", "contact", "sales", "hello", "support", "enquiry", "enquiries")

# --- Business-name normalization (used to match LinkedIn CSV imports to scraped leads) ---
BUSINESS_NAME_JUNK_TOKENS = (" pvt", " ltd", " llc", " inc", ".", ",", "  ")

# --- AI enrichment (Ollama, local/free) ---
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
OLLAMA_TIMEOUT_SECONDS = 30
