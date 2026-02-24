# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A data pipeline and static site for UK Residential Property Tribunal decisions scraped from GOV.UK (England) and residentialpropertytribunal.gov.wales (Wales). Contains 16,110+ England decisions and 650+ Wales decisions with metadata, full text, and PDF attachments. The frontend is hosted on GitHub Pages at https://tractorjuice.github.io/uk-tribunal-decisions/.

## Commands

### Data Pipeline (run sequentially)

```bash
# 1. Scrape decision metadata from GOV.UK Search API
python3 scripts/scrape_tribunal_decisions.py --output data/tribunal_decisions.json --batch-size 500

# 2. Enrich with full text and PDF URLs (resumable on interruption)
python3 scripts/enrich_tribunal_decisions.py --input data/tribunal_decisions.json --output data/tribunal_decisions_full.json --concurrency 4

# 3. Extract structured fields from full_text (applicant, respondent, judges, outcomes, etc.)
python3 scripts/extract_structured_fields.py

# 4. Fetch PDFs for decisions missing full_text (~163 decisions, ~50MB)
python3 scripts/fetch_pdfs.py
# Then re-run extraction on the newly-filled records:
python3 scripts/extract_structured_fields.py

# 5. Scrape Wales tribunal decisions (HTML scraping + PDF extraction, ~30-45 min)
python3 scripts/scrape_wales_decisions.py
# Quick test with: python3 scripts/scrape_wales_decisions.py --sample 5

# 6. Generate frontend data from index (merges England + Wales automatically)
python3 scripts/build_site_data.py
```

### Dependencies

`pip install requests pdfplumber`

- `requests` — HTTP client for GOV.UK APIs and PDF downloads
- `pdfplumber` — PDF text extraction (needed for `fetch_pdfs.py` and `scrape_wales_decisions.py`)

No build tools, linters, or test frameworks are configured.

## Architecture

**Six-stage pipeline (England 1-4, Wales 5, combined 6):**

1. **Scraper** (`scripts/scrape_tribunal_decisions.py`) — Fetches decision metadata from `GOV.UK Search API` in batches. Parses titles to extract case references, property addresses, and region codes. Outputs `data/tribunal_decisions.json` (15MB index).

2. **Enricher** (`scripts/enrich_tribunal_decisions.py`) — Hits `GOV.UK Content API` for each decision using ThreadPoolExecutor. Adds full decision text, PDF attachments, and parses applicant/respondent via regex. Saves progress every 100 records to `data/tribunal_decisions_full.json` (307MB, stored in Git LFS). Resumable if interrupted.

3. **Field Extractor** (`scripts/extract_structured_fields.py`) — Extracts structured fields from existing `full_text` using regex: improved applicant/respondent (~94%), tribunal members/presiding judge (~84%), decision outcomes (~63%), financial amounts (~82%), hearing dates (~18%), and legal acts cited (~95%). Runs in ~45 seconds, no network calls.

4. **PDF Fetcher** (`scripts/fetch_pdfs.py`) — Downloads and extracts text from PDFs for the ~163 decisions missing `full_text`. Uses `pdfplumber` for text extraction. Supports `--sample N` for testing, `--all` for complete archive. PDFs stored in `data/pdfs/` (gitignored), manifest in `data/pdf_manifest.json`. Flags low-text PDFs as `ocr_required`.

5. **Wales Scraper** (`scripts/scrape_wales_decisions.py`) — Scrapes Wales Residential Property Tribunal decisions from `residentialpropertytribunal.gov.wales`. Three phases: list page scraping (3 tribunal types × fiscal years), detail page scraping (metadata extraction), PDF download + text extraction (via pdfplumber). Outputs `data/wales_tribunal_decisions.json`. Supports `--skip-pdfs`, `--sample N`, `--delay`. Resumable on interruption. Wales PDFs stored in `data/wales_pdfs/` (gitignored), manifest in `data/wales_pdf_manifest.json`.

6. **Site Builder** (`scripts/build_site_data.py`) — Transforms the index into `docs/data/decisions.json` with precomputed stats (category counts, region counts, year distribution, category hierarchy, field coverage, legal act frequencies). Automatically merges Wales data if `data/wales_tribunal_decisions.json` exists.

**Frontend** (`docs/`) — Vanilla HTML/CSS/JS (no frameworks, no npm). Fetches `decisions.json` client-side and provides search, filtering (category, sub-category, region, year range), sorting, and a stats dashboard. Paginated at 50 per page. Deployed automatically from `/docs` on the main branch via GitHub Pages.

## Key Data Structures

All data files use the same JSON structure: `{ "metadata": {...}, "decisions": [...] }`. The enriched version adds `full_text`, `attachments`, `pdf_urls`, `applicant`, `respondent`, `application_type`, and `content_id` to each decision. After structured extraction, decisions also have `tribunal_members` (list), `presiding_judge`, `decision_outcome`, `financial_amounts` (list of floats), `hearing_date`, `legal_acts_cited` (list), and optionally `text_source: "pdf"` for PDF-sourced text. Wales decisions additionally have `data_source: "wales"` and `pdf_url` (single URL string). The frontend data file adds a top-level `stats` object.

## Important Details

- `data/tribunal_decisions_full.json` is tracked by Git LFS (see `.gitattributes`)
- `data/pdfs/` is gitignored (3-9GB when all PDFs downloaded)
- `data/wales_pdfs/` is gitignored (150-250MB when all Wales PDFs downloaded)
- `data/pdf_manifest.json` tracks downloaded PDF metadata (committed)
- `data/wales_pdf_manifest.json` tracks Wales PDF downloads (committed)
- GOV.UK APIs are public and require no authentication
- Scraper has retry logic (3 attempts, exponential backoff) and 1-second rate limiting between batches
- Enricher uses 0.15-second per-thread delay and handles HTTP 429 backoff
- Region codes: LON, CHI, MAN, BIR, CAM, HAV, NS, TR, NT, VG, NAT, GB, RC, WAL
