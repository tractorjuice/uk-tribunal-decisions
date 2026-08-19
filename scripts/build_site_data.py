#!/usr/bin/env python3
"""Build everything the GitHub Pages site serves.

Reads the enriched corpus and writes:

  docs/data/decisions.json    the record index the page loads on startup
  docs/data/search/<xx>.json  sharded full-text index, fetched only when searching
  docs/browse/**.html         crawlable hub pages per category, region and year
  docs/sitemap.xml            every hub page
  docs/index.html             regenerated counts between the generated markers
  docs/llms.txt               ditto
  README.md                   ditto

Two things here used to be wrong and are worth knowing about:

1. The record payload was built with a *denylist* of six large fields, so every
   other field shipped to every visitor whether the page read it or not. It is
   an allowlist now (FRONTEND_FIELDS).

2. The search index kept only words appearing in fewer than 5% of documents, on
   the theory that common words are stopwords. In a corpus of tribunal decisions
   the domain vocabulary *is* the common vocabulary, so this deleted "landlord",
   "tenant", "lease", "rent", "service", "charge" and "tribunal" — measured
   recall for "service charge" was 14%. It is a real inverted index now, split
   into shards by first letter pair so a query downloads kilobytes, not
   megabytes.

Usage:
    python3 build_site_data.py [--input FILE] [--output FILE] [--skip-pages]
"""

import argparse
import array
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"
SEARCH_DIR = DOCS_DATA_DIR / "search"
BROWSE_DIR = DOCS_DIR / "browse"

ENRICHED_INPUT = DATA_DIR / "tribunal_decisions_full.json"
INDEX_INPUT = DATA_DIR / "tribunal_decisions.json"
WALES_INPUT = DATA_DIR / "wales_tribunal_decisions.json"
OUTPUT = DOCS_DATA_DIR / "decisions.json"

SITE_URL = "https://tractorjuice.github.io/uk-tribunal-decisions"

# Only these reach the browser. Everything else stays server-side — the page
# never read pdf_urls, tribunal_members, legal_acts_cited, financial_amounts,
# application_type, published_at, category or sub_category, and they accounted
# for 9.7 MB of a 40 MB payload.
#
# gov_uk_path is dropped too: url is the same string with a fixed prefix.
FRONTEND_FIELDS = (
    "case_reference",
    "property_address",
    "region_code",
    "description",
    "category_label",
    "sub_category_label",
    "decision_date",
    "decision_date_approximate",
    "url",
    "applicant",
    "respondent",
    "presiding_judge",
    "decision_outcome",
)

# Genuine English function words. Deliberately short: anything domain-specific
# belongs in the index, which is the whole point of the rewrite.
STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "was", "were", "are", "not",
    "but", "had", "has", "have", "been", "from", "they", "them", "their",
    "there", "then", "than", "which", "who", "whom", "whose", "what", "when",
    "where", "will", "would", "shall", "should", "could", "may", "might",
    "must", "can", "any", "all", "each", "such", "some", "more", "most",
    "other", "into", "upon", "our", "his", "her", "him", "she", "you", "your",
    "its", "it's", "one", "two", "also", "been", "being", "does", "did",
    "done", "how", "out", "off", "over", "under", "about", "after", "before",
    "between", "both", "because", "however", "therefore", "accordingly",
    "said", "same", "made", "make", "made", "given", "give", "having",
}

# No document-frequency ceiling. An earlier attempt capped this at 80%, which
# immediately dropped "landlord", "tenant" and "tribunal" — the same failure as
# the 5% cut it replaced, just further along the curve. A term appearing in
# nearly every decision still has to be indexed, because a query like
# "tribunal service charge" intersects all three terms and a missing one makes
# the whole query return nothing.
#
# The cost is small: 30 such terms across ~17,000 postings each is under 3% of
# the index, and each lives in its own shard that is only downloaded if queried.
# The only excluded words are the function words in STOPWORDS.

# Alphanumeric, two characters or more, so "20" in "section 20" and case-ref
# fragments are searchable. The frontend ignores single-character tokens.
WORD_RE = re.compile(r"[a-z0-9][a-z0-9']+")

