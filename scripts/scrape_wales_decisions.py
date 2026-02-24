#!/usr/bin/env python3
"""
Scrape Wales Residential Property Tribunal decisions from
residentialpropertytribunal.gov.wales.

Three-phase pipeline:
  1. List page scraping — iterate tribunal types × fiscal years to find decision URLs
  2. Detail page scraping — extract metadata (Act, Case type, Property, PDF link)
  3. PDF download + text extraction — download PDFs, extract text with pdfplumber

Resumable: loads existing output on start, skips decisions already enriched with
full_text. Saves progress every 25 decisions via atomic write.

Dependencies: requests, pdfplumber

Usage:
    python3 scrape_wales_decisions.py [--output FILE] [--skip-pdfs] [--sample N] [--delay FLOAT]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# Import extraction functions from the shared module
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from extract_structured_fields import (
    extract_applicant,
    extract_respondent,
    extract_tribunal_members,
    _filter_tribunal_members,
    extract_presiding_judge,
    extract_decision_outcome,
    _truncate_outcome,
    extract_financial_amounts,
    extract_hearing_date,
    extract_legal_acts,
)

# --- Constants ---

BASE_URL = "https://residentialpropertytribunal.gov.wales"

# Tribunal type ID -> (category slug, category label, case ref prefix)
TRIBUNAL_TYPES = {
    1: ("wales-rent-assessment", "Wales - Rent Assessment", "RAC"),
    2: ("wales-leasehold-valuation", "Wales - Leasehold Valuation", "LVT"),
    4: ("wales-residential-property", "Wales - Residential Property", "RPT"),
}

# Fiscal years: April 2012 to present
FIRST_YEAR = 2012
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds base
SAVE_EVERY = 25
OCR_THRESHOLD = 100
LIST_PAGE_DELAY = 1.0  # seconds between list page requests
DETAIL_DELAY = 0.5  # seconds between detail/PDF requests

# --- Regex patterns ---

# List page: extract decision links with case reference and property address
# Matches both patterns: with case ref (RAC/0013/09/24: Address) and without
DECISION_LINK_RE = re.compile(
    r'<a\s+href="(/[^"]+)"[^>]*>\s*'
    r'((?:RAC|LVT|RPT)/\d{4}/\d{2}/\d{2}(?:\s*&amp;\s*(?:RAC|LVT|RPT)/\d{4}/\d{2}/\d{2})*)'
    r':\s*(.+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)

# List page: fallback for decision links without case references
DECISION_LINK_FALLBACK_RE = re.compile(
    r'<a\s+href="(/[^"]+)"[^>]*>\s*(.+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)

# Detail page: metadata in <strong>Label:</strong> Value patterns within body field
DETAIL_FIELD_RE = re.compile(
    r'<strong>\s*(.+?)\s*:?\s*</strong>\s*(?:&nbsp;|\s)*(.+?)(?=<strong>|</p>|</span>|<br)',
    re.IGNORECASE | re.DOTALL,
)

# Body field container
BODY_FIELD_RE = re.compile(
    r'field--name-body[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)

# Detail page: PDF link
PDF_LINK_RE = re.compile(
    r'<a\s+href="(/sites/residentialproperty/files/[^"]+\.pdf)"',
    re.IGNORECASE,
)

# Case reference pattern for validation
CASE_REF_RE = re.compile(r'^(RAC|LVT|RPT)/\d{4}/\d{2}/\d{2}$')

# Content area: restrict link matching to the main content area
CONTENT_AREA_RE = re.compile(
    r'<(?:div|section)[^>]*class="[^"]*(?:view-content|field--name-body|content)[^"]*"[^>]*>(.*?)</(?:div|section)>',
    re.IGNORECASE | re.DOTALL,
)


# --- HTTP helpers ---

def create_session():
    """Create a requests session with appropriate headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "WalesTribunalResearch/1.0 (legal research)",
        "Accept": "text/html,application/xhtml+xml",
    })
    return session


def fetch_page(url, session, delay=0):
    """Fetch an HTML page with retry logic."""
    if delay > 0:
        time.sleep(delay)

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2) * 3
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAY * (attempt + 1)
                print(f"    Retry {attempt + 1}/{MAX_RETRIES} for {url}: {e}")
                time.sleep(wait)
            else:
                print(f"    Failed after {MAX_RETRIES} attempts: {url}: {e}")
                return None
    return None


