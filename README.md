# Lead Generation System

Scrapes local businesses from Google Places, OpenStreetMap (free), and/or
IndiaMART (free, India-only wholesale suppliers), guesses an email from
each business's own website, and stores everything in a local SQLite
database (`data/leads.db`) so re-running never re-scrapes or re-contacts
the same lead. Supports merging in manually-exported LinkedIn contacts.

## Folder structure

```
app.py              entry point — run this
core/                config.py (all tunable constants), db.py (SQLite store),
                     email_finder.py (website email lookup), categories.py,
                     locations.py (city lists per country)
templates/           index.html (Google/OSM scrape form), indiamart.html
                     (separate IndiaMART page), dashboard.html (leads dashboard)
tools/               linkedin_import.py — one-off CSV import script
legacy/              old standalone scripts, kept for reference, no longer used
data/                leads.db (the shared database — dedup/contact-status/DNC
                     live here across all runs), runs/<batch-id>/ (one new
                     folder per scrape, each with its own leads.xlsx snapshot),
                     exports/ (old exported .xlsx files)
```

Every scrape run gets its own folder under `data/runs/<batch-id>/` with a
snapshot of just that run's leads — useful for keeping "Tuesday's Mumbai
cafe scrape" separate from "Thursday's Delhi restaurant scrape" on disk.
The shared `leads.db` still tracks everything centrally (so dedup, contact
status, and do-not-contact keep working across runs) — the dashboard's
**Scrape runs** panel lets you check off multiple runs and export them
merged into one file whenever you want, via `/export/all?batches=id1,id2`
(same pattern for `/export/calls` and `/export/emails`).

Every tunable number (scoring weights, safety caps, cost-estimate
assumptions, email-finder settings) lives in `core/config.py` — nothing is
hardcoded inline in the scraping/scoring logic, so you can retune behavior
by editing one file.

## Setup

```
cp .env.example .env        # then edit .env and paste your Google Maps API key
pip install -r requirements.txt
python3 app.py
```

If you want to use the **IndiaMART** source, it needs a real headless
browser (Playwright) to render pages — a one-time extra step after
`pip install`:
```
playwright install chromium
```
This downloads Chromium (~150-300MB). Skip it if you're not using
IndiaMART — Google Places and OpenStreetMap don't need it.

Open http://localhost:8000 to run a scrape:

1. **Data source** — Google Places (ratings/reviews included, costs money
   past the free monthly quota) and/or OpenStreetMap (completely free, no
   quota, no ratings/reviews — see tradeoffs below). Pick either or both;
   with OpenStreetMap only, no `GOOGLE_MAPS_API_KEY` is required at all.
2. **Who** — check one or more target-customer groups (professional
   services, healthcare/wellness, retail/hospitality, trades & local
   services, education & coaching, automotive, events & hospitality, IT &
   creative agencies), optionally add a legacy ProFixer/AMC preset or your
   own custom categories.
3. **Where** — check a whole country (selects all its major cities) or
   expand "Select specific cities" to pick individual ones — e.g. just
   Mumbai and Pune instead of all 25 Indian cities. Or add custom
   locations of your own.
4. **How many leads** — cap the run at 50/100/150/200/500, or no limit.
   This applies **per source, independently** — with both Google and
   OpenStreetMap selected and a cap of 100, each can contribute up to 100
   (so up to 200 total, not 100 combined). Each source's search stops as
   soon as it hits the cap rather than exhausting every category/city
   combination, which also means a small cap uses noticeably less of your
   Google quota specifically.
5. Watch the live **estimate box** — it shows categories × locations,
   estimated businesses found, and estimated cost *before* you commit
   (OpenStreetMap always shows $0). If your selection is over the safety
   cap (`MAX_QUERIES_PER_RUN` in `.env`, default 400), Start is disabled
   until you narrow scope or raise the cap yourself.

Progress shows live once started; safe to close the tab mid-run.

### Free source: OpenStreetMap

No API key, no quota, no cost, ever — but a real tradeoff, not a free
lunch:

- **No ratings or review counts at all.** OSM doesn't track that data, so
  those parts of the lead score always contribute 0 for OSM-sourced leads.
- **Coverage varies a lot by region.** It's volunteer-mapped, not
  business-claimed, so completeness is inconsistent — in testing, a
  restaurant search in Mumbai returned business names reliably but a
  phone number or website for only about 1 in 5.
