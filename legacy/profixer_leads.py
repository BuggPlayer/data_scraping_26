import os
import time
import logging
import threading
import json
from typing import Dict, List

import pandas as pd
import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

# -----------------------
# FLASK APP SETUP
# -----------------------
app = Flask(__name__)
CORS(app)

progress = {
    "status": "idle",
    "message": "",
    "percent": 0,
    "file_ready": False,
    "output_file": "profixer_leads.xlsx"
}

# -----------------------
# LEAD SCORING ENGINE
# -----------------------
def calculate_lead_score(result: dict) -> int:
    score = 0
    phone = result.get("formatted_phone_number")
    website = result.get("website")
    address = result.get("formatted_address", "")
    rating = result.get("rating", 0)
    reviews = result.get("user_ratings_total", 0)
    status = result.get("business_status", "")

    if status == "OPERATIONAL":
        score += 10
    if phone:
        score += 10
    if not website:
        score += 20
    elif website and ("facebook.com" in website or "instagram.com" in website):
        score += 15
    if 3 <= reviews <= 20:
        score += 10
    elif reviews > 20:
        score += 5
    if rating >= 4.0:
        score += 5
    address_lower = address.lower()
    if any(kw in address_lower for kw in ["shop", "commercial", "office"]):
        score += 5
    if phone and not website:
        score += 25
    return score

# -----------------------
# EXTRACT LOCALITY FROM ADDRESS COMPONENTS
# -----------------------
def extract_locality(result: dict) -> str:
    components = result.get("address_components", [])
    # Priority: sublocality_level_1 > sublocality > neighborhood > locality
    for comp in components:
        types = comp.get("types", [])
        if "sublocality_level_1" in types:
            return comp.get("short_name", "")
    for comp in components:
        types = comp.get("types", [])
        if "sublocality" in types:
            return comp.get("short_name", "")
    for comp in components:
        types = comp.get("types", [])
        if "neighborhood" in types:
            return comp.get("short_name", "")
    # Fallback to city/town
    for comp in components:
        types = comp.get("types", [])
        if "locality" in types:
            return comp.get("short_name", "")
    return ""

# -----------------------
# SCRAPING LOGIC
# -----------------------
def scrape_leads(api_key: str, queries: List[str], location: str):
    global progress
    progress["status"] = "running"
    progress["message"] = "Searching..."
    progress["percent"] = 0
    progress["file_ready"] = False

    SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"
    DETAIL_FIELDS = [
        "name", "formatted_phone_number", "website", "formatted_address",
        "rating", "user_ratings_total", "business_status", "url",
        "address_components"          # <-- added for locality extraction
    ]

    session = requests.Session()
    all_places = []
    total_queries = len(queries)

    try:
        # Step 1: Search all queries and remember category
        for idx, query in enumerate(queries):
            full_query = f"{query} in {location}"
            params = {"query": full_query, "key": api_key}
            page = 0
            while True:
                r = session.get(SEARCH_URL, params=params)
                r.raise_for_status()
                data = r.json()
                places = data.get("results", [])
                # Store the category (the query itself) with each place
                all_places.extend([
                    {"place_id": p["place_id"], "name": p["name"], "category": query}
                    for p in places
                ])
                next_token = data.get("next_page_token")
                if not next_token:
                    break
                time.sleep(2)
                params = {"pagetoken": next_token, "key": api_key}
                page += 1
                progress["percent"] = int(((idx + 1) / total_queries) * 50)
                progress["message"] = f"Searching '{query}' (page {page})..."

        # Remove duplicates (keep first seen, which preserves category)
        unique = list({p["place_id"]: p for p in all_places}.values())
        total = len(unique)
        progress["percent"] = 50
        progress["message"] = f"Found {total} unique businesses. Fetching details..."

        # Step 2: Fetch details and score
        rows = []
        for i, biz in enumerate(unique):
            try:
                r = session.get(DETAIL_URL, params={
                    "place_id": biz["place_id"],
                    "fields": ",".join(DETAIL_FIELDS),
                    "key": api_key
                })
                r.raise_for_status()
                result = r.json().get("result", {})
            except Exception as e:
                result = {}
                logging.error(f"Error: {e}")

            score = calculate_lead_score(result)
            qualification = "Hot" if score >= 45 else ("Warm" if score >= 30 else "Cold")
            locality = extract_locality(result)

            rows.append({
                "First Name": "",                        # not available from API
                "Last Name": "",                         # not available from API
                "Email": "",                             # not available (optionally scrape later)
                "Locality": locality,
                "Service Category": biz.get("category", ""),
                "Business Name": result.get("name", biz.get("name")),
                "Address": result.get("formatted_address"),
                "Phone": result.get("formatted_phone_number"),
                "Website": result.get("website"),
                "Rating": result.get("rating"),
                "Reviews": result.get("user_ratings_total"),
                "Status": result.get("business_status"),
                "Google Maps": result.get("url"),
                "Lead Score": score,
                "Qualification": qualification
            })

            progress["percent"] = 50 + int((i+1)/total * 45)
            progress["message"] = f"Processing {i+1}/{total}: {result.get('name', biz.get('name'))}"
            time.sleep(0.1)

        df = pd.DataFrame(rows)
        df.to_excel(progress["output_file"], index=False)

        progress["percent"] = 100
        progress["message"] = f"Done! {len(rows)} leads exported."
        progress["file_ready"] = True
        progress["status"] = "completed"

    except Exception as e:
        progress["status"] = "error"
        progress["message"] = f"Error: {str(e)}"
        progress["file_ready"] = False