# Phrase support.
#
# Terms are indexed individually, so a two-word query is an AND over both words
# wherever they appear. That is correct but useless for the queries this corpus
# actually gets: "section 20" as two independent words matches 93% of decisions,
# because nearly all of them contain both.
#
# Indexing every adjacent pair would roughly double the index (~30 MB). Instead
# the most frequent pairs are indexed as single terms joined by "_", which costs
# a fraction of that and covers the phrases people search for on a residential
# property tribunal database — "service charge", "section 20", "rent repayment
# order", "ground rent". The frontend joins a multi-word query the same way and
# uses the phrase posting list when one exists, falling back to the AND.
MAX_PHRASES = 4000
MIN_PHRASE_DOCS = 25
# A pair appearing in more than this share of decisions is boilerplate
# ("residential property", "property tribunal") — it cannot narrow a search
# and its posting list is the largest in the index. Discriminating phrases
# like "service charge" (48%) sit well below the line.
MAX_PHRASE_DOC_RATIO = 0.60

REGION_NAMES = {
    "LON": "London", "CHI": "Chichester (South East)", "MAN": "Manchester (North West)",
    "BIR": "Birmingham (Midlands)", "CAM": "Cambridge (East)", "HAV": "Havant (Southern)",
    "NAT": "National", "WAL": "Wales", "NS": "NS", "TR": "TR", "NT": "NT",
    "VG": "VG", "GB": "GB", "RC": "RC", "Unknown": "Unknown",
}


# --- helpers ---------------------------------------------------------------

def looks_like_lfs_pointer(path: Path) -> bool:
    """Whether a file is an unfetched Git LFS pointer rather than real content.

    Path.exists() is True for a pointer, so a bare exists() check silently sends
    the builder down the wrong branch and then dies inside json.load.
    """
    try:
        with open(path, "rb") as f:
            return f.read(42).startswith(b"version https://git-lfs")
    except OSError:
        return False


