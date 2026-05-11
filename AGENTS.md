# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Python data pipeline and a static GitHub Pages site for UK residential property tribunal decisions.

- `scripts/` contains the pipeline scripts for scraping, enrichment, PDF extraction, structured-field parsing, and site-data generation.
- `data/` contains committed JSON datasets and PDF manifests. Large full-text data is tracked with Git LFS.
- `docs/` is the deployed static site. `docs/index.html`, `docs/css/style.css`, and `docs/js/app.js` make up the frontend; `docs/data/decisions.json` is generated output.
- `README.md` documents the public project and data sources. `CLAUDE.md` has detailed pipeline notes.

## Build, Test, and Development Commands

Install runtime dependencies:

```bash
pip install requests pdfplumber
```

Run the main England pipeline in order:

```bash
python3 scripts/scrape_tribunal_decisions.py
python3 scripts/enrich_tribunal_decisions.py
python3 scripts/extract_structured_fields.py
python3 scripts/fetch_pdfs.py
python3 scripts/build_site_data.py
```

Run a small Wales scrape before a full update:

```bash
python3 scripts/scrape_wales_decisions.py --sample 5
```

Rebuild the frontend data after changing source datasets or extraction logic:

```bash
python3 scripts/build_site_data.py
```

## Coding Style & Naming Conventions

Use Python 3 with 4-space indentation, `pathlib.Path` for repository paths, and clear snake_case names for functions, variables, and JSON fields. Keep scripts executable with a `#!/usr/bin/env python3` shebang and a short module docstring. Prefer structured JSON parsing and transformation over ad hoc text edits. Frontend code is plain HTML, CSS, and JavaScript with no npm toolchain.

## Testing Guidelines

No automated test framework is configured. Validate changes with focused command runs: use scraper sample flags where available, then rebuild `docs/data/decisions.json`. For extraction changes, inspect representative records in `data/tribunal_decisions_full.json`, `data/wales_tribunal_decisions.json`, and the generated site data.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `Add llms.txt for the site` and data-update messages such as `Update tribunal decisions data with latest cases (17,262 total)`. Keep commits scoped: separate code, site, and data refresh changes when practical.

Pull requests should describe the pipeline step affected, list commands run, and call out changes to generated files or decision counts. Include screenshots only for visible `docs/` UI changes.

## Data & Configuration Notes

Do not commit downloaded PDF directories such as `data/pdfs/` or `data/wales_pdfs/`; they are intentionally gitignored. Be careful with `data/tribunal_decisions_full.json`, which is large and managed through Git LFS. The public APIs do not require secrets, but scraper changes should preserve existing retry and rate-limit behavior.