- **Category coverage depends on OSM's tagging schema.** Categories with a
  clean OSM tag (restaurants, cafes, dentists, electricians, etc. — see
  `core/osm_source.py`'s `CATEGORY_OSM_TAGS`) work well; categories without
  one (e.g. "AC repair", "pest control") fall back to a noisier
  name-keyword search.
- Respects Nominatim's and Overpass's public-instance usage policies
  (throttled to ~1 request/second) — a full country-wide OSM run will take
  a while (roughly 1-2 seconds per category×city pair), not because of any
  quota, just politeness to the free public infrastructure.

Every lead's **Source** is visible on the dashboard so you know which ones
have Google's richer data vs. OpenStreetMap's free-but-thinner data.

### IndiaMART (separate page: http://localhost:8000/indiamart)

IndiaMART is a B2B marketplace for manufacturers and wholesale suppliers —
different enough from the other sources (product-keyword search, India
only, no per-city grid, no cost/quota concept) that it lives on its own
page instead of being a checkbox in the main scraper. Important details:

- **Search by product keyword, not business type**, and **products only,
  not services** — "stainless steel wire" or "cotton yarn" work; "wedding
  planner" or "marketing agency" return 0 results because that category
  page genuinely doesn't exist on IndiaMART's site (a real 404, not a
  scraping failure) — services aren't listed under `/impcat/` at all.
- **You can paste a full URL instead of a keyword.** Any line in the
  keywords box can be a product keyword OR a full
  `https://dir.indiamart.com/impcat/...` URL. Useful when a typed
  keyword's auto-generated slug doesn't match IndiaMART's real one —
  browse indiamart.com yourself, find the category, and paste its exact
  URL instead of guessing.
- **No phone numbers.** IndiaMART blurs contact details behind a
  mandatory phone-number + OTP login on their interactive search pages.
  This tool only reads their public, unauthenticated category pages
  (`dir.indiamart.com/impcat/...`, meant to be indexed by Google), which
  show company name, the supplier's own website, city, rating, and review
  count — but not a phone number. We deliberately do not automate the
  OTP login flow; use the extracted company website with the email finder
  instead, same as any other source.
- **India only** — the city checkboxes on this page are India's cities only.
- **Needs Playwright** (`playwright install chromium` — see Setup above)
  because listings render client-side; a plain HTTP request only gets
  the empty page shell.
- **No city URL filter exists on these pages**, so one page load per
  product keyword returns suppliers across whatever cities IndiaMART
  shows (often Mumbai-weighted), and this tool filters to your selected
  cities afterward. If none of the results match your selected city,
  you'll get zero for that keyword — try without a city filter, or a
  different keyword.
- This is a genuinely public page (no login required), which is a
  meaningfully lower-risk situation than scraping behind authentication —
  but it likely still violates IndiaMART's Terms of Service. Build this
  knowing that.

### Country-wide scraping costs real money

Google's Places API (legacy endpoints, which this app uses) gives **5,000
free Text Search requests + 5,000 free Place Details requests per calendar
month** (Pro SKU, since we request phone/website fields) — this renews
every month, it is not a one-time signup credit, and it's separate from
the old $200/month universal credit that Google discontinued on
2025-02-28. Beyond that free quota: **$32 per 1,000 Text Search requests**
and **$17 per 1,000 Place Details requests**.

The estimate box tracks how much of that free quota you've already used
this month (`core/usage_tracker.py`, persisted to `data/api_usage.json`)
and subtracts it before showing a cost — so a small run right after the
month rolls over will often show **$0.00**, while the same run later in
the month, after quota is used up, shows the real marginal cost.

Selecting all 4 category groups × all 3 countries is ~2,000 searches and
can surface tens of thousands of businesses — a run that size will likely
exceed the free quota and cost real money (potentially hundreds of
dollars, mostly from Place Details calls). Start narrower (one country,
one or two groups), check the estimate, and scale up deliberately rather
than firing the broadest run first.

## AI-generated cold call/email openers (free, local, via Ollama)

The dashboard can generate a short, personalized opener per lead — 2-3
sentences referencing the business's name, category, and an obvious gap
(like having no website) — using a model running locally through
[Ollama](https://ollama.com), so there's no API cost:

```
ollama serve
ollama pull llama3.1     # or any model you already have pulled
```

If you're running a different model, set it in `.env`:
```
OLLAMA_MODEL=your-model-name
```

On the Leads Dashboard, a status pill in the top-right shows whether Ollama
is reachable. Click "Generate opener" on any lead's row to create one
(saved to that lead so it persists); "Regenerate" replaces it. If Ollama
isn't running, the button shows a clear error instead of failing silently.

This is a starting draft, not a finished script — read it before using it
on a real call or email, and adjust the tone/facts as needed.

## The Leads Dashboard — for your calling/emailing team

Open **http://localhost:8000/leads**. This is the page non-technical team
members should use day to day — no code, no files, just a table:

- Click a phone number to call it, click an email to open a draft.
- Search and filter by qualification (Hot/Warm/Cold) or call status.
- Change the "Call status" / "Email status" dropdown on any row after you
  contact someone — it saves automatically.
- Check "Stop contact" the moment someone asks not to be called/emailed
  again — this permanently hides them from every future export.
- Stat tiles at the top show at a glance how many leads are ready to call,
  ready to email, and already contacted.
- Export buttons (Call list / Email list / All leads) download an Excel
  file matching whatever's currently in the database.

Nobody needs to touch `app.py`, the database, or a terminal to use this —
only whoever runs the scrapes needs the technical setup above.

## Exporting for outreach

- `/export/calls` — leads with a phone number, for your calling team
- `/export/emails` — leads with an email, for cold email sequences
- `/export/all` — everything currently in the database

All exports automatically exclude anything flagged `do_not_contact`. The
dashboard's export buttons link to the same three.

## Marking outcomes / opt-outs (API, used internally by the dashboard)

`POST /api/leads/<id>/status` with any of:
```json
{ "call_status": "connected", "call_notes": "interested, follow up Friday" }
{ "email_status": "replied" }
{ "do_not_contact": true, "reason": "asked to be removed" }
```
Always honor opt-out requests immediately — set `do_not_contact` the moment
someone asks not to be called/emailed again. In practice, just check the
"Stop contact" box on the dashboard — it calls this for you.

## Adding LinkedIn contacts

Custom LinkedIn scraping is **not** included here on purpose — it violates
LinkedIn's Terms of Service and carries real risk (account bans, and legal
precedent like *hiQ v. LinkedIn* shows this area is contested and
enforcement-heavy). Instead:

1. Manually export search results from Sales Navigator (or LinkedIn's own
   data export) to CSV with columns: `first_name, last_name, title,
   company, linkedin_url, email, phone`.
2. Run `python3 tools/linkedin_import.py contacts.csv` — it matches contacts
   to businesses already in `data/leads.db` by company name, or adds them
   as new standalone leads if no match is found.

If you outgrow manual exports, a paid compliant API (Apollo.io, Hunter.io,
ZoomInfo) is a safer way to automate contact/title enrichment than scraping
LinkedIn directly.

## Compliance — read before cold calling/emailing at any volume

This varies by where your leads are located; check what applies to you:

- **Cold calling (India):** numbers registered on the NDNC (National Do Not
  Call) registry can't be called for promotional purposes under TRAI
  regulations. Numbers scraped from Google Business listings are
  effectively unscreened, so build in a way to record "asked not to be
  called" per lead (the `do_not_contact` flag here) at minimum.
- **Cold calling (US):** the TCPA imposes real liability (statutory damages
  per call) for calls to numbers on the National DNC Registry or made with
  autodialers without consent — check exemptions for B2B calls carefully,
  they're narrower than people assume.
- **Cold calling (Australia):** the Do Not Call Register Act 2006 (enforced
  by ACMA) prohibits unsolicited calls to numbers on the DNC Register for
  most marketing purposes, with real penalties per breach. B2B exemptions
  exist but are narrower than commonly assumed — check current ACMA
  guidance before calling business numbers at volume.
- **Cold email:** CAN-SPAM (US) requires a working unsubscribe mechanism,
  accurate sender info, and no misleading subject lines. Australia's Spam
  Act 2003 requires consent, sender identification, and an unsubscribe
  facility for commercial email, enforced by ACMA. GDPR (if any leads are
  in the EU) requires a lawful basis for processing personal data, which
  B2B "legitimate interest" outreach can sometimes satisfy but has real
  limits — role-based emails (info@, sales@) are lower-risk than scraping
  named individuals' personal emails.
- **Scraping generally:** Google's Terms of Service govern use of the
  Places API results — this tool uses the official API rather than
  scraping Maps HTML directly, which is the compliant way to get this data.
  Crawling company websites for emails respects `robots.txt` and only hits
  a few pages per site. IndiaMART scraping (see above) most likely
  violates their ToS even though the pages are publicly accessible without
  login — proceed with that understanding.

None of this is legal advice — if you're doing this at real volume, have a
lawyer confirm what applies to your specific target geography.

## Files

- `app.py` — unified scraper + web UI (replaces the old scripts below,
  which were ~95% duplicated copies of the same tool)
- `templates/index.html` — the Google/OSM scrape-a-new-batch page
- `templates/indiamart.html` — the separate IndiaMART supplier-search page
- `templates/dashboard.html` — the leads dashboard for calling/emailing
- `core/config.py` — every tunable constant (scoring weights, safety caps,
  cost-estimate assumptions, email-finder settings)
- `core/categories.py` — the 8 target-customer groups + legacy presets
- `core/locations.py` — major-city lists for India/USA/Australia
- `core/db.py` — SQLite schema, dedup, contact-status tracking
- `core/email_finder.py` — crawls a business's own website for a contact email
- `core/ai_enricher.py` — generates a personalized opener per lead via local Ollama
- `core/usage_tracker.py` — tracks actual monthly Google Places API calls so the cost estimate reflects your real remaining free quota
- `core/osm_source.py` — free OpenStreetMap business search (Nominatim geocoding + Overpass query), normalized to look like a Google Places result so the rest of the pipeline doesn't care which source a lead came from
- `core/indiamart_source.py` — free IndiaMART supplier search via Playwright, reading only their public (non-login-gated) category pages
- `tools/linkedin_import.py` — merges manually-exported LinkedIn contacts
- `legacy/` — the original scripts (`amc_leads.py`, `profixer_leads.py`,
  `lead_generator_all.py`, `lead_generator.py`), kept for reference only;
  no longer used by anything and safe to delete once you've confirmed
  `app.py` covers your needs