def write_atomic(path: Path, text: str):
    """Write via a temp file so an interrupt cannot truncate the target.

    decisions.json is the file GitHub Pages serves; a half-written one is a
    broken site.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "other"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def replace_generated(text: str, name: str, replacement: str) -> str:
    """Replace the content between <!-- BEGIN:name --> and <!-- END:name -->."""
    pattern = re.compile(
        rf"(<!-- BEGIN:{re.escape(name)} -->).*?(<!-- END:{re.escape(name)} -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(
            f"Marker BEGIN:{name} not found — the generated block was removed or renamed."
        )
    return pattern.sub(lambda m: m.group(1) + replacement + m.group(2), text)


# --- search index ----------------------------------------------------------

def encode_postings(doc_ids) -> str:
    """Delta-encode ascending doc ids as base-36, joined by '.'.

    Deltas rather than absolute ids because a term in 5% of documents has an
    average gap of ~20, which is one base-36 character.
    """
    out = []
    previous = 0
    for doc_id in doc_ids:
        delta = doc_id - previous
        previous = doc_id
        digits = ""
        while True:
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"[delta % 36] + digits
            delta //= 36
            if delta == 0:
                break
        out.append(digits)
    return ".".join(out)


def build_search_index(decisions, out_dir: Path):
    """Build a sharded inverted index over the full text.

    Sharded on the first two characters of each term so that searching
    "service charge" downloads the "se" and "ch" shards rather than the lot.
    """
    print("  Building inverted index...")
    # array("i") rather than list: 4 bytes per posting instead of ~36, which
    # keeps ~10M postings inside a few hundred MB on a CI runner.
    postings = defaultdict(lambda: array.array("i"))
    phrase_df = Counter()

    # Pass 1: unigram postings, and document frequencies for adjacent pairs.
    # The text is kept until pass 2 rather than caching a phrase set per
    # document — that would be tens of millions of live strings.
    for doc_id, decision in enumerate(decisions):
        text = decision.get("full_text") or ""
        if not text:
            continue
        words = WORD_RE.findall(text.lower())
        for word in set(words):
            if word not in STOPWORDS:
                postings[word].append(doc_id)
        phrase_df.update(
            frozenset(
                a + "_" + b
                for a, b in zip(words, words[1:])
                if a not in STOPWORDS and b not in STOPWORDS
            )
        )

    phrase_ceiling = len(decisions) * MAX_PHRASE_DOC_RATIO
    kept_phrases = {
        phrase for phrase, count in phrase_df.most_common(MAX_PHRASES)
        if MIN_PHRASE_DOCS <= count <= phrase_ceiling
    }
    print(f"    Phrases: {len(kept_phrases):,} indexed of "
          f"{len(phrase_df):,} distinct adjacent pairs")
    del phrase_df

    # Pass 2: postings for the phrases that survived, then release the text.
    # Ascending doc_id order keeps every posting list sorted for delta encoding.
    for doc_id, decision in enumerate(decisions):
        text = decision.pop("full_text", None)
        if not text or not kept_phrases:
            continue
        words = WORD_RE.findall(text.lower())
        for phrase in frozenset(
            a + "_" + b for a, b in zip(words, words[1:])
        ) & kept_phrases:
            postings[phrase].append(doc_id)

    shards = defaultdict(dict)
    for term, doc_ids in postings.items():
        shards[term[:2]][term] = encode_postings(doc_ids)

    total_postings = sum(len(v) for v in postings.values())
    print(f"    Vocabulary: {len(postings):,} terms (only stopwords excluded)")
    print(f"    Postings: {total_postings:,}")

    if out_dir.exists():
        for stale in out_dir.glob("*.json"):
            stale.unlink()

    total_bytes = 0
    for prefix, terms in shards.items():
        payload = json.dumps(terms, separators=(",", ":"))
        write_atomic(out_dir / f"{prefix}.json", payload)
        total_bytes += len(payload)

    manifest = sorted(shards.keys())
    write_atomic(out_dir / "shards.json", json.dumps(manifest, separators=(",", ":")))
    print(f"    Wrote {len(shards):,} shards, {total_bytes / 1048576:.1f} MB total "
          f"(median {total_bytes // max(len(shards), 1) / 1024:.0f} KB per query term)")


# --- hub pages -------------------------------------------------------------

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — UK Residential Property Tribunal Decisions</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<link rel="stylesheet" href="{root}/css/style.css">
</head>
<body>
<header>
  <h1>{heading}</h1>
  <p class="subtitle">{description}</p>
</header>
<main>
  <nav aria-label="Breadcrumb" class="static-content">
    <p><a href="{root}/">Search all decisions</a> &rsaquo; <a href="{root}/browse/">Browse</a> &rsaquo; {title}</p>
  </nav>
  <section class="static-content">
{body}
  </section>
</main>
<footer>
  <p>Data sourced from <a href="https://www.gov.uk/residential-property-tribunal-decisions" rel="noopener">GOV.UK</a>
     and <a href="https://residentialpropertytribunal.gov.wales" rel="noopener">Residential Property Tribunal Wales</a>.
     This site is not affiliated with or endorsed by GOV.UK or HMCTS.</p>
  <p>Contains public sector information licensed under the
     <a href="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/" rel="noopener">Open Government Licence v3.0</a>.
     Read our <a href="https://github.com/tractorjuice/uk-tribunal-decisions/blob/main/PRIVACY.md" rel="noopener">privacy and takedown policy</a>.</p>
</footer>
</body>
</html>
"""

# How many decisions each hub page lists. The full set stays behind the search
# interface deliberately: hub pages exist to make the collection discoverable,
# not to publish a crawlable index of named individuals at their home addresses.
RECENT_ON_HUB = 50


def render_hub(title, heading, description, canonical, body, depth):
    root = "/".join([".."] * depth) if depth else "."
    return PAGE_TEMPLATE.format(
        title=esc(title), heading=esc(heading), description=esc(description),
        canonical=canonical, body=body, root=root,
    )


