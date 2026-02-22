#!/usr/bin/env python3
"""Convert tribunal decisions data into a slim JSON file for the GitHub Pages site.

Reads from the enriched file (tribunal_decisions_full.json) if available, falling back
to the index file (tribunal_decisions.json). Strips large fields (full_text, attachments)
to keep the output compact for client-side use.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DOCS_DATA_DIR = SCRIPT_DIR.parent / "docs" / "data"

ENRICHED_INPUT = DATA_DIR / "tribunal_decisions_full.json"
INDEX_INPUT = DATA_DIR / "tribunal_decisions.json"
OUTPUT = DOCS_DATA_DIR / "decisions.json"

# Fields to strip from each decision (too large for frontend)
STRIP_FIELDS = {"full_text", "attachments", "content_id", "_enrichment_error", "text_source"}


def main():
    # Prefer enriched file (has structured fields), fall back to index
    if ENRICHED_INPUT.exists():
        input_path = ENRICHED_INPUT
    else:
        input_path = INDEX_INPUT

    print(f"Reading {input_path} ...")
    with open(input_path) as f:
        raw = json.load(f)

    decisions = raw["decisions"]
    print(f"  {len(decisions)} decisions loaded")

    # Compute stats
    categories = Counter()
    sub_categories = Counter()
    regions = Counter()
    years = Counter()
    cat_to_sub = {}  # category -> set of sub_categories

    for d in decisions:
        cat = d.get("category_label", "")
        sub = d.get("sub_category_label", "")
        region = d.get("region_code", "") or "Unknown"
        date = d.get("decision_date", "")
        year = date[:4] if date else ""

        if cat:
            categories[cat] += 1
        if sub:
            sub_categories[sub] += 1
        if region:
            regions[region] += 1
        if year:
            years[year] += 1

        if cat and sub:
            cat_to_sub.setdefault(cat, set()).add(sub)

    # Build category hierarchy for cascading filters
    category_hierarchy = {}
    for cat, subs in sorted(cat_to_sub.items()):
        category_hierarchy[cat] = sorted(subs)

    # Structured field coverage
    coverage = {}
    for field in ["applicant", "respondent", "tribunal_members", "presiding_judge",
                  "decision_outcome", "financial_amounts", "hearing_date", "legal_acts_cited"]:
        count = sum(1 for d in decisions if d.get(field))
        coverage[field] = count

    # Legal acts frequency
    legal_acts = Counter()
    for d in decisions:
        for act in d.get("legal_acts_cited", []):
            legal_acts[act] += 1

    stats = {
        "total": len(decisions),
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "sub_categories": dict(sorted(sub_categories.items(), key=lambda x: -x[1])),
        "regions": dict(sorted(regions.items(), key=lambda x: -x[1])),
        "years": dict(sorted(years.items())),
        "category_hierarchy": category_hierarchy,
        "date_range": {
            "earliest": min((d["decision_date"] for d in decisions if d.get("decision_date")), default=""),
            "latest": max((d["decision_date"] for d in decisions if d.get("decision_date")), default=""),
        },
        "field_coverage": coverage,
        "legal_acts": dict(sorted(legal_acts.items(), key=lambda x: -x[1])[:20]),
    }

    # Build search keyword index from full_text
    # Keep only distinctive words (appearing in <5% of documents) to stay compact
    print("  Building search keyword index...")
    doc_freq = Counter()
    doc_words = []  # parallel to decisions
    for d in decisions:
        text = d.get("full_text", "") or ""
        words = set(re.findall(r"[a-zA-Z]{3,}", text.lower())) if text else set()
        doc_words.append(words)
        for w in words:
            doc_freq[w] += 1

    max_doc_freq = len(decisions) * 0.05  # 5% threshold
    common_words = {w for w, c in doc_freq.items() if c > max_doc_freq}
    print(f"  Vocabulary: {len(doc_freq):,} total, {len(common_words):,} common (dropped)")

    # Strip large fields from decisions for frontend
    slim_decisions = []
    for i, d in enumerate(decisions):
        slim = {k: v for k, v in d.items() if k not in STRIP_FIELDS}
        # Add search keywords (distinctive words from full_text)
        distinctive = doc_words[i] - common_words
        if distinctive:
            slim["search_keywords"] = " ".join(sorted(distinctive))
        slim_decisions.append(slim)

    output = {
        "stats": stats,
        "decisions": slim_decisions,
    }

    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing {OUTPUT} ...")
    with open(OUTPUT, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"  Written {size_mb:.1f} MB")
    print("Done.")


if __name__ == "__main__":
    main()
