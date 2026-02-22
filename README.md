# UK Residential Property Tribunal Decisions Database

A complete database of all First-tier Tribunal (Property Chamber) residential property decisions published on GOV.UK.

## Browse the Database

**[View the searchable database online](https://tractorjuice.github.io/uk-tribunal-decisions/)** — search and filter all 16,110 decisions by category, region, year, and keyword.

To configure GitHub Pages: set the source to the `/docs` directory on the `main` branch in your repository settings.

## Contents

```
Tribunal-Decisions/
├── data/
│   ├── tribunal_decisions.json        # Index database (16,110 decisions, ~14 MB)
│   └── tribunal_decisions_full.json   # Full database with decision text (~306 MB, Git LFS)
├── docs/                              # GitHub Pages site
│   ├── index.html                     # Searchable decisions browser
│   ├── css/style.css
│   ├── js/app.js
│   └── data/decisions.json            # Site data (generated from index)
├── scripts/
│   ├── scrape_tribunal_decisions.py   # Scrapes decision metadata from GOV.UK search API
│   ├── enrich_tribunal_decisions.py   # Enriches with full text via GOV.UK content API
│   └── build_site_data.py            # Generates docs/data/decisions.json from index
└── README.md
```

## Data

### tribunal_decisions.json (Index)

Metadata for all 16,110 decisions scraped from the GOV.UK search API:

- `title`, `description`, `link`
- `case_reference`, `property_address`, `region_code`
- `tribunal_decision_category`, `tribunal_decision_sub_category`
- `tribunal_decision_decision_date`, `public_timestamp`

### tribunal_decisions_full.json (Full Text)

Enriched version with additional fields from the GOV.UK content API:

- `full_text` — complete decision text (98.9% coverage)
- `attachments` — PDF URLs and metadata (99.8% coverage, 19,244 PDFs)
- `content_id` — GOV.UK content UUID
- `applicant`, `respondent`, `application_type` — parsed from text

## Scripts

### Scraping

```bash
# Scrape all decision metadata (takes ~5 minutes)
python3 scripts/scrape_tribunal_decisions.py

# Enrich with full text and PDFs (takes ~15 minutes, resumes on interruption)
python3 scripts/enrich_tribunal_decisions.py
```

Both scripts accept `--output` / `--input` flags to override default paths. By default they read/write to `data/`.

### Requirements

```
pip install requests
```

## API Sources

- **Search API:** `https://www.gov.uk/api/search.json?filter_document_type=residential_property_tribunal_decision`
- **Content API:** `https://www.gov.uk/api/content/{path}` (for individual decisions)

## Statistics

| Metric | Value |
|--------|-------|
| Total decisions | 16,110 |
| With full text | 15,935 (98.9%) |
| With PDF attachments | 16,070 (99.8%) |
| Total PDF files | 19,244 |
| With applicant parsed | 12,009 (74.5%) |
| Average text length | 17,795 chars |
| Last scraped | 21 February 2026 |
