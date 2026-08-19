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
MAX_RATE_LIMIT_WAITS = 8  # 429s do not consume MAX_RETRIES; bound them separately
SAVE_EVERY = 100  # Save progress every N decisions
REQUEST_DELAY = 0.15  # Seconds between requests per thread


progress_lock = Lock()
save_lock = Lock()
stats = {"fetched": 0, "errors": 0, "skipped": 0}
ENRICHMENT_FIELDS = {
    "content_id",
    "full_text",
    "attachments",
    "pdf_urls",
    "applicant",
    "respondent",
    "application_type",
    "tribunal_members",
    "presiding_judge",
    "decision_outcome",
    "financial_amounts",
    "hearing_date",
    "legal_acts_cited",
    "text_source",
    "_enrichment_error",
}

# Fields the raw index also carries, but which extract_structured_fields.py
# repairs afterwards: it fixes typo'd years (3034 -> 2034), backfills region
# codes from the case reference, and recovers missing references from the text.
#
# Without this, re-running enrichment restored the raw index values and silently
# undid every one of those repairs — several thousand of them — leaving the fix
# to survive only if stage 3 happened to be re-run afterwards.
#
# A repaired value wins only when it is non-empty, so a newly-populated upstream
# value is still picked up.
REPAIRED_FIELDS = {
    "decision_date",
    "region_code",
    "case_reference",
}


def decision_key(decision: dict) -> str:
    """Stable key for matching index records to previously enriched records."""
    return decision.get("gov_uk_path") or decision.get("url") or decision.get("case_reference", "")


def merge_latest_index(input_db: dict, existing_db: dict) -> tuple[dict, int, int]:
    """Use the latest index as the record set while preserving enrichment fields."""
    existing_by_key = {
        decision_key(decision): decision
        for decision in existing_db.get("decisions", [])
        if decision_key(decision)
    }

    merged_decisions = []
    reused = 0
    added = 0
    seen_keys = set()

    for decision in input_db["decisions"]:
        key = decision_key(decision)
        seen_keys.add(key)
        existing = existing_by_key.get(key)
        if existing:
            merged = decision.copy()
            for field in ENRICHMENT_FIELDS:
                if field in existing:
                    merged[field] = existing[field]
            for field in REPAIRED_FIELDS:
                if existing.get(field):
                    merged[field] = existing[field]
            merged_decisions.append(merged)
            reused += 1
        else:
            merged_decisions.append(decision)
            added += 1

    # Records that were enriched before but are no longer in the index — upstream
    # re-slugged or unpublished them. Dropping them would discard their full_text
    # for good, including text recovered from PDFs that are not in version
    # control. Keep them and let the operator decide.
    retired = [
        existing_by_key[key]
        for key in existing_by_key
        if key not in seen_keys
    ]
    if retired:
        print(f"  {len(retired)} previously enriched record(s) are no longer in the "
              f"index — keeping them (marked _retired_from_index)")
        for decision in retired:
            decision["_retired_from_index"] = True
            merged_decisions.append(decision)

    input_db["decisions"] = merged_decisions
    input_db.setdefault("metadata", {})
    input_db["metadata"]["previous_enriched_records_reused"] = reused
    input_db["metadata"]["new_records_for_enrichment"] = added
    input_db["metadata"]["retired_from_index"] = len(retired)
    return input_db, reused, added


def fetch_decision_detail(gov_uk_path: str, session: requests.Session) -> dict | None:
    """Fetch full decision details from the GOV.UK content API."""
    url = CONTENT_API + gov_uk_path

    attempt = 0
    rate_limit_waits = 0
    while attempt < MAX_RETRIES:
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                # Being rate limited is not a failure of this request, so it
                # does not spend an attempt. Honour Retry-After when given.
                if rate_limit_waits >= MAX_RATE_LIMIT_WAITS:
                    print(f"    Giving up after {rate_limit_waits} rate-limit waits: {url}")
                    return None
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = int(retry_after) if retry_after else RETRY_DELAY * (rate_limit_waits + 2) * 3
                except ValueError:
                    wait = RETRY_DELAY * (rate_limit_waits + 2) * 3
                rate_limit_waits += 1
                time.sleep(min(wait, 300))
                continue
            if 400 <= resp.status_code < 500:
                # Permanent client errors (403, 410) will not improve on retry.
                print(f"    HTTP {resp.status_code}: {url}")
                return None
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            attempt += 1
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                print(f"    Failed after {MAX_RETRIES} attempts: {url}: {e}")
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


