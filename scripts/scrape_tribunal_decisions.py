#!/usr/bin/env python3
"""
Scrape all UK Residential Property Tribunal decisions from GOV.UK search API.

Outputs a JSON database file containing all decision metadata, suitable for
reuse across projects. The GOV.UK search API provides structured data including
case references, categories, decision dates, and links to full decisions.

Usage:
    python3 scrape_tribunal_decisions.py [--output FILE] [--batch-size N]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

API_URL = "https://www.gov.uk/api/search.json"
BASE_URL = "https://www.gov.uk"
FIELDS = [
    "title",
    "description",
    "link",
    "public_timestamp",
    "tribunal_decision_category",
    "tribunal_decision_sub_category",
    "tribunal_decision_decision_date",
]
DOCUMENT_TYPE = "residential_property_tribunal_decision"
DEFAULT_BATCH_SIZE = 500
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


def parse_title(title: str) -> dict:
    """Extract property address and case reference from the title field."""
    # Titles follow the pattern: "Address: CASE/REF/NUMBER"
    parts = title.rsplit(":", 1)
    if len(parts) == 2:
        return {
            "property_address": parts[0].strip(),
            "case_reference": parts[1].strip(),
        }
    return {
        "property_address": title.strip(),
        "case_reference": "",
    }


def clean_category(cat: str) -> str:
    """Convert slug-style category to readable text."""
    if not cat:
        return ""
    return cat.replace("-", " ").replace("   ", " - ").strip().title()


def fetch_batch(start: int, count: int, session: requests.Session) -> dict:
    """Fetch a batch of decisions from the GOV.UK search API."""
    params = {
        "filter_document_type": DOCUMENT_TYPE,
        "count": count,
        "start": start,
        "fields": ",".join(FIELDS),
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(API_URL, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  Retry {attempt + 1}/{MAX_RETRIES} after error: {e}")
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise


def process_result(result: dict) -> dict:
    """Transform a raw API result into a clean decision record."""
    title_parts = parse_title(result.get("title", ""))

    # Parse case reference to extract region code
    case_ref = title_parts["case_reference"]
    region_code = ""
    if case_ref:
        match = re.match(r"^([A-Z]+)/", case_ref)
        if match:
            region_code = match.group(1)

    return {
        "case_reference": case_ref,
        "property_address": title_parts["property_address"],
        "region_code": region_code,
        "description": result.get("description", ""),
        "category": result.get("tribunal_decision_category", ""),
        "category_label": clean_category(result.get("tribunal_decision_category", "")),
        "sub_category": result.get("tribunal_decision_sub_category", ""),
        "sub_category_label": clean_category(
            result.get("tribunal_decision_sub_category", "")
        ),
        "decision_date": result.get("tribunal_decision_decision_date", ""),
        "published_at": result.get("public_timestamp", ""),
        "url": BASE_URL + result.get("link", ""),
        "gov_uk_path": result.get("link", ""),
    }


def scrape_all_decisions(batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
    """Scrape all tribunal decisions from the GOV.UK API."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "GrantleyGardens-TribunalResearch/1.0 (legal research)",
            "Accept": "application/json",
        }
    )

    # First request to get total count
    print("Fetching total count...")
    initial = fetch_batch(0, 1, session)
    total = initial.get("total", 0)
    print(f"Total decisions available: {total:,}")

    all_decisions = []
    start = 0

    while start < total:
        batch_num = (start // batch_size) + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(
            f"Fetching batch {batch_num}/{total_batches} "
            f"(decisions {start + 1}-{min(start + batch_size, total)} of {total:,})..."
        )

        data = fetch_batch(start, batch_size, session)
        results = data.get("results", [])

        if not results:
            print(f"  No results returned at start={start}, stopping.")
            break

        for result in results:
            decision = process_result(result)
            all_decisions.append(decision)

        start += batch_size

        # Be polite to the API
        if start < total:
            time.sleep(1)

    return {
        "metadata": {
            "source": "GOV.UK Residential Property Tribunal Decisions",
            "source_url": "https://www.gov.uk/residential-property-tribunal-decisions",
            "api_url": API_URL,
            "total_decisions": len(all_decisions),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "description": (
                "All decisions from the First-tier Tribunal (Property Chamber) "
                "as published on GOV.UK. Covers leasehold disputes, service charges, "
                "enfranchisement, rents, housing act matters, and more."
            ),
        },
        "decisions": all_decisions,
    }


def print_summary(db: dict):
    """Print a summary of the extracted database."""
    decisions = db["decisions"]
    total = len(decisions)

    print(f"\n{'=' * 60}")
    print(f"EXTRACTION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Total decisions extracted: {total:,}")

    # Category breakdown
    categories = {}
    for d in decisions:
        cat = d["category_label"] or "Uncategorised"
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\nDecisions by category:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        pct = (count / total) * 100
        print(f"  {cat}: {count:,} ({pct:.1f}%)")

    # Date range
    dates = [d["decision_date"] for d in decisions if d["decision_date"]]
    if dates:
        print(f"\nDate range: {min(dates)} to {max(dates)}")

    # Region breakdown
    regions = {}
    for d in decisions:
        r = d["region_code"] or "Unknown"
        regions[r] = regions.get(r, 0) + 1

    print(f"\nDecisions by region code:")
    for region, count in sorted(regions.items(), key=lambda x: -x[1]):
        print(f"  {region}: {count:,}")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape UK Residential Property Tribunal decisions from GOV.UK"
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output JSON file path (default: tribunal_decisions.json in script dir)",
    )
    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of decisions per API request (default: {DEFAULT_BATCH_SIZE})",
    )
    args = parser.parse_args()

    if args.output is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        args.output = os.path.join(script_dir, "..", "data", "tribunal_decisions.json")

    print(f"Scraping all residential property tribunal decisions from GOV.UK...")
    print(f"Output: {args.output}")
    print()

    db = scrape_all_decisions(batch_size=args.batch_size)

    print(f"\nWriting to {args.output}...")
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

    file_size = os.path.getsize(args.output)
    print(f"File size: {file_size / (1024 * 1024):.1f} MB")

    print_summary(db)


if __name__ == "__main__":
    main()