# -----------------------
# ROUTES
# -----------------------
@app.route("/start", methods=["POST"])
def start_scrape():
    data = request.json
    api_key = data.get("api_key", "").strip()
    location = data.get("location", "Mira Road Mumbai")
    categories = data.get("categories", [])

    if not api_key:
        return jsonify({"error": "API key is required"}), 400

    if not categories:
        categories = [
            "Electrician", "Plumber", "AC repair", "Painter",
            "Carpenter", "Home cleaning service", "Pest control",
            "Roofing contractor", "Water purifier repair",
            "Electrical contractor", "Bathroom renovation",
            "Kitchen renovation", "Handyman", "TV repair"
        ]

    progress["status"] = "starting"
    progress["percent"] = 0
    progress["message"] = "Starting..."
    progress["file_ready"] = False

    thread = threading.Thread(target=scrape_leads, args=(api_key, categories, location))
    thread.daemon = True
    thread.start()

    return jsonify({"message": "Scraping started"})

@app.route("/progress")
def get_progress():
    return jsonify(progress)

@app.route("/download")
def download_file():
    if progress["file_ready"]:
        return send_file(progress["output_file"], as_attachment=True)
    else:
        return "File not ready", 404

# -----------------------
# INLINE HTML TEMPLATE
# -----------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ProFixer Lead Generator</title>
  <style>
    :root { --primary: #2d89ef; --bg: #f4f7fc; --card: white; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); display: flex; justify-content: center; align-items: center; min-height: 100vh; }
    .container { max-width: 700px; width: 90%; margin: 20px; }
    .card { background: var(--card); border-radius: 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.08); padding: 30px; }
    h1 { color: #1e293b; font-size: 2rem; margin-bottom: 8px; }
    h1 span { color: var(--primary); }
    p.sub { color: #64748b; margin-bottom: 24px; }
    label { font-weight: 600; display: block; margin: 12px 0 6px; color: #334155; }
    input, textarea { width: 100%; padding: 10px 14px; border: 2px solid #e2e8f0; border-radius: 10px; font-size: 1rem; transition: border .2s; }
    input:focus, textarea:focus { border-color: var(--primary); outline: none; }
    .btn { background: var(--primary); color: white; border: none; padding: 12px 28px; border-radius: 10px; font-size: 1rem; font-weight: 600; cursor: pointer; transition: .2s; margin-top: 20px; width: 100%; }
    .btn:hover { background: #1b6bc1; }
    .btn:disabled { background: #94a3b8; cursor: not-allowed; }
    .progress-container { display: none; margin: 24px 0 0; }
    .progress-bar { height: 12px; background: #e2e8f0; border-radius: 10px; overflow: hidden; margin-bottom: 10px; }
    .progress-fill { height: 100%; width: 0%; background: var(--primary); border-radius: 10px; transition: width 0.3s; }
    .status-message { font-weight: 500; color: #475569; }
    .download-section { display: none; margin-top: 20px; }
    .download-btn { background: #059669; }
    .download-btn:hover { background: #047857; }
    .error { color: #dc2626; background: #fee2e2; padding: 10px; border-radius: 8px; }
  </style>
</head>
<body>
<div class="container">
  <div class="card">
    <h1>ProFixer <span>Lead Generator</span></h1>
    <p class="sub">Generate qualified sales leads for home service businesses.</p>

    <label for="api_key">Google API Key *</label>
    <input type="text" id="api_key" placeholder="AIzaSy...">

    <label for="location">Location (city / area)</label>
    <input type="text" id="location" value="Mira Road Mumbai">

    <label for="categories">Categories (one per line)</label>
    <textarea id="categories" rows="5">Electrician
Plumber
AC repair
Painter
Carpenter
Home cleaning service
Pest control
Roofing contractor
Water purifier repair
Electrical contractor
Bathroom renovation
Kitchen renovation
Handyman
TV repair</textarea>

    <button id="start-btn" class="btn" onclick="startScraping()">Start Scraping</button>

    <div id="progress-box" class="progress-container">
      <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
      <div class="status-message" id="status-message"></div>
    </div>

    <div id="download-box" class="download-section">
      <button class="btn download-btn" onclick="downloadFile()">Download Excel File</button>
    </div>
    <div id="error-box" class="error" style="display: none;"></div>
  </div>
</div>

<script>
  let polling = null;

  async function startScraping() {
    const apiKey = document.getElementById('api_key').value.trim();
    if (!apiKey) { alert('Please enter your API key'); return; }

    const location = document.getElementById('location').value.trim() || 'Mira Road Mumbai';
    const categories = document.getElementById('categories').value.split('\\n').filter(c => c.trim() !== '');

    document.getElementById('start-btn').disabled = true;
    document.getElementById('progress-box').style.display = 'block';
    document.getElementById('download-box').style.display = 'none';
    document.getElementById('error-box').style.display = 'none';

    try {
      const res = await fetch('/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ api_key: apiKey, location, categories })
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.error || 'Failed to start');
      }

      polling = setInterval(checkProgress, 1000);
    } catch (e) {
      document.getElementById('error-box').style.display = 'block';
      document.getElementById('error-box').textContent = e.message;
      document.getElementById('start-btn').disabled = false;
    }
  }

  async function checkProgress() {
    try {
      const res = await fetch('/progress');
      const data = await res.json();
      document.getElementById('progress-fill').style.width = data.percent + '%';
      document.getElementById('status-message').textContent = data.message;

      if (data.status === 'completed') {
        clearInterval(polling);
        document.getElementById('start-btn').disabled = false;
        document.getElementById('download-box').style.display = 'block';
      } else if (data.status === 'error') {
        clearInterval(polling);
        document.getElementById('start-btn').disabled = false;
        document.getElementById('error-box').style.display = 'block';
        document.getElementById('error-box').textContent = data.message;
      }
    } catch (e) {
      console.error(e);
    }
  }

  function downloadFile() {
    window.location.href = '/download';
  }
</script>
</body>
</html>
"""

@app.route("/")
def index_page():
    return HTML_TEMPLATE

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=False, host="0.0.0.0", port=8000)