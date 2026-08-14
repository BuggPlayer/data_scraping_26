"""
Supplier discovery via IndiaMART's public product/category pages
(dir.indiamart.com/impcat/*.html) - these are IndiaMART's own SEO-facing
pages, meant to be indexed by Google, so the basic listing (company name,
company website, city, rating, review count) is NOT behind their
login/OTP wall - unlike their interactive search flow, which blurs
everything until you log in with a phone number.

Phone numbers ARE still gated behind a "Call Now" button that requires
login - NOT available here. Use the extracted company website with
core/email_finder.py to get a contact email instead, same as any other
source.

This targets PRODUCT keywords ("stainless steel wire", "cotton yarn"),
not business-type categories like "Electrician" - it's a wholesale/
manufacturing supplier directory, a different axis than the other
sources. Feeding it a business-type category will just return no results,
not an error.

Requires Playwright (a real headless browser) because IndiaMART renders
listings client-side; a plain HTTP request only returns the page shell.
Uses NO stealth/evasion techniques - a plain, identifiable headless
browser, single page load per keyword. If IndiaMART changes their page
structure or starts blocking this, expect it to return [] silently, not
some workaround.
"""

import logging
import re

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
BASE_URL = "https://dir.indiamart.com/impcat/{slug}.html"
PAGE_LOAD_TIMEOUT_MS = 30000
RENDER_WAIT_MS = 4000


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def fetch_category(keyword: str, max_results: int = 30) -> list:
    """Loads the IndiaMART category page for this product keyword and
    returns every supplier found (across all cities shown on the page,
    not city-filtered - do that afterward in Python since IndiaMART's URL
    doesn't reliably support city query params here).

    Returns a list of dicts shaped like a Google Place Details 'result'
    (name, website, formatted_address=city, rating, user_ratings_total,
    business_status) so this plugs into the same pipeline as other
    sources. Returns [] on any failure - a bad keyword, page structure
    change, or network error shouldn't crash the run."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logging.error("Playwright isn't installed. Run: pip install playwright && "
                       "playwright install chromium")
        return []

    slug = _slugify(keyword)
    if not slug:
        return []
    url = BASE_URL.format(slug=slug)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS)
                page.wait_for_timeout(RENDER_WAIT_MS)

                cards = page.locator("article.template7-product-card")
                count = min(cards.count(), max_results)
                results = []

                for i in range(count):
                    card = cards.nth(i)
                    parsed = _parse_card(card, slug, url)
                    if parsed:
                        results.append(parsed)

                return results
            finally:
                browser.close()
    except Exception as e:
        logging.warning(f"IndiaMART fetch failed for '{keyword}': {e}")
        return []


def _parse_card(card, slug: str, source_url: str):
    try:
        name_el = card.locator("a.template7-seller-name").first
        if name_el.count() == 0:
            return None
        name = name_el.inner_text().strip()
        if not name:
            return None
        website = name_el.get_attribute("href") or ""

        city_el = card.locator("span[itemprop=addressLocality]").first
        city = city_el.inner_text().strip() if city_el.count() else ""

        rating = None
        rating_el = card.locator(".dag5 .b").first
        if rating_el.count():
            try:
                rating = float(rating_el.inner_text().strip())
            except ValueError:
                rating = None

        reviews = None
        review_spans = card.locator(".dag5 span")
        if review_spans.count() > 1:
            digits = re.sub(r"\D", "", review_spans.nth(1).inner_text())
            reviews = int(digits) if digits else None

        return {
            "place_id": f"indiamart:{slug}:{_slugify(name)}:{_slugify(city)}",
            "name": name,
            "formatted_phone_number": "",
            "website": website,
            "formatted_address": city,
            "rating": rating or 0,
            "user_ratings_total": reviews or 0,
            "business_status": "",
            "url": source_url,
            "types": [],
        }
    except Exception as e:
        logging.warning(f"IndiaMART card parse failed: {e}")
        return None
