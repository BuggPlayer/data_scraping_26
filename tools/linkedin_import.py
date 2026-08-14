"""
Merge a manually-exported LinkedIn/Sales Navigator CSV into leads.db.

We do NOT automate LinkedIn scraping (violates their ToS, risk of account
ban/legal action - see hiQ v. LinkedIn and LinkedIn's active enforcement).
Instead: export your Sales Navigator search results manually (or via
LinkedIn's own data export), then run this to attach decision-maker
contacts to businesses already found by the Google Maps scraper.

Expected CSV columns (case-insensitive, extra columns ignored):
    first_name, last_name, title, company, linkedin_url, email, phone

Usage (run from the project root):
    python3 tools/linkedin_import.py contacts.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import db


def main(csv_path: str):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    required = {"first_name", "last_name", "company"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"CSV is missing required columns: {missing}")

    db.init_db()
    matched, unmatched = 0, 0

    for _, row in df.iterrows():
        company = str(row.get("company", "")).strip()
        lead_id = db.find_lead_by_business_name(db._normalize(company))

        if lead_id is None:
            # No matching scraped business - still record the contact as a
            # standalone lead so it isn't lost, flagged with no phone/email
            # from Maps but sourced from LinkedIn.
            lead_id = db.upsert_lead({
                "place_id": f"linkedin:{company}:{row.get('first_name')}:{row.get('last_name')}",
                "business_name": company,
                "category": "", "locality": "", "address": "",
                "phone": row.get("phone", "") or "",
                "website": "", "email": row.get("email", "") or "",
                "rating": None, "reviews": None, "business_status": "",
                "maps_url": "", "lead_score": 0, "qualification": "Cold",
                "source": "linkedin_manual",
            })
            unmatched += 1
        else:
            matched += 1

        db.add_contact(
            lead_id=lead_id,
            first_name=row.get("first_name", ""),
            last_name=row.get("last_name", ""),
            title=row.get("title", ""),
            email=row.get("email", ""),
            phone=row.get("phone", ""),
            linkedin_url=row.get("linkedin_url", ""),
            source="linkedin_manual",
        )

    print(f"Imported {len(df)} contacts: {matched} matched to existing leads, "
          f"{unmatched} added as new standalone leads.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 tools/linkedin_import.py <path_to_exported_contacts.csv>")
    main(sys.argv[1])