# --- Phase 1: List page scraping ---

def generate_list_urls():
    """Generate all list page URLs: 3 tribunal types × fiscal years."""
    current_year = datetime.now().year
    # Fiscal year ends in March, so include up to next year
    last_start = current_year if datetime.now().month >= 4 else current_year - 1

    urls = []
    for type_id in TRIBUNAL_TYPES:
        for start_year in range(FIRST_YEAR, last_start + 1):
            end_year = start_year + 1
            url = f"{BASE_URL}/decisions/{type_id}/{start_year}-04--{end_year}-04"
            urls.append((type_id, start_year, url))
    return urls


def parse_list_page(html, type_id):
    """Parse decision links from a list page HTML."""
    decisions = []
    seen_slugs = set()

    # Find all decision links with case references
    for m in DECISION_LINK_RE.finditer(html):
        slug = m.group(1).strip()
        case_ref = m.group(2).strip()
        # Decode HTML entities in case reference
        case_ref = case_ref.replace("&amp;", "&")
        property_address = m.group(3).strip()
        # Clean HTML tags from property address
        property_address = re.sub(r'<[^>]+>', '', property_address).strip()

        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # Use first case reference if multiple (e.g., "RPT/0008/07/23 & RPT/0009/07/23")
        primary_ref = case_ref.split("&")[0].strip()

        decisions.append({
            "slug": slug,
            "case_reference": primary_ref,
            "property_address": property_address,
            "type_id": type_id,
        })

    return decisions


def scrape_list_pages(session, delay):
    """Phase 1: Scrape all list pages to collect decision URLs."""
    urls = generate_list_urls()
    all_decisions = []
    seen_refs = set()

    print(f"Phase 1: Scraping {len(urls)} list pages...")

    for i, (type_id, start_year, url) in enumerate(urls):
        prefix = TRIBUNAL_TYPES[type_id][2]
        html = fetch_page(url, session, delay=delay if i > 0 else 0)
        if html is None:
            print(f"  [{i+1}/{len(urls)}] {prefix} {start_year}-{start_year+1}: FAILED")
            continue

        page_decisions = parse_list_page(html, type_id)
        new_count = 0
        for d in page_decisions:
            key = d["case_reference"] or d["slug"]
            if key not in seen_refs:
                seen_refs.add(key)
                all_decisions.append(d)
                new_count += 1

        print(f"  [{i+1}/{len(urls)}] {prefix} {start_year}-{start_year+1}: "
              f"{len(page_decisions)} found, {new_count} new")

    print(f"  Total unique decisions: {len(all_decisions)}")
    return all_decisions


# --- Phase 2: Detail page scraping ---

def parse_detail_page(html):
    """Parse metadata and PDF link from a decision detail page."""
    metadata = {}

    # Extract body field content first, then parse metadata within it
    body_match = BODY_FIELD_RE.search(html)
    body_html = body_match.group(1) if body_match else html

    for m in DETAIL_FIELD_RE.finditer(body_html):
        key = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        value = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        # Clean up HTML entities and whitespace
        for old, new in [('&nbsp;', ' '), ('&amp;', '&'), ('\xa0', ' ')]:
            key = key.replace(old, new)
            value = value.replace(old, new)
        key = key.strip().rstrip(':').strip().lower()
        value = value.strip()
        if key and value:
            metadata[key] = value

    # Extract PDF link (search full page)
    pdf_match = PDF_LINK_RE.search(html)
    pdf_path = pdf_match.group(1) if pdf_match else None

    return metadata, pdf_path


def slugify_case_type(case_type):
    """Convert a case type string to a slug for sub_category."""
    if not case_type:
        return ""
    slug = case_type.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


def decision_date_from_ref(case_ref):
    """Extract decision date from case reference MM/YY portion.

    Case references like RAC/0013/09/24 -> month=09, year=24 -> 2024-09-01
    """
    m = re.match(r'(?:RAC|LVT|RPT)/\d{4}/(\d{2})/(\d{2})', case_ref)
    if not m:
        return ""
    month = int(m.group(1))
    year_short = int(m.group(2))
    # Convert 2-digit year: 00-99 -> 2000-2099
    year = 2000 + year_short
    if month < 1 or month > 12:
        return ""
    return f"{year:04d}-{month:02d}-01"


