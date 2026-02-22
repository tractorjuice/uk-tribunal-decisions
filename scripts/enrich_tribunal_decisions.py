#!/usr/bin/env python3
"""
Enrich tribunal decisions database with full decision text and PDF attachment URLs.

Reads the existing tribunal_decisions.json, fetches the GOV.UK content API for
each decision to extract:
  - Full decision text (hidden_indexable_content)
  - PDF attachment URLs and metadata
  - Content UUID
  - Applicant/respondent details parsed from the text

Saves progress incrementally to avoid data loss on interruption.

Usage:
    python3 enrich_tribunal_decisions.py [--input FILE] [--output FILE] [--concurrency N]
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock

import requests

CONTENT_API = "https://www.gov.uk/api/content"
MAX_RETRIES = 3
RETRY_DELAY = 2
SAVE_EVERY = 100  # Save progress every N decisions
REQUEST_DELAY = 0.15  # Seconds between requests per thread


progress_lock = Lock()
save_lock = Lock()
stats = {"fetched": 0, "errors": 0, "skipped": 0}


def fetch_decision_detail(gov_uk_path: str, session: requests.Session) -> dict | None:
    """Fetch full decision details from the GOV.UK content API."""
    url = CONTENT_API + gov_uk_path

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2) * 3
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                return None
    return None


def extract_attachments(details: dict) -> list:
    """Extract PDF attachment info from the details object."""
    attachments = details.get("details", {}).get("attachments", [])
    result = []
    for att in attachments:
        result.append({
            "title": att.get("title", ""),
            "url": att.get("url", ""),
            "content_type": att.get("content_type", ""),
            "content_id": att.get("content_id", ""),
        })
    return result


def extract_parties(text: str) -> dict:
    """Try to extract applicant/respondent from decision text."""
    parties = {}

    # Try to find applicant(s)
    app_match = re.search(
        r'Applicants?\s*(?:\/\s*Tenants?)?\s*:?\s*(.+?)(?:\n|Respondent|Representative)',
        text, re.IGNORECASE | re.DOTALL
    )
    if app_match:
        applicant = app_match.group(1).strip()
        applicant = re.sub(r'\s+', ' ', applicant)
        if len(applicant) < 300:
            parties["applicant"] = applicant

    # Try to find respondent(s)
    resp_match = re.search(
        r'Respondents?\s*(?:\/\s*Landlords?)?\s*:?\s*(.+?)(?:\n|Representative|Solicitor|Type of|Date of|Tribunal)',
        text, re.IGNORECASE | re.DOTALL
    )
    if resp_match:
        respondent = resp_match.group(1).strip()
        respondent = re.sub(r'\s+', ' ', respondent)
        if len(respondent) < 300:
            parties["respondent"] = respondent

    # Try to find legal basis / type of application
    type_match = re.search(
        r'Type of (?:Application|application)\s*:?\s*(.+?)(?:\n|Tribunal|Date)',
        text, re.IGNORECASE
    )
    if type_match:
        app_type = type_match.group(1).strip()
        app_type = re.sub(r'\s+', ' ', app_type)
        if len(app_type) < 200:
            parties["application_type"] = app_type

    return parties


def process_decision(idx: int, decision: dict, session: requests.Session) -> dict:
    """Fetch and enrich a single decision."""
    path = decision.get("gov_uk_path", "")
    if not path:
        return decision

    # Skip if already enriched
    if decision.get("full_text"):
        with progress_lock:
            stats["skipped"] += 1
        return decision

    time.sleep(REQUEST_DELAY)

    detail = fetch_decision_detail(path, session)
    if detail is None:
        with progress_lock:
            stats["errors"] += 1
        decision["_enrichment_error"] = True
        return decision

    # Extract data
    details_obj = detail.get("details", {})
    metadata = details_obj.get("metadata", {})

    full_text = metadata.get("hidden_indexable_content", "")
    attachments = extract_attachments(detail)
    content_id = detail.get("content_id", "")

    # Parse parties from full text
    parties = extract_parties(full_text) if full_text else {}

    # Enrich the decision
    decision["content_id"] = content_id
    decision["full_text"] = full_text
    decision["attachments"] = attachments
    decision["pdf_urls"] = [a["url"] for a in attachments if a["url"]]

    if parties.get("applicant"):
        decision["applicant"] = parties["applicant"]
    if parties.get("respondent"):
        decision["respondent"] = parties["respondent"]
    if parties.get("application_type"):
        decision["application_type"] = parties["application_type"]

    with progress_lock:
        stats["fetched"] += 1

    return decision


def save_progress(db: dict, output_path: str):
    """Save current state to disk."""
    with save_lock:
        tmp_path = output_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, output_path)


def main():
    parser = argparse.ArgumentParser(
        description="Enrich tribunal decisions with full text and PDFs"
    )
    parser.add_argument(
        "--input", "-i",
        default=None,
        help="Input JSON file (default: tribunal_decisions.json in script dir)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file (default: tribunal_decisions_full.json in script dir)",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=4,
        help="Number of concurrent requests (default: 4)",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "..", "data")
    if args.input is None:
        args.input = os.path.join(data_dir, "tribunal_decisions.json")
    if args.output is None:
        args.output = os.path.join(data_dir, "tribunal_decisions_full.json")

    # Load existing data - prefer output file if it exists (for resume)
    if os.path.exists(args.output):
        print(f"Resuming from {args.output}...")
        with open(args.output, "r", encoding="utf-8") as f:
            db = json.load(f)
    else:
        print(f"Loading from {args.input}...")
        with open(args.input, "r", encoding="utf-8") as f:
            db = json.load(f)

    decisions = db["decisions"]
    total = len(decisions)

    # Count already enriched
    already_done = sum(1 for d in decisions if d.get("full_text"))
    remaining = total - already_done
    print(f"Total decisions: {total:,}")
    print(f"Already enriched: {already_done:,}")
    print(f"Remaining: {remaining:,}")
    print(f"Concurrency: {args.concurrency} threads")
    print(f"Output: {args.output}")
    print()

    if remaining == 0:
        print("All decisions already enriched!")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "GrantleyGardens-TribunalResearch/1.0 (legal research)",
        "Accept": "application/json",
    })

    start_time = time.time()
    batch_count = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {}
        for idx, decision in enumerate(decisions):
            if decision.get("full_text"):
                continue
            future = executor.submit(process_decision, idx, decision, session)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                decisions[idx] = future.result()
            except Exception as e:
                print(f"  Exception processing decision {idx}: {e}")
                stats["errors"] += 1

            batch_count += 1
            done = stats["fetched"] + stats["errors"] + stats["skipped"]

            if batch_count % 25 == 0:
                elapsed = time.time() - start_time
                rate = batch_count / elapsed if elapsed > 0 else 0
                eta_secs = (remaining - batch_count) / rate if rate > 0 else 0
                eta_mins = eta_secs / 60
                print(
                    f"  Progress: {batch_count:,}/{remaining:,} "
                    f"({batch_count/remaining*100:.1f}%) | "
                    f"Rate: {rate:.1f}/sec | "
                    f"ETA: {eta_mins:.0f}min | "
                    f"Errors: {stats['errors']}"
                )

            # Save progress periodically
            if batch_count % SAVE_EVERY == 0:
                db["metadata"]["last_enrichment_save"] = datetime.now(timezone.utc).isoformat()
                db["metadata"]["enrichment_progress"] = f"{batch_count}/{remaining}"
                save_progress(db, args.output)
                print(f"  [Saved progress at {batch_count:,}]")

    # Final save
    db["metadata"]["enriched_at"] = datetime.now(timezone.utc).isoformat()
    db["metadata"]["enrichment_complete"] = True
    db["metadata"].pop("enrichment_progress", None)
    db["metadata"].pop("last_enrichment_save", None)
    save_progress(db, args.output)

    elapsed = time.time() - start_time
    file_size = os.path.getsize(args.output) / (1024 * 1024)

    print(f"\n{'=' * 60}")
    print(f"ENRICHMENT COMPLETE")
    print(f"{'=' * 60}")
    print(f"Fetched: {stats['fetched']:,}")
    print(f"Errors: {stats['errors']:,}")
    print(f"Skipped (already done): {stats['skipped']:,}")
    print(f"Time: {elapsed/60:.1f} minutes")
    print(f"File size: {file_size:.1f} MB")
    print(f"Output: {args.output}")

    # Summary stats
    with_text = sum(1 for d in decisions if d.get("full_text"))
    with_pdfs = sum(1 for d in decisions if d.get("pdf_urls"))
    with_applicant = sum(1 for d in decisions if d.get("applicant"))
    total_pdfs = sum(len(d.get("pdf_urls", [])) for d in decisions)

    print(f"\nDecisions with full text: {with_text:,}/{total:,}")
    print(f"Decisions with PDFs: {with_pdfs:,}/{total:,}")
    print(f"Total PDF attachments: {total_pdfs:,}")
    print(f"Decisions with applicant parsed: {with_applicant:,}/{total:,}")


if __name__ == "__main__":
    main()