def is_pdf_attachment(att: dict) -> bool:
    """Whether an attachment is actually a PDF.

    GOV.UK decisions also carry .doc, .xlsx and .csv attachments. Feeding those
    to the PDF fetcher makes pdfplumber fail on every one and report them as
    scanned documents needing OCR.
    """
    if att.get("content_type") == "application/pdf":
        return True
    return att.get("url", "").lower().split("?")[0].endswith(".pdf")


def process_decision(path: str, session: requests.Session) -> dict:
    """Fetch a single decision and return the fields to apply to it.

    Returns only new field values — it does not touch the shared decision dict.
    Workers used to mutate those dicts in place while the main thread was
    serialising the same structure to disk, which can abort a save (and the run)
    with "dictionary changed size during iteration".
    """
    time.sleep(REQUEST_DELAY)

    detail = fetch_decision_detail(path, session)
    if detail is None:
        return {"_status": "error", "_enrichment_error": True}

    details_obj = detail.get("details", {})
    metadata = details_obj.get("metadata", {})

    full_text = metadata.get("hidden_indexable_content", "")
    attachments = extract_attachments(detail)

    fields = {
        "_status": "fetched",
        "content_id": detail.get("content_id", ""),
        "full_text": full_text,
        "attachments": attachments,
        "pdf_urls": [a["url"] for a in attachments if a["url"] and is_pdf_attachment(a)],
    }

    parties = extract_parties(full_text) if full_text else {}
    for key in ("applicant", "respondent", "application_type"):
        if parties.get(key):
            fields[key] = parties[key]

    return fields


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

    # Load existing data - prefer output file if it exists (for resume), but merge
    # it with the latest input index so newly published decisions are not skipped.
    if os.path.exists(args.output):
        print(f"Resuming from {args.output}...")
        with open(args.output, "r", encoding="utf-8") as f:
            existing_db = json.load(f)
        print(f"Loading latest index from {args.input}...")
        with open(args.input, "r", encoding="utf-8") as f:
            input_db = json.load(f)
        db, reused, added = merge_latest_index(input_db, existing_db)
        print(f"Reused enriched records: {reused:,}")
        print(f"New records to enrich: {added:,}")
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

    db.setdefault("metadata", {})

    start_time = time.time()
    batch_count = 0

    interrupted = False
    executor = ThreadPoolExecutor(max_workers=args.concurrency)
    try:
        futures = {}
        for idx, decision in enumerate(decisions):
            if decision.get("full_text"):
                continue
            path = decision.get("gov_uk_path", "")
            if not path:
                continue
            future = executor.submit(process_decision, path, session)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            # Only the main thread writes to `decisions`, so a save can never
            # race a worker.
            try:
                fields = future.result()
                status = fields.pop("_status", "fetched")
                decisions[idx].update(fields)
                stats["fetched" if status == "fetched" else "errors"] += 1
            except Exception as e:
                print(f"  Exception processing decision {idx}: {e}")
                stats["errors"] += 1

            batch_count += 1

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

    except KeyboardInterrupt:
        # Without cancel_futures every queued request still runs before the
        # interrupt propagates — hours of it — so users reach for kill -9 and
        # lose everything since the last checkpoint.
        interrupted = True
        print("\nInterrupted — cancelling queued requests and saving progress...")
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    # Final save
    db["metadata"]["last_enrichment_save"] = datetime.now(timezone.utc).isoformat()
    if interrupted:
        db["metadata"]["enrichment_progress"] = f"{batch_count}/{remaining}"
        db["metadata"]["enrichment_complete"] = False
    else:
        db["metadata"]["enriched_at"] = datetime.now(timezone.utc).isoformat()
        db["metadata"]["enrichment_complete"] = True
        db["metadata"].pop("enrichment_progress", None)
        db["metadata"].pop("last_enrichment_save", None)
    save_progress(db, args.output)

    if interrupted:
        print(f"Saved {batch_count:,} of {remaining:,}. Re-run to resume.")
        return 1

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
    sys.exit(main() or 0)