def build_decision_record(list_entry, detail_metadata, pdf_path):
    """Build a full decision record from list + detail page data."""
    type_id = list_entry["type_id"]
    category, category_label, prefix = TRIBUNAL_TYPES[type_id]
    case_ref = list_entry["case_reference"]

    # Property address: prefer detail page (more complete), fall back to list
    property_address = detail_metadata.get("property", "") or list_entry["property_address"]

    # Case type -> sub_category
    case_type = detail_metadata.get("case type", "")
    sub_slug = slugify_case_type(case_type)
    sub_category = f"{category}---{sub_slug}" if sub_slug else ""
    sub_category_label = case_type

    # Legal act from detail page
    act = detail_metadata.get("act", "")
    legal_acts_cited = [act] if act else []

    # Decision date from case reference
    decision_date = decision_date_from_ref(case_ref)

    record = {
        "case_reference": case_ref,
        "property_address": property_address,
        "region_code": "WAL",
        "description": "",
        "category": category,
        "category_label": category_label,
        "sub_category": sub_category,
        "sub_category_label": sub_category_label,
        "decision_date": decision_date,
        "published_at": "",
        "url": BASE_URL + list_entry["slug"],
        "data_source": "wales",
        "legal_acts_cited": legal_acts_cited,
    }

    if pdf_path:
        record["pdf_url"] = BASE_URL + pdf_path

    return record


def scrape_detail_pages(list_entries, session, existing_index, delay):
    """Phase 2: Fetch detail pages for each decision."""
    print(f"\nPhase 2: Scraping {len(list_entries)} detail pages...")
    records = []

    for i, entry in enumerate(list_entries):
        case_ref = entry["case_reference"]

        # Skip if already enriched
        if case_ref in existing_index and existing_index[case_ref].get("full_text"):
            records.append(existing_index[case_ref])
            if (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(list_entries)}] Skipped (already enriched)")
            continue

        url = BASE_URL + entry["slug"]
        html = fetch_page(url, session, delay=delay if i > 0 else 0)

        if html is None:
            # Build record without detail page data
            record = build_decision_record(entry, {}, None)
            records.append(record)
            print(f"  [{i+1}/{len(list_entries)}] {case_ref}: detail page FAILED")
            continue

        detail_metadata, pdf_path = parse_detail_page(html)
        record = build_decision_record(entry, detail_metadata, pdf_path)
        records.append(record)

        if (i + 1) % 25 == 0:
            pdf_status = "PDF" if pdf_path else "no PDF"
            print(f"  [{i+1}/{len(list_entries)}] {case_ref}: {pdf_status}")

    print(f"  Scraped {len(records)} decision records")
    return records


# --- Phase 3: PDF download + text extraction ---

