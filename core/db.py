"""
SQLite-backed lead store. Gives the scraper persistence across runs so the
same business is never re-scraped/re-contacted twice, and gives cold
email/calling a place to track outcomes and opt-outs.
"""

import os
import sqlite3
from contextlib import contextmanager

from core import config

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "leads.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT UNIQUE,
    business_name TEXT,
    category TEXT,
    locality TEXT,
    region TEXT,
    country TEXT,
    address TEXT,
    phone TEXT,
    website TEXT,
    email TEXT,
    rating REAL,
    reviews INTEGER,
    business_status TEXT,
    maps_url TEXT,
    lead_score INTEGER,
    qualification TEXT,
    source TEXT DEFAULT 'google_maps',
    do_not_contact INTEGER DEFAULT 0,
    do_not_contact_reason TEXT,
    call_status TEXT DEFAULT 'not_called',
    call_notes TEXT,
    last_called_at TEXT,
    email_status TEXT DEFAULT 'not_sent',
    last_emailed_at TEXT,
    ai_pitch TEXT,
    batch_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    label TEXT,
    sources TEXT,
    categories_summary TEXT,
    locations_summary TEXT,
    lead_count INTEGER DEFAULT 0,
    output_dir TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    first_name TEXT,
    last_name TEXT,
    title TEXT,
    email TEXT,
    phone TEXT,
    linkedin_url TEXT,
    source TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """Add columns introduced after a user's leads.db already existed."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
    for column, ddl_type in (("region", "TEXT"), ("country", "TEXT"), ("ai_pitch", "TEXT"), ("batch_id", "TEXT")):
        if column not in existing_cols:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {ddl_type}")


def upsert_lead(row: dict) -> int:
    """Insert a new lead or update an existing one (matched by place_id),
    preserving call/email status and do-not-contact flags on repeat scrapes.
    Returns the lead's row id."""
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM leads WHERE place_id = ?", (row["place_id"],)
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE leads SET
                    business_name = ?, category = ?, locality = ?, region = ?, country = ?,
                    address = ?, phone = ?, website = ?,
                    email = COALESCE(NULLIF(email, ''), ?),
                    rating = ?, reviews = ?, business_status = ?, maps_url = ?,
                    lead_score = ?, qualification = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    row["business_name"], row["category"], row["locality"],
                    row.get("region", ""), row.get("country", ""), row["address"],
                    row["phone"], row["website"], row.get("email", ""),
                    row["rating"], row["reviews"], row["business_status"], row["maps_url"],
                    row["lead_score"], row["qualification"],
                    existing["id"],
                ),
            )
            return existing["id"]

        cur = conn.execute(
            """
            INSERT INTO leads (
                place_id, business_name, category, locality, region, country, address,
                phone, website, email, rating, reviews, business_status,
                maps_url, lead_score, qualification, source, batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["place_id"], row["business_name"], row["category"], row["locality"],
                row.get("region", ""), row.get("country", ""),
                row["address"], row["phone"], row["website"], row.get("email", ""),
                row["rating"], row["reviews"], row["business_status"], row["maps_url"],
                row["lead_score"], row["qualification"], row.get("source", "google_maps"),
                row.get("batch_id", ""),
            ),
        )
        return cur.lastrowid


def get_lead_by_place_id(place_id: str):
    with connect() as conn:
        return conn.execute("SELECT * FROM leads WHERE place_id = ?", (place_id,)).fetchone()


def get_lead_by_id(lead_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None


def set_ai_pitch(lead_id: int, pitch: str):
    with connect() as conn:
        conn.execute(
            "UPDATE leads SET ai_pitch = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (pitch, lead_id),
        )


def set_email(lead_id: int, email: str):
    with connect() as conn:
        conn.execute(
            "UPDATE leads SET email = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (email, lead_id),
        )


def set_do_not_contact(lead_id: int, reason: str = ""):
    with connect() as conn:
        conn.execute(
            "UPDATE leads SET do_not_contact = 1, do_not_contact_reason = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (reason, lead_id),
        )


def set_call_status(lead_id: int, status: str, notes: str = ""):
    with connect() as conn:
        conn.execute(
            "UPDATE leads SET call_status = ?, call_notes = ?, last_called_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, notes, lead_id),
        )


def set_email_status(lead_id: int, status: str):
    with connect() as conn:
        conn.execute(
            "UPDATE leads SET email_status = ?, last_emailed_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, lead_id),
        )


def find_lead_by_business_name(name_normalized: str):
    """Best-effort match for merging manual LinkedIn exports onto scraped leads."""
    with connect() as conn:
        rows = conn.execute("SELECT id, business_name FROM leads").fetchall()
        for r in rows:
            if _normalize(r["business_name"]) == name_normalized:
                return r["id"]
        return None


def _normalize(name: str) -> str:
    if not name:
        return ""
    n = name.lower().strip()
    for j in config.BUSINESS_NAME_JUNK_TOKENS:
        n = n.replace(j, " ")
    return " ".join(n.split())


def create_batch(batch_id: str, label: str, sources: str, categories_summary: str,
                  locations_summary: str, output_dir: str):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO batches (batch_id, label, sources, categories_summary, locations_summary, output_dir)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (batch_id, label, sources, categories_summary, locations_summary, output_dir),
        )


def set_batch_lead_count(batch_id: str, count: int):
    with connect() as conn:
        conn.execute("UPDATE batches SET lead_count = ? WHERE batch_id = ?", (count, batch_id))


def get_batches():
    """Most recent run first."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def add_contact(lead_id, first_name, last_name, title, email, phone, linkedin_url, source):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO contacts (lead_id, first_name, last_name, title, email, phone, linkedin_url, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (lead_id, first_name, last_name, title, email, phone, linkedin_url, source),
        )


def export_rows(callable_only: bool = False, emailable_only: bool = False,
                 include_do_not_contact: bool = False, country: str = None, batch_ids: list = None):
    """Return leads. Excludes do_not_contact unless include_do_not_contact=True
    (the dashboard wants to see everyone including opt-outs; file exports for
    actually calling/emailing should never include them). Filter by country
    since cold-calling/emailing compliance rules differ (see README).
    Filter by batch_ids (a list) to merge specific runs together on export -
    omit for "every run combined", which is the historical default behavior."""
    query = "SELECT * FROM leads"
    conditions = []
    params = []
    if not include_do_not_contact:
        conditions.append("do_not_contact = 0")
    if callable_only:
        conditions.append("phone IS NOT NULL AND phone != ''")
    if emailable_only:
        conditions.append("email IS NOT NULL AND email != ''")
    if country:
        conditions.append("country = ?")
        params.append(country)
    if batch_ids:
        conditions.append(f"batch_id IN ({','.join('?' * len(batch_ids))})")
        params.extend(batch_ids)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY lead_score DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
