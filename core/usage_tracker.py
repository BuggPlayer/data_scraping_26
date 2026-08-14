"""
Tracks actual Google Places API calls made this calendar month, so the
/estimate endpoint can subtract the real remaining free quota (5,000
Text Search + 5,000 Place Details per month, Pro SKU) instead of assuming
the full quota is available on every run.
"""

import json
import os
from datetime import date

from core import config

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
USAGE_PATH = os.path.join(DATA_DIR, "api_usage.json")


def _current_month():
    return date.today().isoformat()[:7]


def _load():
    if os.path.exists(USAGE_PATH):
        try:
            with open(USAGE_PATH) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    month = _current_month()
    if data.get("month") != month:
        # new calendar month - the free quota has reset, so start counting fresh
        data = {"month": month, "text_search_calls": 0, "details_calls": 0}
    return data


def _save(data):
    with open(USAGE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def record_text_search_calls(n: int):
    if n <= 0:
        return
    data = _load()
    data["text_search_calls"] = data.get("text_search_calls", 0) + n
    _save(data)


def record_details_calls(n: int):
    if n <= 0:
        return
    data = _load()
    data["details_calls"] = data.get("details_calls", 0) + n
    _save(data)


def get_usage():
    """Returns this month's call counts and remaining free quota."""
    data = _load()
    search_used = data.get("text_search_calls", 0)
    details_used = data.get("details_calls", 0)
    return {
        "month": data["month"],
        "text_search_calls_used": search_used,
        "details_calls_used": details_used,
        "text_search_free_remaining": max(0, config.TEXT_SEARCH_FREE_QUOTA_MONTHLY - search_used),
        "details_free_remaining": max(0, config.DETAILS_FREE_QUOTA_MONTHLY - details_used),
    }