def download_pdf(url, dest_path, session):
    """Download a PDF file."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=60, stream=True)
            if resp.status_code == 404:
                return False
            if resp.status_code == 429:
                wait = RETRY_DELAY * (attempt + 2) * 3
                print(f"    Rate limited on PDF, waiting {wait}s...")
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
        return "\n\n".join(text_parts), page_count
    except Exception as e:
        print(f"    PDF extraction error: {e}")
        return "", 0


def pdf_filename_from_url(url):
    """Generate a safe local filename from a PDF URL."""
    # URLs: /sites/residentialproperty/files/YYYY-MM/filename.pdf
    parts = url.rstrip("/").split("/")
    filename = parts[-1] if parts else "unknown.pdf"
    # Prepend year-month folder for uniqueness
    if len(parts) >= 2:
        folder = parts[-2]
        return f"{folder}_{filename}"
    return filename


def extract_structured_fields(text):
    """Extract structured fields from PDF text using shared extraction functions."""
    if not text:
        return {}

    fields = {}

    applicant = extract_applicant(text)
    if applicant:
        fields["applicant"] = applicant

    respondent = extract_respondent(text)
    if respondent:
        fields["respondent"] = respondent

    members = extract_tribunal_members(text)
    members = _filter_tribunal_members(members)
    if members:
        fields["tribunal_members"] = members
        judge = extract_presiding_judge(members)
        if judge:
            fields["presiding_judge"] = judge

    outcome = extract_decision_outcome(text)
    outcome = _truncate_outcome(outcome)
    if outcome:
        fields["decision_outcome"] = outcome

    amounts = extract_financial_amounts(text)
    if amounts:
        fields["financial_amounts"] = amounts

    hearing_date = extract_hearing_date(text)
    if hearing_date:
        fields["hearing_date"] = hearing_date

    acts = extract_legal_acts(text)
    if acts:
        fields["legal_acts_cited"] = acts

    return fields


def process_pdfs(records, session, pdf_dir, manifest_path, output_path, delay):
    """Phase 3: Download PDFs and extract text."""
    if pdfplumber is None:
        print("\nPhase 3: SKIPPED (pdfplumber not installed)")
        print("  Install with: pip install pdfplumber")
        return

    # Load existing manifest
    manifest = {"pdfs": [], "metadata": {}}
    manifest_index = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for entry in manifest.get("pdfs", []):
            if entry.get("url"):
                manifest_index[entry["url"]] = entry

    targets = [r for r in records if r.get("pdf_url") and not r.get("full_text")]
    print(f"\nPhase 3: Downloading PDFs for {len(targets)} decisions...")

    stats = {"downloaded": 0, "extracted": 0, "errors": 0, "skipped": 0, "ocr_required": 0}
    processed = 0

    for i, record in enumerate(targets):
        pdf_url = record["pdf_url"]
        case_ref = record.get("case_reference", "unknown")
        filename = pdf_filename_from_url(pdf_url)
        dest = os.path.join(pdf_dir, filename)

        # Check manifest for already-downloaded PDFs
        if pdf_url in manifest_index:
            entry = manifest_index[pdf_url]
            if entry.get("text"):
                record["full_text"] = entry["text"]
                record["text_source"] = "pdf"
                # Extract structured fields from cached text
                fields = extract_structured_fields(entry["text"])
                for k, v in fields.items():
                    if k == "legal_acts_cited":
                        # Merge with acts from detail page
                        existing = set(record.get("legal_acts_cited", []))
                        existing.update(v)
                        record["legal_acts_cited"] = sorted(existing)
                    else:
                        record[k] = v
                stats["skipped"] += 1
                processed += 1
                continue

        if delay > 0:
            time.sleep(delay)

        success = download_pdf(pdf_url, dest, session)
        if not success:
            stats["errors"] += 1
            processed += 1
            continue

        stats["downloaded"] += 1

        # Extract text
        text, page_count = extract_text_from_pdf(dest)
        ocr_needed = len(text.strip()) < OCR_THRESHOLD

        if ocr_needed:
            stats["ocr_required"] += 1

        # Update manifest
        entry = {
            "url": pdf_url,
            "local_path": dest,
            "filename": filename,
            "case_reference": case_ref,
            "page_count": page_count,
            "char_count": len(text),
            "ocr_required": ocr_needed,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }
        if text.strip():
            entry["text"] = text
            record["full_text"] = text
            record["text_source"] = "pdf"
            stats["extracted"] += 1

            # Extract structured fields from PDF text
            fields = extract_structured_fields(text)
            for k, v in fields.items():
                if k == "legal_acts_cited":
                    existing = set(record.get("legal_acts_cited", []))
                    existing.update(v)
                    record["legal_acts_cited"] = sorted(existing)
                else:
                    record[k] = v

        manifest["pdfs"].append(entry)
        manifest_index[pdf_url] = entry
        processed += 1

        if processed % 10 == 0:
            print(f"  [{processed}/{len(targets)}] Downloaded: {stats['downloaded']} | "
                  f"Extracted: {stats['extracted']} | Errors: {stats['errors']}")

        # Save periodically
        if processed % SAVE_EVERY == 0:
            save_manifest(manifest, manifest_path)
            save_decisions(records, output_path)
            print(f"  [Saved progress at {processed}]")

    # Final manifest save
    manifest["metadata"] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "total_pdfs": len(manifest["pdfs"]),
    }
    save_manifest(manifest, manifest_path)

    print(f"\n  PDF Results:")
    print(f"    Downloaded: {stats['downloaded']}")
    print(f"    Text extracted: {stats['extracted']}")
    print(f"    Skipped (cached): {stats['skipped']}")
    print(f"    Errors: {stats['errors']}")
    print(f"    OCR required: {stats['ocr_required']}")


# --- Save helpers ---

def save_manifest(manifest, path):
    """Atomic save of manifest file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save_decisions(records, output_path):
    """Atomic save of decisions file."""
    db = {
        "metadata": {
            "source": "Wales Residential Property Tribunal",
            "source_url": BASE_URL,
            "total_decisions": len(records),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "description": (
                "Decisions from the Residential Property Tribunal Wales, "
                "covering rent assessment, leasehold valuation, and residential "
                "property matters. Sourced from residentialpropertytribunal.gov.wales."
            ),
        },
        "decisions": records,
    }
    tmp = output_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    os.replace(tmp, output_path)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Wales Residential Property Tribunal decisions"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output JSON file (default: data/wales_tribunal_decisions.json)",
    )
    parser.add_argument(
        "--skip-pdfs",
        action="store_true",
        help="Skip PDF download and text extraction (phases 1-2 only)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Only process N decisions (for testing)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Override delay between requests (default: 1.0s list, 0.5s detail/PDF)",
    )
    args = parser.parse_args()

    data_dir = os.path.join(SCRIPT_DIR, "..", "data")
    if args.output is None:
        args.output = os.path.join(data_dir, "wales_tribunal_decisions.json")

    pdf_dir = os.path.join(data_dir, "wales_pdfs")
    manifest_path = os.path.join(data_dir, "wales_pdf_manifest.json")

    list_delay = args.delay if args.delay is not None else LIST_PAGE_DELAY
    detail_delay = args.delay if args.delay is not None else DETAIL_DELAY

    print(f"Wales Residential Property Tribunal Scraper")
    print(f"Output: {args.output}")
    print(f"PDF dir: {pdf_dir}")
    print()

    # Load existing output for resumability
    existing_index = {}
    existing_records = []
    if os.path.exists(args.output):
        print(f"Loading existing data from {args.output}...")
        with open(args.output, "r", encoding="utf-8") as f:
            existing_db = json.load(f)
        existing_records = existing_db.get("decisions", [])
        for r in existing_records:
            if r.get("case_reference"):
                existing_index[r["case_reference"]] = r
        print(f"  {len(existing_records)} existing decisions loaded")
        print()

    session = create_session()
    start_time = time.time()

    # Phase 1: List pages
    list_entries = scrape_list_pages(session, list_delay)

    if args.sample > 0:
        list_entries = list_entries[:args.sample]
        print(f"\n  Sampling {args.sample} decisions for testing")

    # Phase 2: Detail pages
    records = scrape_detail_pages(list_entries, session, existing_index, detail_delay)

    # Phase 3: PDF download + text extraction
    if not args.skip_pdfs:
        process_pdfs(records, session, pdf_dir, manifest_path, args.output, detail_delay)

    # Save final output
    save_decisions(records, args.output)

    elapsed = time.time() - start_time

    # Summary
    with_text = sum(1 for r in records if r.get("full_text"))
    with_pdf = sum(1 for r in records if r.get("pdf_url"))
    with_applicant = sum(1 for r in records if r.get("applicant"))
    with_respondent = sum(1 for r in records if r.get("respondent"))
    with_members = sum(1 for r in records if r.get("tribunal_members"))
    with_outcome = sum(1 for r in records if r.get("decision_outcome"))
    with_acts = sum(1 for r in records if r.get("legal_acts_cited"))

    # Category breakdown
    categories = {}
    for r in records:
        cat = r.get("category_label", "Unknown")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n{'=' * 60}")
    print(f"WALES SCRAPE COMPLETE")
    print(f"{'=' * 60}")
    print(f"Total decisions: {len(records)}")
    print(f"With PDF URL: {with_pdf}")
    print(f"With full_text: {with_text}")
    print(f"Time: {elapsed / 60:.1f} minutes")
    print(f"\nBy category:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    print(f"\nField coverage:")
    print(f"  applicant: {with_applicant}")
    print(f"  respondent: {with_respondent}")
    print(f"  tribunal_members: {with_members}")
    print(f"  decision_outcome: {with_outcome}")
    print(f"  legal_acts_cited: {with_acts}")
    print(f"\nOutput: {args.output}")


if __name__ == "__main__":
    main()
