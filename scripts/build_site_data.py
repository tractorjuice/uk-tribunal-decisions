#!/usr/bin/env python3
"""Convert tribunal_decisions.json into a slim JSON file for the GitHub Pages site."""

import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DOCS_DATA_DIR = SCRIPT_DIR.parent / "docs" / "data"

INPUT = DATA_DIR / "tribunal_decisions.json"
OUTPUT = DOCS_DATA_DIR / "decisions.json"


def main():
    print(f"Reading {INPUT} ...")
    with open(INPUT) as f:
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
    }

    output = {
        "stats": stats,
        "decisions": decisions,
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
