# UK Residential Property Tribunal Decisions

A searchable database of residential property tribunal decisions from England
(First-tier Tribunal, Property Chamber, via GOV.UK) and Wales (Residential
Property Tribunal Wales).

**[Search the database](https://tractorjuice.github.io/uk-tribunal-decisions/)**
· **[Browse by category, region or year](https://tractorjuice.github.io/uk-tribunal-decisions/browse/)**

Every count on the site and in this README is generated from the data by
`scripts/build_site_data.py`. Don't edit them by hand — they get overwritten,
and hand-maintained counts are why five different places used to disagree about
how many decisions there were.

## Contents

```
data/
  tribunal_decisions.json          England index, raw from the search API
  tribunal_decisions_full.json     England, enriched with full text (Git LFS)
  wales_tribunal_decisions.json    Wales, with text extracted from PDFs
  pdf_manifest.json                Which England PDFs were downloaded
  wales_pdf_manifest.json          Which Wales PDFs were downloaded
docs/                              The published site (GitHub Pages serves /docs)
  index.html  css/  js/
  data/decisions.json              Record index the page loads at startup
  data/search/                     Sharded full-text index, fetched per query
  browse/                          Generated hub pages
scripts/
  scrape_tribunal_decisions.py     1. England metadata from the GOV.UK Search API
  enrich_tribunal_decisions.py     2. England full text from the GOV.UK Content API
  extract_structured_fields.py     3. Structured fields from the decision text
  fetch_pdfs.py                    4. PDFs for decisions with no inline text
  scrape_wales_decisions.py        5. Wales decisions and PDFs
  build_site_data.py               6. Everything the site serves
  verify_data.py                   Validates the published data
  test_extraction.py               Unit tests for the extraction regexes
```

## Running the pipeline

```bash
pip install -r requirements.txt      # Python 3.10+
```

England, in order:

```bash
python3 scripts/scrape_tribunal_decisions.py     # ~5 min
python3 scripts/enrich_tribunal_decisions.py     # ~15 min, resumable
python3 scripts/extract_structured_fields.py     # ~45 sec, no network
```

Stage 3 must follow stage 2. Enrichment carries previously repaired fields
forward, but only extraction recomputes them from the text.

Optionally, for the decisions whose text is only in an attached PDF (downloads
to a gitignored directory, several GB with `--all`):

```bash
python3 scripts/fetch_pdfs.py
python3 scripts/extract_structured_fields.py     # re-run over the new text
```

Wales:

```bash
python3 scripts/scrape_wales_decisions.py                 # ~30-45 min
python3 scripts/scrape_wales_decisions.py --sample 5      # safe smoke test
```

`--sample` is safe: the scraper merges into the existing dataset rather than
replacing it, and refuses to write fewer decisions than are already on disk
unless you pass `--allow-shrink`.

Then build the site:

```bash
python3 scripts/build_site_data.py
python3 scripts/verify_data.py       # blocks obviously broken output
```

This all runs automatically every Monday — see `.github/workflows/refresh-data.yml`.

## How the search works

The page loads `docs/data/decisions.json` (record metadata only) and searches
that directly. Full-text search uses a separate inverted index under
`docs/data/search/`, sharded on the first two letters of each term, so a query
downloads roughly 30 KB rather than the whole index.

Postings are base-36 delta-encoded document ids indexing into the `decisions`
array; `shards.json` lists the available prefixes. The most frequent adjacent
word pairs are indexed as single terms joined by `_`, so a two-word query like
`service charge` is answered as a phrase rather than as an AND over two words
that separately appear in most decisions.

Every word in a query must appear. There is no phrase search beyond the indexed
pairs, and words of four letters or more also match longer words starting with
them.

## Data notes

- `case_reference` is **not** unique and is missing on a small number of
  records. Use `url` as the key.
- Wales decisions carry `decision_date_approximate: true`. The Wales tribunal
  publishes only a month and year, so the day in those dates is a placeholder.
- `data/tribunal_decisions.json` is the raw scrape, before repair. It still
  contains typo'd years and missing region codes. The corrected data is in
  `tribunal_decisions_full.json` and everything built from it.
- `legal_acts_cited` only includes Acts whose year matches a real Act. OCR of
  scanned decisions corrupts years often enough that unrecognised ones are
  dropped rather than published — see `LEGAL_ACTS` in
  `scripts/extract_structured_fields.py`.

## Statistics

<!-- BEGIN:generated-stats -->
| Metric | Count |
|--------|-------|
| Decisions | 18,764 |
| Date range | 2001-05-28 to 2026-09-30 |
| Categories | 14 |
| Tribunal regions | 15 |
| With applicant | 17,833 (95.0%) |
| With respondent | 17,865 (95.2%) |
| With tribunal members | 15,465 (82.4%) |
| With presiding judge | 15,465 (82.4%) |
| With decision outcome | 10,152 (54.1%) |
| With financial amounts | 16,521 (88.0%) |
| With hearing date | 3,159 (16.8%) |
| With legal acts cited | 17,992 (95.9%) |

_Generated by `scripts/build_site_data.py` on 2026-08-24._
<!-- END:generated-stats -->

## API sources

- GOV.UK Search API — `https://www.gov.uk/api/search.json?filter_document_type=residential_property_tribunal_decision`
- GOV.UK Content API — `https://www.gov.uk/api/content/{path}`
- Wales Tribunal — `https://residentialpropertytribunal.gov.wales/decisions/{type_id}/{year_range}`

Both are public and need no authentication.

## Licence and privacy

The code is MIT licensed — see [LICENSE](LICENSE).

The decision data is public sector information licensed under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
The OGL is a copyright licence and does not extend to personal data. These
records name individuals and their addresses, so please read
[PRIVACY.md](PRIVACY.md), which also explains how to ask for a record to be
corrected or removed.

This project is not affiliated with or endorsed by GOV.UK, HMCTS, the Welsh
Government, or any tribunal.
