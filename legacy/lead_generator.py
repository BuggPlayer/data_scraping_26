import os
import time
import logging
from typing import Dict, List

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------
# CONFIG
# -----------------------

load_dotenv()

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")
if not API_KEY:
    raise SystemExit(
        "GOOGLE_MAPS_API_KEY not set. Copy .env.example to .env and add your key."
    )

# Edit these as you like – add multiple search queries if needed
QUERIES = [
    "Electrician in Mira Road Mumbai",
    # "Electrical contractor Mira Road Mumbai",
    # "Electrical repair Mira Road Mumbai",
]

OUTPUT = "business_leads.xlsx"

DETAIL_FIELDS = [
    "name",
    "formatted_phone_number",
    "website",
    "formatted_address",
    "rating",
    "user_ratings_total",
    "business_status",
    "url"
]

# -----------------------
# LOGGING
# -----------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# -----------------------
# HTTP SESSION WITH RETRIES
# -----------------------

session = requests.Session()

retry = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504]
)

adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)

# -----------------------
# HELPER: fetch places for one query
# -----------------------

SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"

def search_places(query: str) -> List[Dict]:
    """Return list of dicts with 'place_id' and 'name' for a given query."""
    places = []
    params = {"query": query, "key": API_KEY}

    while True:
        logging.info(f"Searching page with token: {params.get('pagetoken', 'none')}")
        r = session.get(SEARCH_URL, params=params)
        r.raise_for_status()
        data = r.json()

        for place in data.get("results", []):
            places.append({
                "place_id": place.get("place_id"),
                "name": place.get("name")
            })

        next_token = data.get("next_page_token")
        if not next_token:
            break

        # Mandatory delay before using next_page_token
        time.sleep(2)
        params = {"pagetoken": next_token, "key": API_KEY}

    return places

# -----------------------
# SEARCH ALL QUERIES
# -----------------------

all_places = []

for query in QUERIES:
    logging.info(f"Searching for: {query}")
    all_places.extend(search_places(query))

# -----------------------
# REMOVE DUPLICATES
# -----------------------

unique = {}
for place in all_places:
    if place["place_id"] not in unique:
        unique[place["place_id"]] = place

unique = list(unique.values())
logging.info(f"Unique businesses found: {len(unique)}")

# -----------------------
# FETCH DETAILS
# -----------------------

rows = []

for i, business in enumerate(unique, start=1):
    logging.info(f"({i}/{len(unique)}) Fetching details for: {business['name']}")

    try:
        r = session.get(
            DETAIL_URL,
            params={
                "place_id": business["place_id"],
                "fields": ",".join(DETAIL_FIELDS),
                "key": API_KEY
            }
        )
        r.raise_for_status()
        result = r.json().get("result", {})
    except Exception as e:
        logging.error(f"Failed to fetch {business['place_id']}: {e}")
        result = {}

    rows.append({
        "Business Name": result.get("name"),
        "Address": result.get("formatted_address"),
        "Phone": result.get("formatted_phone_number"),
        "Website": result.get("website"),
        "Rating": result.get("rating"),
        "Reviews": result.get("user_ratings_total"),
        "Status": result.get("business_status"),
        "Google Maps": result.get("url"),
    })

    # Gentle rate limiting – avoid hitting per‑second quotas
    time.sleep(0.1)

# -----------------------
# EXPORT TO EXCEL
# -----------------------

df = pd.DataFrame(rows)
df.to_excel(OUTPUT, index=False)
logging.info(f"Done! Saved {len(df)} leads to {OUTPUT}")