def decision_rows(decisions):
    rows = []
    for d in decisions:
        date = esc(d.get("decision_date") or "—")
        if d.get("decision_date_approximate"):
            date = f"{date} <abbr title=\"Month and year only; the day is not published\">(approx.)</abbr>"
        rows.append(
            "        <tr>"
            f"<td>{date}</td>"
            f"<td>{esc(d.get('case_reference') or '—')}</td>"
            f"<td>{esc(d.get('property_address') or '—')}</td>"
            f"<td><a href=\"{esc(d.get('url') or '')}\" rel=\"noopener\">View</a></td>"
            "</tr>"
        )
    return (
        "    <table>\n"
        "      <caption>Most recent decisions</caption>\n"
        "      <thead><tr><th scope=\"col\">Date</th><th scope=\"col\">Case reference</th>"
        "<th scope=\"col\">Property address</th><th scope=\"col\">Source</th></tr></thead>\n"
        "      <tbody>\n" + "\n".join(rows) + "\n      </tbody>\n    </table>"
    )


def build_hub_pages(decisions, stats):
    """Write per-category, per-region and per-year hub pages plus an index."""
    print("  Building hub pages...")
    BROWSE_DIR.mkdir(parents=True, exist_ok=True)
    written = []

    by_category = defaultdict(list)
    by_region = defaultdict(list)
    by_year = defaultdict(list)
    for d in decisions:
        if d.get("category_label"):
            by_category[d["category_label"]].append(d)
        by_region[d.get("region_code") or "Unknown"].append(d)
        if d.get("decision_date"):
            by_year[d["decision_date"][:4]].append(d)

    def recent(items):
        return sorted(items, key=lambda x: x.get("decision_date") or "", reverse=True)[:RECENT_ON_HUB]

    groups = [
        ("category", by_category, "Category",
         lambda k: k,
         lambda k: f"{k} decisions from the First-tier Tribunal (Property Chamber) and "
                   f"Residential Property Tribunal Wales."),
        ("region", by_region, "Region",
         lambda k: REGION_NAMES.get(k, k),
         lambda k: f"Residential property tribunal decisions heard in the "
                   f"{REGION_NAMES.get(k, k)} tribunal region."),
        ("year", by_year, "Year",
         lambda k: k,
         lambda k: f"Residential property tribunal decisions issued in {k}."),
    ]

    for kind, mapping, label, name_of, describe in groups:
        (BROWSE_DIR / kind).mkdir(parents=True, exist_ok=True)
        for key, items in sorted(mapping.items()):
            slug = slugify(key)
            name = name_of(key)
            path = BROWSE_DIR / kind / f"{slug}.html"
            canonical = f"{SITE_URL}/browse/{kind}/{slug}.html"

            sub_counts = Counter(d.get("sub_category_label") or "Unspecified" for d in items)
            dates = sorted(d["decision_date"] for d in items if d.get("decision_date"))
            span = f"{dates[0][:4]}–{dates[-1][:4]}" if dates else "not recorded"

            breakdown = "\n".join(
                f"      <li>{esc(sub)} — {count:,} decision{'s' if count != 1 else ''}</li>"
                for sub, count in sub_counts.most_common(15)
            )

            body = (
                f"    <h2>About these decisions</h2>\n"
                f"    <p>This page covers <strong>{len(items):,}</strong> of the "
                f"{len(decisions):,} decisions in the database, spanning {span}. "
                f"Each links to the full decision on its official source.</p>\n"
                f"    <h3>Breakdown</h3>\n    <ul>\n{breakdown}\n    </ul>\n"
                f"    <h3>Recent decisions</h3>\n"
                f"    <p>The {min(RECENT_ON_HUB, len(items))} most recent. "
                f"<a href=\"../../\">Search all {len(items):,}</a> using the full interface.</p>\n"
                + decision_rows(recent(items))
            )

            write_atomic(path, render_hub(
                f"{name} ({label.lower()})", f"{name}", describe(key), canonical, body, depth=2))
            written.append((canonical, kind))

    # Browse index
    def link_list(kind, mapping, name_of):
        return "\n".join(
            f"      <li><a href=\"{kind}/{slugify(k)}.html\">{esc(name_of(k))}</a> — "
            f"{len(v):,}</li>"
            for k, v in sorted(mapping.items(), key=lambda kv: -len(kv[1]))
        )

    body = (
        f"    <h2>Browse {len(decisions):,} decisions</h2>\n"
        f"    <p>Every decision is also searchable by address, case reference, party name "
        f"and full text from the <a href=\"../\">main search page</a>.</p>\n"
        f"    <h3>By category</h3>\n    <ul>\n{link_list('category', by_category, lambda k: k)}\n    </ul>\n"
        f"    <h3>By tribunal region</h3>\n    <ul>\n{link_list('region', by_region, lambda k: REGION_NAMES.get(k, k))}\n    </ul>\n"
        f"    <h3>By year</h3>\n    <ul>\n{link_list('year', by_year, lambda k: k)}\n    </ul>"
    )
    write_atomic(BROWSE_DIR / "index.html", render_hub(
        "Browse", "Browse decisions",
        "Browse UK residential property tribunal decisions by category, tribunal region and year.",
        f"{SITE_URL}/browse/", body, depth=1))
    written.append((f"{SITE_URL}/browse/", "index"))

    print(f"    Wrote {len(written):,} hub pages")
    return written


