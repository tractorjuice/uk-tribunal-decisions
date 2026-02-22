#!/usr/bin/env python3
"""
Download and extract text from PDFs for tribunal decisions missing full_text.

By default, targets only the ~163 decisions that have PDF attachments but no
full_text from the GOV.UK Content API. Use --all to download all PDFs.

Dependencies: pdfplumber (pip install pdfplumber)

Usage:
    # Fetch PDFs for decisions missing full_text
    python3 fetch_pdfs.py [--input FILE] [--output FILE]

    # Test on a small sample first
    python3 fetch_pdfs.py --sample 10

    # Fetch ALL PDFs (3-9GB, ~2 hours)
    python3 fetch_pdfs.py --all

    # Resume after interruption (reads manifest)
    python3 fetch_pdfs.py
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from threading import Lock

try:
    import pdfplumber
except ImportError:
    print("Error: pdfplumber is required. Install it with: pip install pdfplumber")
    sys.exit(1)

import requests

MAX_RETRIES = 3
RETRY_DELAY = 2
REQUEST_DELAY = 0.25  # Seconds between downloads per thread
SAVE_EVERY = 25  # Save manifest every N PDFs
OCR_THRESHOLD = 100  # Characters below which PDF is flagged as ocr_required

progress_lock = Lock()
save_lock = Lock()
stats = {"downloaded": 0, "extracted": 0, "errors": 0, "skipped": 0, "ocr_required": 0}


def download_pdf(url, dest_path, session):
    """Download a PDF file from a URL."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=60, stream=True)
            if resp.status_code == 404:
                return False
            if resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2) * 3
                time.sleep(wait)
                continue
            resp.raise_for_status()

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        except requests.RequestException:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                return False
    return False


def extract_text_from_pdf(pdf_path):
    """Extract text from a PDF using pdfplumber."""
    try:
        text_parts = []
        page_count = 0
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        full_text = "\n\n".join(text_parts)
        return full_text, page_count
    except Exception:
        return "", 0


def pdf_filename_from_url(url):
    """Generate a safe filename from a PDF URL."""
    # URLs look like /government/uploads/system/uploads/attachment_data/file/12345/foo.pdf
    parts = url.rstrip("/").split("/")
    filename = parts[-1] if parts else "unknown.pdf"
    # Prepend the numeric file ID for uniqueness
    file_id = ""
    for part in reversed(parts[:-1]):
        if part.isdigit():
            file_id = part
            break
    if file_id:
        return f"{file_id}_{filename}"
    return filename


def process_decision(decision, pdf_dir, session, manifest, manifest_index):
    """Download PDFs and extract text for a single decision."""
    pdf_urls = decision.get("pdf_urls", [])
    if not pdf_urls:
        return None

    case_ref = decision.get("case_reference", "unknown")
    gov_uk_path = decision.get("gov_uk_path", "")

    all_text = []
    pdf_entries = []

    for url in pdf_urls:
        filename = pdf_filename_from_url(url)
        dest = os.path.join(pdf_dir, filename)

        # Check manifest for already-downloaded PDFs
        if url in manifest_index and os.path.exists(manifest_index[url].get("local_path", "")):
            entry = manifest_index[url]
            if entry.get("text"):
                all_text.append(entry["text"])
            pdf_entries.append(entry)
            with progress_lock:
                stats["skipped"] += 1
            continue

        time.sleep(REQUEST_DELAY)

        success = download_pdf(url, dest, session)
        if not success:
            with progress_lock:
                stats["errors"] += 1
            pdf_entries.append({
                "url": url,
                "local_path": dest,
                "error": True,
            })
            continue

        with progress_lock:
            stats["downloaded"] += 1

        # Extract text
        text, page_count = extract_text_from_pdf(dest)
        ocr_needed = len(text.strip()) < OCR_THRESHOLD

        if ocr_needed:
            with progress_lock:
                stats["ocr_required"] += 1

        if text.strip():
            all_text.append(text)
            with progress_lock:
                stats["extracted"] += 1

        entry = {
            "url": url,
            "local_path": dest,
            "filename": filename,
            "case_reference": case_ref,
            "gov_uk_path": gov_uk_path,
            "page_count": page_count,
            "char_count": len(text),
            "ocr_required": ocr_needed,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }
        if text.strip():
            entry["text"] = text
        pdf_entries.append(entry)

    combined_text = "\n\n".join(all_text) if all_text else ""
    return {
        "combined_text": combined_text,
        "pdf_entries": pdf_entries,
    }


def save_manifest(manifest, manifest_path):
    """Save PDF manifest to disk."""
    with save_lock:
        tmp_path = manifest_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, manifest_path)


