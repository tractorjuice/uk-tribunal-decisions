# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project overview

A data pipeline and static site for UK Residential Property Tribunal decisions
scraped from GOV.UK (England) and residentialpropertytribunal.gov.wales (Wales).
The frontend is served by GitHub Pages from `/docs` on `main`:
https://tractorjuice.github.io/uk-tribunal-decisions/

Current record counts live in the generated block in `README.md` and in
`docs/data/decisions.json` under `stats`. This file deliberately quotes none —
hand-maintained counts here went stale within weeks, every time.

## Commands

```bash
pip install -r requirements.txt     # pinned; Python 3.10+ required

# England, in order
python3 scripts/scrape_tribunal_decisions.py [--output FILE] [--batch-size N]
python3 scripts/enrich_tribunal_decisions.py [--input FILE] [--output FILE] [--concurrency N]
python3 scripts/extract_structured_fields.py [--input FILE] [--output FILE] [--overwrite]

# Optional: decisions whose text is only in an attached PDF
python3 scripts/fetch_pdfs.py [--sample N] [--all]
python3 scripts/extract_structured_fields.py      # re-run over the new text

# Wales
python3 scripts/scrape_wales_decisions.py [--sample N] [--skip-pdfs] [--delay S] [--allow-shrink]

# Build the site
python3 scripts/build_site_data.py [--input FILE] [--skip-pages] [--skip-search-index]

# Checks (both run in CI)
python3 scripts/test_extraction.py
python3 scripts/verify_data.py [--strict]
```

`.github/workflows/refresh-data.yml` runs the whole thing weekly.

## Architecture

**Stages 1–4 England, 5 Wales, 6 combined.**

1. **Scraper** (`scrape_tribunal_decisions.py`) — GOV.UK Search API in batches,
   with an explicit `order` so the pagination windows are stable. Parses titles
   into address, case reference and region code. Writes
   `data/tribunal_decisions.json`. This is the *raw* index: it still contains
   typo'd years and missing region codes, which stage 3 repairs.

2. **Enricher** (`enrich_tribunal_decisions.py`) — GOV.UK Content API over a
   thread pool, adding `full_text`, attachments and PDF URLs. Workers are pure:
   they return field dicts and only the main thread mutates records, because
   mutating them while the main thread serialised the same structure could abort
   a save. Resumable, checkpointing every 100 records; Ctrl-C cancels queued
   work and saves.

3. **Field extractor** (`extract_structured_fields.py`) — regex extraction from
   `full_text`, no network, ~45s. Also repairs `decision_date`, `region_code`
   and `case_reference`. Fields in `RECOMPUTED_FIELDS` are cleared before each
   run so output from an older version cannot survive.

4. **PDF fetcher** (`fetch_pdfs.py`) — downloads and extracts text for decisions
   with no inline text. PDFs go to gitignored `data/pdfs/`; the manifest records
   *what was downloaded*, not the text.

5. **Wales scraper** (`scrape_wales_decisions.py`) — list pages × fiscal years,
   then detail pages, then PDFs. Merges into the existing dataset on `url`.

6. **Site builder** (`build_site_data.py`) — writes `docs/data/decisions.json`,
   the sharded search index, hub pages, the sitemap, and the generated blocks in
   `index.html`, `llms.txt` and `README.md`.

**Frontend** (`docs/`) — vanilla HTML/CSS/JS, no framework, no npm. Rows are
built with DOM APIs rather than HTML strings so escaping cannot be forgotten.
Filter state lives in the query string.

## Traps worth knowing

These all caused real, published defects.

- **Replacing a scraped dataset instead of merging destroys data.** A crawl is
  partial for ordinary reasons. `scrape_wales_decisions.py` merges on `url` and
  refuses to shrink the dataset without `--allow-shrink`.
- **Stage 3 must follow stage 2.** `ENRICHMENT_FIELDS` and `REPAIRED_FIELDS` in
  the enricher carry repaired values forward; only stage 3 recomputes them.
- **One Act name nests inside another.** "Leasehold Reform Act" inside
  "Commonhold and Leasehold Reform Act", "Housing Act" inside "Local Government
  and Housing Act". Both leaked, publishing thousands of citations of Acts that
  do not exist. `LEGAL_ACTS` uses negative lookbehinds and a per-Act list of
  real years; unrecognised years are dropped and reported.
- **A document-frequency ceiling on the search index removes the words people
  search for.** A 5% cut deleted the entire legal vocabulary; an 80% cut deleted
  "landlord", "tenant" and "tribunal". There is no ceiling now — only stopwords
  are excluded. `verify_data.py` asserts specific legal terms are findable.
- **`FRONTEND_FIELDS` is an allowlist.** It replaced a denylist that shipped
  9.7 MB of never-read fields to every visitor.
- **`Path.exists()` is true for an unfetched Git LFS pointer.** `build_site_data.py`
  checks the file's first bytes and stops with a clear message.

## Data structures

All data files are `{ "metadata": {...}, "decisions": [...] }`.

The enriched file adds `full_text`, `attachments`, `pdf_urls`, `applicant`,
`respondent`, `application_type`, `content_id`; extraction adds
`tribunal_members`, `presiding_judge`, `decision_outcome`, `financial_amounts`
(floats), `hearing_date` (ISO 8601), `legal_acts_cited`, and `text_source:
"pdf"` where applicable. Wales records add `data_source: "wales"`, `pdf_url`,
and `decision_date_approximate: true` — the Wales tribunal publishes only a
month and year, so the day in those dates is a placeholder.

`docs/data/decisions.json` carries only `FRONTEND_FIELDS` plus a top-level
`stats` object. Full text is not in it; it is in the search shards.

Search shards (`docs/data/search/<xx>.json`) map term → base-36 delta-encoded
document ids indexing into the `decisions` array. `shards.json` lists the
prefixes. Frequent adjacent word pairs are indexed joined by `_` for phrase
queries.

**Use `url` as the record key.** `case_reference` is neither unique nor always
present.

## Other details

- `data/tribunal_decisions_full.json` is Git LFS (`.gitattributes`).
- `data/pdfs/` and `data/wales_pdfs/` are gitignored.
- GOV.UK APIs are public and need no authentication. The scrapers rate-limit,
  back off on 429 without spending their retry budget, and write atomically.
- Region codes: LON, CHI, MAN, BIR, CAM, HAV, NS, TR, NT, VG, NAT, GB, RC, WAL.
  `build_site_data.py` maps an empty code to `Unknown` for the stats block.
- These records name individuals at their home addresses. `PRIVACY.md` records
  the position and the takedown route; the site deliberately publishes no
  per-decision pages.