def build_sitemap(hub_urls, today):
    urls = [f"{SITE_URL}/"] + [u for u, _ in hub_urls]
    entries = "\n".join(
        f"  <url>\n    <loc>{u}</loc>\n    <lastmod>{today}</lastmod>\n"
        f"    <changefreq>weekly</changefreq>\n  </url>"
        for u in urls
    )
    write_atomic(
        DOCS_DIR / "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n",
    )
    print(f"    Wrote sitemap with {len(urls):,} URLs")


# --- generated documentation blocks ----------------------------------------

def update_generated_docs(stats, today, latest_date):
    """Rewrite every hand-maintained count from the data.

    These numbers were previously updated by hand in five places, which is why
    four of the five were wrong and the head and body of index.html disagreed
    by 400 decisions.
    """
    total = stats["total"]
    cats = stats["categories"]
    regions = stats["regions"]
    coverage = stats["field_coverage"]

    # index.html — head metadata and JSON-LD
    description = (
        f"Search {total:,} residential property tribunal decisions from England and Wales, "
        f"covering leasehold disputes, rents, park homes and housing conditions from "
        f"{stats['date_range']['earliest'][:4]} to {latest_date[:4]}."
    )
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "UK Residential Property Tribunal Decisions",
        "description": description,
        "keywords": ["tribunal", "property", "leasehold", "residential", "UK", "HMCTS",
                     "First-tier Tribunal", "Property Chamber", "rents", "housing"],
        "url": f"{SITE_URL}/",
        "license": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        "isAccessibleForFree": True,
        "inLanguage": "en-GB",
        "datePublished": "2026-02-22",
        "dateModified": today,
        "temporalCoverage": f"{stats['date_range']['earliest'][:4]}/{latest_date[:4]}",
        "spatialCoverage": {"@type": "Place", "name": "England and Wales"},
        "distribution": {
            "@type": "DataDownload",
            "encodingFormat": "application/json",
            "contentUrl": f"{SITE_URL}/data/decisions.json",
        },
        "sourceOrganization": {
            "@type": "Organization",
            "name": "HM Courts & Tribunals Service",
            "url": "https://www.gov.uk/government/organisations/hm-courts-and-tribunals-service",
        },
    }

    head_block = (
        f'\n  <meta name="description" content="{esc(description)}">\n'
        f'  <meta property="og:description" content="{esc(description)}">\n'
        f'  <meta name="twitter:description" content="{esc(description)}">\n'
        f'  <script type="application/ld+json">\n'
        f'  {json.dumps(jsonld, indent=2)}\n'
        f'  </script>\n  '
    )

    cat_items = "\n".join(
        f'        <li><a href="browse/category/{slugify(name)}.html">{esc(name)}</a> — '
        f'{count:,} decisions</li>'
        for name, count in cats.items()
    )
    region_items = ", ".join(
        f'<a href="browse/region/{slugify(code)}.html">{esc(REGION_NAMES.get(code, code))}</a>'
        for code in regions
    )
    body_block = (
        f'\n      <h2>About this database</h2>\n'
        f'      <p>This database contains <strong>{total:,}</strong> decisions from the '
        f'<strong>First-tier Tribunal (Property Chamber)</strong> in England and the '
        f'<strong>Residential Property Tribunal Wales</strong>, spanning '
        f'{stats["date_range"]["earliest"][:4]} to {latest_date[:4]}. England decisions are sourced from '
        f'<a href="https://www.gov.uk/residential-property-tribunal-decisions" rel="noopener">GOV.UK</a> '
        f'and Wales decisions from '
        f'<a href="https://residentialpropertytribunal.gov.wales" rel="noopener">residentialpropertytribunal.gov.wales</a>.</p>\n'
        f'      <h3>Decision categories</h3>\n      <ul>\n{cat_items}\n      </ul>\n'
        f'      <h3>Coverage</h3>\n'
        f'      <p>Tribunal regions: {region_items}.</p>\n'
        f'      <p>Each record includes the decision date, case reference, property address, '
        f'category, and a link to the full decision. Structured fields are extracted from the '
        f'decision text: applicant ({coverage["applicant"] / total * 100:.0f}%), '
        f'respondent ({coverage["respondent"] / total * 100:.0f}%), '
        f'presiding judge ({coverage["presiding_judge"] / total * 100:.0f}%), '
        f'outcome ({coverage["decision_outcome"] / total * 100:.0f}%).</p>\n'
        f'      <p><a href="browse/">Browse all categories, regions and years</a>.</p>\n'
        f'      <p>Data is licensed under the '
        f'<a href="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/" rel="noopener">'
        f'Open Government Licence v3.0</a>. See our '
        f'<a href="https://github.com/tractorjuice/uk-tribunal-decisions/blob/main/PRIVACY.md" rel="noopener">'
        f'privacy and takedown policy</a>.</p>\n    '
    )

    index_path = DOCS_DIR / "index.html"
    text = index_path.read_text(encoding="utf-8")
    text = replace_generated(text, "generated-head", head_block)
    text = replace_generated(text, "generated-about", body_block)
    write_atomic(index_path, text)

    # llms.txt
    llms_block = (
        f"\n- **Total decisions:** {total:,}\n"
        f"- **Date range:** {stats['date_range']['earliest']} to {latest_date}\n"
        f"- **Last updated:** {today}\n\n"
        f"### Categories\n\n"
        + "\n".join(f"- {name}: {count:,}" for name, count in cats.items())
        + "\n\n### Tribunal regions\n\n"
        + "\n".join(f"- {code} ({REGION_NAMES.get(code, code)}): {count:,}"
                    for code, count in regions.items())
        + "\n\n### Fields on each record in decisions.json\n\n"
        + "`" + "`, `".join(FRONTEND_FIELDS) + "`\n\n"
        + "Full decision text is *not* in decisions.json. It is indexed separately at "
          "`/data/search/<prefix>.json` as an inverted index: keys are terms, values are "
          "base-36 delta-encoded document ids indexing into the `decisions` array. "
          "`/data/search/shards.json` lists the available prefixes.\n"
    )
    llms_path = DOCS_DIR / "llms.txt"
    write_atomic(llms_path, replace_generated(
        llms_path.read_text(encoding="utf-8"), "generated-stats", llms_block))

    # README
    readme_block = (
        f"\n| Metric | Count |\n|--------|-------|\n"
        f"| Decisions | {total:,} |\n"
        f"| Date range | {stats['date_range']['earliest']} to {latest_date} |\n"
        f"| Categories | {len(cats)} |\n"
        f"| Tribunal regions | {len(regions)} |\n"
        + "".join(
            f"| With {field.replace('_', ' ')} | {count:,} ({count / total * 100:.1f}%) |\n"
            for field, count in coverage.items()
        )
        + f"\n_Generated by `scripts/build_site_data.py` on {today}._\n"
    )
    readme_path = ROOT / "README.md"
    write_atomic(readme_path, replace_generated(
        readme_path.read_text(encoding="utf-8"), "generated-stats", readme_block))

    print("    Updated generated blocks in index.html, llms.txt, README.md")