def main():
    parser = argparse.ArgumentParser(
        description="Download and extract text from tribunal decision PDFs"
    )
    parser.add_argument(
        "--input", "-i",
        default=None,
        help="Input JSON file (default: data/tribunal_decisions_full.json)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file (default: same as input)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download ALL PDFs, not just those missing full_text",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Only process N decisions (for testing)",
    )
    parser.add_argument(
        "--concurrency", "-c",
        type=int,
        default=4,
        help="Number of concurrent downloads (default: 4)",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "..", "data")
    if args.input is None:
        args.input = os.path.join(data_dir, "tribunal_decisions_full.json")
    if args.output is None:
        args.output = args.input

    pdf_dir = os.path.join(data_dir, "pdfs")
    manifest_path = os.path.join(data_dir, "pdf_manifest.json")

    print(f"Loading {args.input}...")
    with open(args.input, "r", encoding="utf-8") as f:
        db = json.load(f)

    decisions = db["decisions"]
    total = len(decisions)

    # Load existing manifest
    manifest = {"pdfs": [], "metadata": {}}
    manifest_index = {}  # url -> entry for quick lookup
    if os.path.exists(manifest_path):
        print(f"Loading existing manifest from {manifest_path}...")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for entry in manifest.get("pdfs", []):
            if entry.get("url"):
                manifest_index[entry["url"]] = entry

    # Select decisions to process
    if args.all:
        targets = [d for d in decisions if d.get("pdf_urls")]
        mode = "ALL decisions with PDFs"
    else:
        targets = [d for d in decisions if d.get("pdf_urls") and not d.get("full_text")]
        mode = "decisions missing full_text"

    if args.sample > 0:
        targets = targets[:args.sample]
        mode += f" (sample of {args.sample})"

    total_pdfs = sum(len(d.get("pdf_urls", [])) for d in targets)

    print(f"Total decisions: {total:,}")
    print(f"Target: {len(targets):,} {mode}")
    print(f"Total PDFs to process: {total_pdfs:,}")
    print(f"PDF directory: {pdf_dir}")
    print(f"Concurrency: {args.concurrency} threads")
    print()

    if not targets:
        print("No decisions to process!")
        return

    session = requests.Session()
    session.headers.update({
        "User-Agent": "GrantleyGardens-TribunalResearch/1.0 (legal research)",
    })

    os.makedirs(pdf_dir, exist_ok=True)
    start_time = time.time()
    batch_count = 0

    # Process sequentially to maintain simple manifest updates
    # (PDFs within each decision are downloaded sequentially anyway)
    for i, decision in enumerate(targets):
        result = process_decision(decision, pdf_dir, session, manifest, manifest_index)
        if result is None:
            continue

        # Update manifest
        for entry in result["pdf_entries"]:
            if entry.get("url") and entry["url"] not in manifest_index:
                manifest["pdfs"].append(entry)
                manifest_index[entry["url"]] = entry

        # Fill in full_text if decision was missing it
        if not decision.get("full_text") and result["combined_text"]:
            decision["full_text"] = result["combined_text"]
            decision["text_source"] = "pdf"

        batch_count += 1

        if batch_count % 10 == 0:
            elapsed = time.time() - start_time
            rate = batch_count / elapsed if elapsed > 0 else 0
            remaining = len(targets) - batch_count
            eta_secs = remaining / rate if rate > 0 else 0
            eta_mins = eta_secs / 60
            print(
                f"  Progress: {batch_count:,}/{len(targets):,} "
                f"({batch_count / len(targets) * 100:.1f}%) | "
                f"Rate: {rate:.1f}/sec | "
                f"ETA: {eta_mins:.0f}min | "
                f"Downloaded: {stats['downloaded']} | "
                f"Errors: {stats['errors']}"
            )

        # Save periodically
        if batch_count % SAVE_EVERY == 0:
            save_manifest(manifest, manifest_path)
            print(f"  [Saved manifest at {batch_count:,}]")

    # Final saves
    manifest["metadata"] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "total_pdfs": len(manifest["pdfs"]),
        "mode": "all" if args.all else "missing_text",
    }
    save_manifest(manifest, manifest_path)

    # Save updated decisions
    print(f"\nSaving decisions to {args.output}...")
    tmp_path = args.output + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, args.output)

    elapsed = time.time() - start_time

    print(f"\n{'=' * 60}")
    print(f"PDF FETCH COMPLETE")
    print(f"{'=' * 60}")
    print(f"Decisions processed: {batch_count:,}")
    print(f"PDFs downloaded: {stats['downloaded']:,}")
    print(f"Text extracted: {stats['extracted']:,}")
    print(f"Skipped (already had): {stats['skipped']:,}")
    print(f"Errors: {stats['errors']:,}")
    print(f"OCR required (low text): {stats['ocr_required']:,}")
    print(f"Time: {elapsed / 60:.1f} minutes")
    print(f"Manifest: {manifest_path}")

    # Updated coverage
    with_text = sum(1 for d in decisions if d.get("full_text"))
    from_pdf = sum(1 for d in decisions if d.get("text_source") == "pdf")
    print(f"\nDecisions with full_text: {with_text:,}/{total:,}")
    print(f"Text from PDF extraction: {from_pdf:,}")


if __name__ == "__main__":
    main()
