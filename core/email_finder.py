"""
Best-effort email discovery from a business's own website. Google Places
never returns emails, so for cold email we crawl a handful of likely pages
(home, contact, about) on the SAME domain and pull out any email addresses.

Respects robots.txt and uses a light request budget per site — this is a
handful of pages for a handful of hundred leads/month, not a full crawl.
"""

import logging
import re
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core import config

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _allowed_by_robots(base_url: str) -> bool:
    try:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urljoin(base_url, "/robots.txt"))
        rp.read()
        return rp.can_fetch(config.EMAIL_FINDER_USER_AGENT, base_url)
    except Exception:
        return True  # no robots.txt or unreachable -> assume allowed


def _extract_emails(html: str, domain: str) -> set:
    found = set(EMAIL_RE.findall(html))
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href^=mailto]"):
        addr = a["href"].split("mailto:")[-1].split("?")[0].strip()
        if addr:
            found.add(addr)

    clean = set()
    for e in found:
        e = e.strip().strip(".,")
        if any(j in e.lower() for j in config.EMAIL_JUNK_DOMAINS):
            continue
        clean.add(e)
    return clean


def _rank(emails: set, domain: str) -> list:
    def score(e):
        local = e.split("@")[0].lower()
        same_domain = domain in e.lower()
        role_based = any(local.startswith(p) for p in config.EMAIL_ROLE_PREFIXES)
        return (same_domain, role_based)

    return sorted(emails, key=score, reverse=True)


def find_email(website_url: str) -> str:
    """Return the best-guess email for a business, or '' if none found."""
    if not website_url:
        return ""

    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    parsed = urlparse(website_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc.replace("www.", "")

    if not _allowed_by_robots(base):
        logging.info(f"robots.txt disallows crawling {base}, skipping")
        return ""

    session = requests.Session()
    session.headers.update({"User-Agent": config.EMAIL_FINDER_USER_AGENT})

    all_emails = set()
    for path in config.EMAIL_FINDER_CANDIDATE_PATHS:
        url = urljoin(base, path)
        try:
            r = session.get(url, timeout=config.EMAIL_FINDER_TIMEOUT_SECONDS)
            if r.status_code == 200:
                all_emails |= _extract_emails(r.text, domain)
        except requests.RequestException:
            continue
        if all_emails:
            break  # found something, no need to keep hitting more pages

    if not all_emails:
        return ""

    ranked = _rank(all_emails, domain)
    return ranked[0]