# --- main ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", "-i", default=None,
                        help="Enriched decisions JSON (default: data/tribunal_decisions_full.json)")
    parser.add_argument("--wales", default=None,
                        help="Wales decisions JSON (default: data/wales_tribunal_decisions.json)")
    parser.add_argument("--output", "-o", default=None,
                        help="Site data JSON (default: docs/data/decisions.json)")
    parser.add_argument("--skip-pages", action="store_true",
                        help="Skip hub pages, sitemap and documentation regeneration")
    parser.add_argument("--skip-search-index", action="store_true",
                        help="Skip rebuilding the full-text search shards")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else ENRICHED_INPUT
    wales_path = Path(args.wales) if args.wales else WALES_INPUT
    output_path = Path(args.output) if args.output else OUTPUT

    if not args.input:
        if looks_like_lfs_pointer(ENRICHED_INPUT):
            raise SystemExit(
                f"{ENRICHED_INPUT} is an unfetched Git LFS pointer.\n"
                "Run `git lfs pull` first. Refusing to fall back to the raw index, which "
                "still contains uncorrected dates and missing region codes."
            )
        if not ENRICHED_INPUT.exists():
            raise SystemExit(
                f"{ENRICHED_INPUT} not found. Run the enrichment and extraction stages "
                f"first, or pass --input {INDEX_INPUT} to build from the raw index "
                f"(note: it contains uncorrected data)."
            )

    print(f"Reading {input_path} ...")
    with open(input_path, encoding="utf-8") as f:
        raw = json.load(f)

    # Copy rather than alias: extending raw["decisions"] in place mutates the
    # loaded England database.
    decisions = list(raw["decisions"])
    print(f"  {len(decisions):,} England decisions loaded")

    if wales_path.exists():
        print(f"Reading {wales_path} ...")
        with open(wales_path, encoding="utf-8") as f:
            wales_decisions = json.load(f)["decisions"]
        decisions.extend(wales_decisions)
        print(f"  {len(wales_decisions):,} Wales decisions merged")

    print(f"  {len(decisions):,} total decisions")

    # Search index first, while full_text is still in memory.
    if not args.skip_search_index:
        build_search_index(decisions, SEARCH_DIR)

    # Stats
    categories, sub_categories, regions, years = Counter(), Counter(), Counter(), Counter()
    cat_to_sub = defaultdict(set)
    legal_acts = Counter()
    coverage_fields = ["applicant", "respondent", "tribunal_members", "presiding_judge",
                       "decision_outcome", "financial_amounts", "hearing_date",
                       "legal_acts_cited"]
    coverage = Counter()

    for d in decisions:
        cat = d.get("category_label", "")
        sub = d.get("sub_category_label", "")
        region = d.get("region_code", "") or "Unknown"
        date = d.get("decision_date", "")
        if cat:
            categories[cat] += 1
        if sub:
            sub_categories[sub] += 1
        regions[region] += 1
        if date:
            years[date[:4]] += 1
        if cat and sub:
            cat_to_sub[cat].add(sub)
        for act in d.get("legal_acts_cited") or []:
            legal_acts[act] += 1
        for field in coverage_fields:
            if d.get(field):
                coverage[field] += 1

    dates = sorted(d["decision_date"] for d in decisions if d.get("decision_date"))
    stats = {
        "total": len(decisions),
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "sub_categories": dict(sorted(sub_categories.items(), key=lambda x: -x[1])),
        "regions": dict(sorted(regions.items(), key=lambda x: -x[1])),
        "years": dict(sorted(years.items())),
        "category_hierarchy": {c: sorted(s) for c, s in sorted(cat_to_sub.items())},
        "date_range": {
            "earliest": dates[0] if dates else "",
            "latest": dates[-1] if dates else "",
        },
        "field_coverage": {f: coverage[f] for f in coverage_fields},
        "legal_acts": dict(sorted(legal_acts.items(), key=lambda x: -x[1])[:20]),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

    # Slim records — allowlist, and drop keys that are empty anyway.
    slim_decisions = [
        {k: d[k] for k in FRONTEND_FIELDS if d.get(k)}
        for d in decisions
    ]

    payload = json.dumps({"stats": stats, "decisions": slim_decisions},
                         separators=(",", ":"))
    write_atomic(output_path, payload)
    size_mb = output_path.stat().st_size / 1048576
    print(f"Writing {output_path} ...\n  Written {size_mb:.1f} MB")

    if not args.skip_pages:
        today = stats["generated_at"]
        hub_urls = build_hub_pages(decisions, stats)
        build_sitemap(hub_urls, today)
        update_generated_docs(stats, today, stats["date_range"]["latest"])

    print("Done.")


if __name__ == "__main__":
    sys.exit(main() or 0)
