# Repository guidelines

Detailed architecture and pipeline notes live in **[CLAUDE.md](CLAUDE.md)**.
Practical usage lives in **[README.md](README.md)**.

This file deliberately does not restate them. It used to, and the two copies
drifted: the duplicated pipeline listing lost a required step, and the example
commit message here was already stale on the commit that introduced it.

## Working here

- Python 3.10+, 4-space indent, snake_case, `pathlib.Path` for repo paths.
  Frontend is plain HTML/CSS/JS with no build step and no npm.
- Install with `pip install -r requirements.txt`. Dependencies are pinned;
  `pdfplumber` in particular changes its text output between versions, and
  every extraction regex is calibrated against the version that produced the
  current corpus.

## Before you commit

```bash
python3 scripts/test_extraction.py    # extraction regexes
python3 scripts/verify_data.py        # published data
```

Both run in CI. `verify_data.py` exists because fabricated legal citations,
year-3034 dates and a search index missing every common legal term all reached
production unnoticed.

## Things that are easy to get wrong

- **Never write a scraped dataset by replacing it.** Merge into what is already
  on disk. A crawl can be partial for ordinary reasons — a sampled run, one list
  page returning 404 — and a replace turns that into permanent data loss.
- **`extract_structured_fields.py` must run after `enrich_tribunal_decisions.py`.**
  Enrichment carries repaired fields forward, but only extraction recomputes
  them.
- **Don't hand-edit counts** in `README.md`, `docs/index.html` or
  `docs/llms.txt`. They sit between `<!-- BEGIN:generated-* -->` markers and are
  rewritten by `scripts/build_site_data.py`.
- **Watch for one Act name inside another** when touching `LEGAL_ACTS`.
  "Leasehold Reform Act" is a substring of "Commonhold and Leasehold Reform
  Act", and "Housing Act" of "Local Government and Housing Act". Both leaks
  published thousands of citations of Acts that do not exist.
- **Anything added to `FRONTEND_FIELDS`** in `build_site_data.py` ships to every
  visitor. That list is an allowlist for a reason.

## Commits and pull requests

Short imperative summaries. Keep code, site and data-refresh changes in separate
commits where practical — a refresh rewrites tens of thousands of lines of
generated JSON and will bury a real change.

Describe which pipeline stage a PR affects, list the commands you ran, and call
out any change to generated files.

## Data handling

`data/pdfs/` and `data/wales_pdfs/` are gitignored and must stay that way.
`data/tribunal_decisions_full.json` is Git LFS; run `git lfs pull` before
building or `build_site_data.py` will stop and tell you to.

These records contain personal data. Read [PRIVACY.md](PRIVACY.md) before
changing what the site publishes or how it is indexed.
