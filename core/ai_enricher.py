"""
Optional local AI enrichment via Ollama - free, runs on your machine, no
API key and no per-call cost. Generates a short, personalized cold
call/email opener per lead using what the scraper already knows about the
business (name, category, whether it has a website, reviews).

Requires Ollama running locally with a model pulled:
    ollama serve
    ollama pull llama3.1
"""

import logging

import requests

from core import config

SYSTEM_PROMPT = (
    "You write short, natural-sounding openers for cold calls and cold "
    "emails from a software agency that sells CRM setup, websites, AI "
    "services, and business automation to small/local businesses. "
    "Write 2-3 sentences, no greeting, no sign-off, no markdown - just the "
    "opener itself. Reference something specific and true about the "
    "business (its name, category, or an obvious gap like having no "
    "website) rather than generic flattery. Keep it conversational, not "
    "salesy."
)


def is_available() -> bool:
    """Whether Ollama is reachable right now - used to show a status
    indicator in the dashboard rather than failing silently per-lead."""
    try:
        r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _lead_facts(lead: dict) -> str:
    facts = [
        f"Business name: {lead.get('business_name', '')}",
        f"Category: {lead.get('category', '')}",
    ]
    if lead.get("locality") or lead.get("country"):
        facts.append(f"Location: {lead.get('locality', '')}, {lead.get('country', '')}")
    facts.append("Has a website: " + ("yes" if lead.get("website") else "no"))
    if lead.get("reviews") is not None:
        facts.append(f"Google reviews: {lead.get('reviews')} (rating {lead.get('rating', 'n/a')})")
    return "\n".join(facts)


def generate_pitch(lead: dict) -> str:
    """Return a personalized opener for this lead, or '' on any failure
    (Ollama not running, model not pulled, request error) - this is a
    nice-to-have enrichment step and should never raise or block the
    scrape/dashboard."""
    prompt = f"Business details:\n{_lead_facts(lead)}\n\nWrite the opener."

    try:
        r = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "system": SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
            },
            timeout=config.OLLAMA_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except requests.RequestException as e:
        logging.warning(f"Ollama pitch generation failed: {e}")
        return ""
