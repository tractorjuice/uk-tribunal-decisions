# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A data pipeline and static site for UK Residential Property Tribunal decisions scraped from GOV.UK. Contains 16,110+ decisions with metadata, full text, and PDF attachments. The frontend is hosted on GitHub Pages at https://tractorjuice.github.io/uk-tribunal-decisions/.

## Commands

### Data Pipeline (run sequentially)

```bash
# 1. Scrape decision metadata from GOV.UK Search API
python3 scripts/scrape_tribunal_decisions.py --output data/tribunal_decisions.json --batch-size 500

# 2. Enrich with full text and PDF URLs (resumable on interruption)
python3 scripts/enrich_tribunal_decisions.py --input data/tribunal_decisions.json --output data/tribunal_decisions_full.json --concurrency 4

# 3. Generate frontend data from index
python3 scripts/build_site_data.py
```

### Dependencies

Only `requests` is needed: `pip install requests`

No build tools, linters, or test frameworks are configured.

## Architecture

**Three-stage pipeline:**

1. **Scraper** (`scripts/scrape_tribunal_decisions.py`) — Fetches decision metadata from `GOV.UK Search API` in batches. Parses titles to extract case references, property addresses, and region codes. Outputs `data/tribunal_decisions.json` (15MB index).

2. **Enricher** (`scripts/enrich_tribunal_decisions.py`) — Hits `GOV.UK Content API` for each decision using ThreadPoolExecutor. Adds full decision text, PDF attachments, and parses applicant/respondent via regex. Saves progress every 100 records to `data/tribunal_decisions_full.json` (307MB, stored in Git LFS). Resumable if interrupted.

3. **Site Builder** (`scripts/build_site_data.py`) — Transforms the index into `docs/data/decisions.json` with precomputed stats (category counts, region counts, year distribution, category hierarchy). Hardcoded input/output paths.

**Frontend** (`docs/`) — Vanilla HTML/CSS/JS (no frameworks, no npm). Fetches `decisions.json` client-side and provides search, filtering (category, sub-category, region, year range), sorting, and a stats dashboard. Paginated at 50 per page. Deployed automatically from `/docs` on the main branch via GitHub Pages.

## Key Data Structures

All data files use the same JSON structure: `{ "metadata": {...}, "decisions": [...] }`. The enriched version adds `full_text`, `attachments`, `pdf_urls`, `applicant`, `respondent`, `application_type`, and `content_id` to each decision. The frontend data file adds a top-level `stats` object.

## Important Details

- `data/tribunal_decisions_full.json` is tracked by Git LFS (see `.gitattributes`)
- GOV.UK APIs are public and require no authentication
- Scraper has retry logic (3 attempts, exponential backoff) and 1-second rate limiting between batches
- Enricher uses 0.15-second per-thread delay and handles HTTP 429 backoff
- Region codes: LON, CHI, MAN, BIR, CAM, HAV, NS, TR, NT, VG, NAT, GB, RC
