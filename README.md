# UK Residential Property Tribunal Decisions Database

A complete database of residential property tribunal decisions from England (GOV.UK) and Wales (residentialpropertytribunal.gov.wales).

## Browse the Database

**[View the searchable database online](https://tractorjuice.github.io/uk-tribunal-decisions/)** — search and filter all 16,873 decisions by category, region, year, and keyword.

To configure GitHub Pages: set the source to the `/docs` directory on the `main` branch in your repository settings.

## Contents

```
Tribunal-Decisions/
├── data/
│   ├── tribunal_decisions.json          # England index (16,110 decisions, ~14 MB)
│   ├── tribunal_decisions_full.json     # England full text (~306 MB, Git LFS)
│   ├── wales_tribunal_decisions.json    # Wales decisions with full text (~9 MB)
│   ├── pdf_manifest.json               # England PDF download manifest
│   └── wales_pdf_manifest.json         # Wales PDF download manifest
├── docs/                                # GitHub Pages site
│   ├── index.html
│   ├── css/style.css
│   ├── js/app.js
│   └── data/decisions.json              # Site data (merged England + Wales)
├── scripts/
│   ├── scrape_tribunal_decisions.py     # Scrape England metadata from GOV.UK
│   ├── enrich_tribunal_decisions.py     # Enrich England with full text
│   ├── extract_structured_fields.py     # Extract structured fields from text
│   ├── fetch_pdfs.py                    # Fetch PDFs for England decisions
│   ├── scrape_wales_decisions.py        # Scrape Wales decisions + PDFs
│   └── build_site_data.py              # Build frontend data (merges both)
└── README.md
```

## Data

### England — tribunal_decisions_full.json

Enriched metadata and full text for 16,110 decisions from the GOV.UK Search and Content APIs:

- `case_reference`, `property_address`, `region_code`
- `category`, `sub_category`, `decision_date`
- `full_text` — complete decision text (99.7% coverage)
- `applicant`, `respondent` — parsed from text (~94%)
- `tribunal_members`, `presiding_judge` (~84%)
- `decision_outcome`, `financial_amounts`, `hearing_date`
- `legal_acts_cited` (~95%)

### Wales — wales_tribunal_decisions.json

763 decisions scraped from residentialpropertytribunal.gov.wales across 3 tribunal types:

- Wales - Leasehold Valuation (347 decisions)
- Wales - Rent Assessment (261 decisions)
- Wales - Residential Property (155 decisions)
- Full text extracted from PDFs (93% coverage)
- Structured fields extracted using the same regex pipeline as England

## Scripts

### England Pipeline

```bash
# 1. Scrape metadata (~5 minutes)
python3 scripts/scrape_tribunal_decisions.py

# 2. Enrich with full text (~15 minutes, resumable)
python3 scripts/enrich_tribunal_decisions.py

# 3. Extract structured fields (~45 seconds)
python3 scripts/extract_structured_fields.py

# 4. Fetch PDFs for decisions missing text
python3 scripts/fetch_pdfs.py
python3 scripts/extract_structured_fields.py
```

### Wales Pipeline

```bash
# Scrape decisions, detail pages, and PDFs (~30 minutes)
python3 scripts/scrape_wales_decisions.py

# Test with a small sample first
python3 scripts/scrape_wales_decisions.py --sample 5
```

### Build Frontend

```bash
# Merges England + Wales automatically
python3 scripts/build_site_data.py
```

### Requirements

```
pip install requests pdfplumber
```

## API Sources

- **GOV.UK Search API:** `https://www.gov.uk/api/search.json?filter_document_type=residential_property_tribunal_decision`
- **GOV.UK Content API:** `https://www.gov.uk/api/content/{path}`
- **Wales Tribunal:** `https://residentialpropertytribunal.gov.wales/decisions/{type_id}/{year_range}`

## Statistics

| Metric | England | Wales | Total |
|--------|---------|-------|-------|
| Decisions | 16,110 | 763 | 16,873 |
| With full text | 16,060 (99.7%) | 708 (92.8%) | 16,768 |
| With applicant | 15,180 (94.2%) | 703 (92.1%) | 15,883 |
| With legal acts | 15,483 (96.1%) | 763 (100%) | 16,246 |
| Tribunal members | 13,728 (85.2%) | 223 (29.2%) | 13,951 |
| Date range | 2001–present | 2012–present | 2001–present |
| Regions | 13 | 1 (WAL) | 14 |

## Licence

Data sourced from GOV.UK and the Residential Property Tribunal Wales. Contains public sector information licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
