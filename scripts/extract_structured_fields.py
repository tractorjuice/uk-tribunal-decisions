#!/usr/bin/env python3
"""
Extract structured fields from tribunal decision full_text using regex.

Reads tribunal_decisions_full.json and extracts additional structured fields
from the existing full_text content:
  - Improved applicant/respondent (adds Tenant/Landlord/Lessee/Freeholder patterns)
  - Tribunal members and presiding judge
  - Decision outcome
  - Financial amounts (£ values)
  - Hearing date
  - Legal acts cited

Usage:
    python3 extract_structured_fields.py [--input FILE] [--output FILE] [--overwrite]
"""

import argparse
import json
import os
import re
import time
from datetime import datetime

VALID_REGION_CODES = {'LON', 'CHI', 'MAN', 'BIR', 'CAM', 'HAV', 'NS', 'TR', 'NT', 'VG', 'NAT', 'GB', 'RC', 'WAL'}


# --- Applicant / Respondent extraction ---

def extract_applicant(text):
    """Extract applicant name from full_text, trying multiple patterns."""
    # Pattern priority: Applicant > Tenant > Lessee
    patterns = [
        r'Applicants?\s*(?:/\s*(?:Tenant|Lessee)s?)?\s*[\t :]+\s*(.+?)(?:\n|Respondent|Representative|Landlord|Freeholder)',
        r'Applicants?\s*[\t :]+\s*(.+?)(?:\n|Respondent|Representative|Landlord|Freeholder)',
        r'Tenants?\s*[\t :]+\s*(.+?)(?:\n|Landlord|Representative|Address|Type of|Date|Tribunal)',
        r'Lessees?\s*[\t :]+\s*(.+?)(?:\n|Landlord|Freeholder|Representative|Type of|Date|Tribunal)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            val = m.group(1).strip()
            val = re.sub(r'\s+', ' ', val)
            val = val.strip(' \t:')
            if 3 < len(val) < 300 and not _is_noise(val):
                return val
    return None


def extract_respondent(text):
    """Extract respondent name from full_text, trying multiple patterns."""
    patterns = [
        r'Respondents?\s*(?:/\s*(?:Landlord|Freeholder)s?)?\s*[\t :]+\s*(.+?)(?:\n|Representative|Solicitor|Type of|Date|Tribunal|Venue)',
        r'Respondents?\s*[\t :]+\s*(.+?)(?:\n|Representative|Solicitor|Type of|Date|Tribunal|Venue)',
        r'Landlords?\s*[\t :]+\s*(.+?)(?:\n|Tenant|Representative|Address|Type of|Date|Tribunal)',
        r'Freeholders?\s*[\t :]+\s*(.+?)(?:\n|Tenant|Lessee|Representative|Type of|Date|Tribunal)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            val = m.group(1).strip()
            val = re.sub(r'\s+', ' ', val)
            val = val.strip(' \t:')
            if 3 < len(val) < 300 and not _is_noise(val):
                return val
    return None


def _is_noise(val):
    """Check if extracted value is noise rather than a real name."""
    noise = [
        'n/a', 'not applicable', 'none', 'unknown', 'the tribunal',
        'see below', 'as above', 'various',
    ]
    lower = val.lower().strip()
    if lower in noise:
        return True
    # Reject if it's mostly numbers or punctuation
    alpha = sum(1 for c in val if c.isalpha())
    if alpha < 3:
        return True
    return False


def _is_bad_short_value(val):
    """Check if a value is a garbage short string (1-3 chars of punctuation/noise)."""
    if not val or not isinstance(val, str):
        return True
    stripped = val.strip()
    if len(stripped) <= 3:
        return True
    return False


# --- Tribunal members extraction ---

def extract_tribunal_members(text):
    """Extract tribunal member names from full_text."""
    members = []

    # Pattern 1: "Tribunal members :" or "Tribunal member :" block
    m = re.search(
        r'Tribunal\s+[Mm]embers?\s*[\t :]+\s*(.+?)(?=Venue|Date of|Date and|Hearing|\n\s*\n\s*\n|DECISION)',
        text, re.DOTALL
    )
    if m:
        block = m.group(1)
        members = _parse_member_block(block)
        if members:
            return members

    # Pattern 2: "Tribunal :" followed by Judge/Tribunal Judge
    m = re.search(
        r'Tribunal\s*[\t :]+\s*((?:(?:Tribunal\s+)?Judge|Deputy).+?)(?=Venue|Date of|Date and|Hearing|\n\s*\n\s*\n|DECISION)',
        text, re.DOTALL
    )
    if m:
        block = m.group(1)
        members = _parse_member_block(block)
        if members:
            return members

    # Pattern 3: "Chairman :" in fair rent decisions
    m = re.search(
        r'(?:The Tribunal members were|Tribunal members were)\s*(.+?)(?=Landlord|Tenant|$)',
        text, re.DOTALL
    )
    if m:
        block = m.group(1)
        members = _parse_member_block(block)
        if members:
            return members

    # Pattern 4: Standalone "Chairman : Name"
    m = re.search(r'Chairman\s*[\t :]+\s*([A-Z][^\n]{5,100})', text)
    if m:
        name = _clean_member_name(m.group(1).strip())
        if name:
            return [name]

    return []


def _parse_member_block(block):
    """Parse a block of text containing member names into a list."""
    members = []
    # Split on newlines
    lines = block.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Stop at section boundaries
        if re.match(r'(?:Venue|Date|Hearing|DECISION|Application|Property|Case)', line, re.IGNORECASE):
            break
        name = _clean_member_name(line)
        if name:
            members.append(name)
    return members


def _clean_member_name(raw):
    """Clean a raw tribunal member line into a name."""
    # Remove common prefixes/suffixes
    val = raw.strip(' \t:,')
    # Remove trailing date info
    val = re.sub(r'\s+Date[:\s].*$', '', val)
    val = re.sub(r'\s+Dated[:\s].*$', '', val)
    # Remove "(Chair)" "(Chairman)" etc. but keep the name
    val = re.sub(r'\s*\((?:Chair(?:man)?|Presiding)\)\s*', ' ', val, flags=re.IGNORECASE)
    val = val.strip()
    # Must start with a title or name-like character
    if not re.match(r'(?:Mr|Ms|Mrs|Miss|Dr|Prof|Judge|Deputy|Tribunal|Regional|Sir|Dame|[A-Z])', val):
        return None
    # Must be reasonable length
    if len(val) < 4 or len(val) > 150:
        return None
    # Reject lines that are section headers
    if re.match(r'(?:Venue|Date|Type|Case|Property|Hearing|Application|Representative|DECISION)', val, re.IGNORECASE):
        return None
    return val


def _filter_tribunal_members(members):
    """Filter noisy entries from tribunal members list."""
    if not members:
        return []

    noise_re = re.compile(
        r'^(?:Landlords?|Tenants?|Applicants?|Respondents?|Lessees?|Freeholders?|'
        r'None|N/?A|Not applicable|Unknown|'
        r'FHSJA|AISMA|ARLA|RICS|BSc|BA|MA|LLB|MRICS|FRICS|'
        r'See above|As above|Various)$',
        re.IGNORECASE
    )
    postcode_re = re.compile(r'[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}', re.IGNORECASE)

    filtered = []
    for member in members:
        stripped = member.strip()
        if noise_re.match(stripped):
            continue
        if postcode_re.search(member):
            continue
        if '\t' in member and re.search(
            r'\t\s*(?:Applicant|Respondent|Landlord|Tenant)', member, re.IGNORECASE
        ):
            continue
        words = stripped.split()
        if len(words) == 1 and not re.match(
            r'(?:Judge|Deputy|Chairman|Dr|Prof|Sir|Dame)', words[0], re.IGNORECASE
        ):
            continue
        filtered.append(member)

    return filtered[:5]


def extract_presiding_judge(members):
    """Identify the presiding judge from tribunal members list."""
    if not members:
        return None
    for m in members:
        if re.search(r'Judge|Chairman|Chairm', m, re.IGNORECASE):
            return m
    # If no explicit judge title, return the first member
    return members[0] if members else None


# --- Decision outcome extraction ---

def extract_decision_outcome(text):
    """Extract a brief decision outcome summary."""
    # Look for numbered decision items after DECISION header
    m = re.search(
        r'DECISION\s*\n+\s*(?:Decisions? of the Tribunal\s*\n+\s*)?(?:\(?1\)?\s*)?(.+?)(?:\n\s*\(?2\)|\n\s*\n|$)',
        text, re.DOTALL
    )
    if m:
        outcome = m.group(1).strip()
        outcome = re.sub(r'\s+', ' ', outcome)
        if 10 < len(outcome) < 500:
            return outcome

    # Look for "The tribunal determines/orders/decides"
    m = re.search(
        r'(?:The )?[Tt]ribunal (?:determines|orders|decides|grants)\s+(.+?)(?:\.\s|\n\s*\n)',
        text, re.DOTALL
    )
    if m:
        outcome = "The tribunal " + m.group(0).strip()
        outcome = re.sub(r'\s+', ' ', outcome)
        if len(outcome) < 500:
            return outcome

    # Look for "application is dismissed/allowed/refused"
    m = re.search(
        r'(?:The )?(?:application|appeal)\s+is\s+(dismissed|allowed|granted|refused|struck out)(.{0,200}?)(?:\.\s|\n)',
        text, re.IGNORECASE | re.DOTALL
    )
    if m:
        outcome = m.group(0).strip()
        outcome = re.sub(r'\s+', ' ', outcome)
        if len(outcome) < 500:
            return outcome

    return None


def _truncate_outcome(outcome):
    """Truncate overly long decision outcomes at a sentence boundary."""
    if not outcome or len(outcome) <= 200:
        return outcome
    # Find first sentence boundary after 200 chars
    idx = outcome.find('. ', 200)
    if idx != -1 and idx < 300:
        return outcome[:idx + 1]
    # Hard cap at 300
    if len(outcome) > 300:
        return outcome[:297] + '...'
    return outcome


# --- Financial amounts ---

def extract_financial_amounts(text):
    """Extract all £ amounts from the text."""
    amounts = []
    for m in re.finditer(r'£([\d,]+(?:\.\d{2})?)', text):
        raw = m.group(1).replace(',', '')
        try:
            val = float(raw)
            if val > 0:
                amounts.append(val)
        except ValueError:
            continue
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for a in amounts:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique


# --- Hearing date ---

def extract_hearing_date(text):
    """Extract hearing date from the text."""
    patterns = [
        r'Date\s+of\s+(?:Video\s+|Paper\s+|Oral\s+)?Hearing\s*[\t :]+\s*(.{5,60})',
        r'Date\s+and\s+[Vv]enue\s+of\s+(?:Hearing|hearing)\s*[\t :]+\s*(.{5,60})',
        r'Hearing\s+[Dd]ate\s*[\t :]+\s*(.{5,60})',
        r'Heard?\s+on\s*[\t :]+\s*(.{5,60})',
        r'Date\s+of\s+[Dd]etermination\s*[\t :]+\s*(.{5,60})',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(1).strip()
            # Clean the date: take just the date part
            date_m = re.match(
                r'(\d{1,2}\s*(?:st|nd|rd|th)?\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
                raw, re.IGNORECASE
            )
            if date_m:
                return _normalise_date(date_m.group(1))
            # Try DD/MM/YYYY or DD.MM.YYYY
            date_m = re.match(r'(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})', raw)
            if date_m:
                return date_m.group(1)
    return None


def _normalise_date(raw):
    """Normalise a date string, removing ordinal suffixes."""
    return re.sub(r'(\d)(st|nd|rd|th)', r'\1', raw).strip()


# --- Legal acts cited ---

LEGAL_ACTS = [
    (r'Landlord and Tenant Act\s+(\d{4})', 'Landlord and Tenant Act {}'),
    (r'Leasehold Reform[,\s]+Housing and Urban Development Act\s+(\d{4})', 'Leasehold Reform, Housing and Urban Development Act {}'),
    (r'Leasehold Reform Act\s+(\d{4})', 'Leasehold Reform Act {}'),
    (r'Housing Act\s+(\d{4})', 'Housing Act {}'),
    (r'Housing and Planning Act\s+(\d{4})', 'Housing and Planning Act {}'),
    (r'Commonhold and Leasehold Reform Act\s+(\d{4})', 'Commonhold and Leasehold Reform Act {}'),
    (r'Rent Act\s+(\d{4})', 'Rent Act {}'),
    (r'Building Safety Act\s+(\d{4})', 'Building Safety Act {}'),
    (r'Equality Act\s+(\d{4})', 'Equality Act {}'),
    (r'Protection from Eviction Act\s+(\d{4})', 'Protection from Eviction Act {}'),
    (r'Tribunal Procedure[^.]{0,50}Rules\s+(\d{4})', 'Tribunal Procedure Rules {}'),
]


def extract_legal_acts(text):
    """Extract all legal acts cited in the text."""
    acts = []
    seen = set()
    for pattern, template in LEGAL_ACTS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            year = m.group(1)
            act = template.format(year)
            if act not in seen:
                seen.add(act)
                acts.append(act)
    return acts


# --- Decision date fixing ---

def fix_decision_dates(decisions):
    """Fix decisions with obviously wrong years in decision_date.

    Some GOV.UK entries have typos in the year (e.g. 2925, 3034).
    Uses published_at as a reliable reference to derive the correct year.
    """
    current_year = datetime.now().year
    min_year = 2001
    max_year = current_year + 1
    fixed = 0

    for decision in decisions:
        date_str = decision.get("decision_date")
        published_at = decision.get("published_at")
        if not date_str or not published_at:
            continue

        # Parse year from decision_date (format: YYYY-MM-DD)
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', date_str)
        if not m:
            continue
        year = int(m.group(1))

        # Parse published_at to get the reference year (ISO 8601)
        pub_m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', published_at)
        if not pub_m:
            continue
        pub_year = int(pub_m.group(1))
        pub_month = int(pub_m.group(2))
        pub_day = int(pub_m.group(3))

        # Check 1: year outside reasonable range
        # Check 2: decision_date > published_at by 90+ days (year typo)
        needs_fix = year < min_year or year > max_year
        if not needs_fix:
            try:
                dec_date = datetime(year, int(m.group(2)), int(m.group(3)))
                pub_date = datetime(pub_year, pub_month, pub_day)
                if (dec_date - pub_date).days > 90:
                    needs_fix = True
            except ValueError:
                pass

        if not needs_fix:
            continue

        # Use published_at year as the corrected year
        corrected_year = pub_year
        dec_month = int(m.group(2))
        dec_day = int(m.group(3))

        # If corrected date would be after published_at, use pub_year - 1
        # (handles December decisions published in January)
        if (corrected_year, dec_month, dec_day) > (pub_year, pub_month, pub_day):
            corrected_year = pub_year - 1

        new_date = f"{corrected_year:04d}-{m.group(2)}-{m.group(3)}"
        ref = decision.get("case_reference", decision.get("title", "unknown"))
        print(f"  Fixed date: {date_str} -> {new_date}  [{ref}]")
        decision["decision_date"] = new_date
        fixed += 1

    return fixed


# --- Region code fixing ---

def fix_missing_region_codes(decisions):
    """Fix missing and invalid region codes by searching case_reference, property_address, and full_text."""
    ref_pattern = re.compile(
        r'\b(' + '|'.join(VALID_REGION_CODES) + r')/'
    )

    fuzzy_map = {
        'BI': 'BIR', 'LO': 'LON', 'MA': 'MAN', 'CH': 'CHI',
        'CA': 'CAM', 'HA': 'HAV',
    }

    fixed_invalid = 0
    fixed_missing = 0

    for decision in decisions:
        region = decision.get('region_code', '')

        if region and region in VALID_REGION_CODES:
            continue

        case_ref = decision.get('case_reference', '')
        prop_addr = decision.get('property_address', '')
        text = decision.get('full_text', '')

        if region and region not in VALID_REGION_CODES:
            # Invalid code - try to find valid code in case_reference
            found = None
            m = ref_pattern.search(case_ref)
            if m:
                found = m.group(1).upper()
            else:
                prefix = region[:2].upper()
                if prefix in fuzzy_map:
                    found = fuzzy_map[prefix]

            if found:
                decision['region_code'] = found
                fixed_invalid += 1
                continue

        # Missing or unfixable invalid code - search other fields
        found = None
        for source in [case_ref, prop_addr, text[:500] if text else '']:
            if not source:
                continue
            m = ref_pattern.search(source)
            if m:
                found = m.group(1).upper()
                break

        if found:
            decision['region_code'] = found
            if not case_ref and text:
                ref_m = re.search(
                    r'(' + '|'.join(VALID_REGION_CODES) + r')/\S+',
                    text[:500]
                )
                if ref_m:
                    decision['case_reference'] = ref_m.group(0)
            fixed_missing += 1

    return fixed_invalid, fixed_missing


# --- Short full_text cleanup ---

def clean_short_full_text(decisions):
    """Null out full_text that is too short to extract meaningful data from."""
    cleaned = 0
    for decision in decisions:
        text = decision.get('full_text', '')
        if text and len(text) < 100:
            decision['full_text'] = None
            cleaned += 1
    return cleaned


# --- Post-extraction cleanup ---

def clean_extracted_fields(decisions):
    """Post-extraction cleanup: remove garbage short values and extreme amounts."""
    bad_applicant = 0
    bad_respondent = 0
    bad_amounts = 0

    for decision in decisions:
        if decision.get('applicant') and _is_bad_short_value(decision['applicant']):
            decision['applicant'] = None
            bad_applicant += 1
        if decision.get('respondent') and _is_bad_short_value(decision['respondent']):
            decision['respondent'] = None
            bad_respondent += 1

        amounts = decision.get('financial_amounts', [])
        if amounts:
            filtered = [a for a in amounts if a <= 50_000_000]
            removed = len(amounts) - len(filtered)
            if removed > 0:
                bad_amounts += removed
                decision['financial_amounts'] = filtered if filtered else []

    return bad_applicant, bad_respondent, bad_amounts


# --- Main processing ---

def extract_all_fields(decision):
    """Extract all structured fields from a single decision."""
    text = decision.get("full_text", "")
    if not text:
        return {}

    fields = {}

    # Applicant - only extract if not already set
    if not decision.get("applicant"):
        applicant = extract_applicant(text)
        if applicant:
            fields["applicant"] = applicant

    # Respondent - only extract if not already set
    if not decision.get("respondent"):
        respondent = extract_respondent(text)
        if respondent:
            fields["respondent"] = respondent

    # Tribunal members (always extract, new field)
    members = extract_tribunal_members(text)
    members = _filter_tribunal_members(members)
    if members:
        fields["tribunal_members"] = members
        judge = extract_presiding_judge(members)
        if judge:
            fields["presiding_judge"] = judge

    # Decision outcome (always extract, new field)
    outcome = extract_decision_outcome(text)
    outcome = _truncate_outcome(outcome)
    if outcome:
        fields["decision_outcome"] = outcome

    # Financial amounts (always extract, new field)
    amounts = extract_financial_amounts(text)
    if amounts:
        fields["financial_amounts"] = amounts

    # Hearing date (always extract, new field)
    hearing_date = extract_hearing_date(text)
    if hearing_date:
        fields["hearing_date"] = hearing_date

    # Legal acts cited (always extract, new field)
    acts = extract_legal_acts(text)
    if acts:
        fields["legal_acts_cited"] = acts

    return fields


def main():
    parser = argparse.ArgumentParser(
        description="Extract structured fields from tribunal decision full_text"
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
        "--overwrite",
        action="store_true",
        help="Overwrite existing applicant/respondent fields",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "..", "data")
    if args.input is None:
        args.input = os.path.join(data_dir, "tribunal_decisions_full.json")
    if args.output is None:
        args.output = args.input

    print(f"Loading {args.input}...")
    with open(args.input, "r", encoding="utf-8") as f:
        db = json.load(f)

    decisions = db["decisions"]
    total = len(decisions)
    with_text = sum(1 for d in decisions if d.get("full_text"))

    print(f"Total decisions: {total:,}")
    print(f"With full_text: {with_text:,}")
    print()

    # Fix decisions with wrong years in decision_date
    print("Checking decision dates for year typos...")
    date_fixes = fix_decision_dates(decisions)
    if date_fixes:
        print(f"Fixed {date_fixes} decision date(s)\n")
    else:
        print("No date fixes needed\n")

    # Fix missing and invalid region codes
    print("Fixing missing/invalid region codes...")
    fixed_invalid, fixed_missing = fix_missing_region_codes(decisions)
    print(f"  Fixed {fixed_invalid} invalid region code(s)")
    print(f"  Fixed {fixed_missing} missing region code(s)\n")

    # Clean short full_text before extraction
    print("Cleaning short full_text entries...")
    short_cleaned = clean_short_full_text(decisions)
    print(f"  Nulled {short_cleaned} full_text entries <100 chars\n")

    # Pre-extraction stats
    pre_stats = {
        "applicant": sum(1 for d in decisions if d.get("applicant")),
        "respondent": sum(1 for d in decisions if d.get("respondent")),
        "region_code": sum(1 for d in decisions if d.get("region_code")),
    }

    start_time = time.time()
    extraction_stats = {
        "applicant_added": 0,
        "respondent_added": 0,
        "tribunal_members": 0,
        "presiding_judge": 0,
        "decision_outcome": 0,
        "financial_amounts": 0,
        "hearing_date": 0,
        "legal_acts_cited": 0,
        "processed": 0,
    }

    for i, decision in enumerate(decisions):
        if not decision.get("full_text"):
            continue

        if args.overwrite:
            # Clear existing fields for re-extraction
            for field in ["applicant", "respondent"]:
                decision.pop(field, None)

        fields = extract_all_fields(decision)

        # Apply extracted fields
        for key, value in fields.items():
            if key == "applicant" and not decision.get("applicant"):
                decision["applicant"] = value
                extraction_stats["applicant_added"] += 1
            elif key == "respondent" and not decision.get("respondent"):
                decision["respondent"] = value
                extraction_stats["respondent_added"] += 1
            else:
                decision[key] = value

        # Track new field stats
        if "tribunal_members" in fields:
            extraction_stats["tribunal_members"] += 1
        if "presiding_judge" in fields:
            extraction_stats["presiding_judge"] += 1
        if "decision_outcome" in fields:
            extraction_stats["decision_outcome"] += 1
        if "financial_amounts" in fields:
            extraction_stats["financial_amounts"] += 1
        if "hearing_date" in fields:
            extraction_stats["hearing_date"] += 1
        if "legal_acts_cited" in fields:
            extraction_stats["legal_acts_cited"] += 1

        extraction_stats["processed"] += 1

        if (i + 1) % 2000 == 0:
            print(f"  Processed {i + 1:,}/{total:,}...")

    elapsed = time.time() - start_time

    # Post-extraction cleanup
    print("\nCleaning extracted fields...")
    bad_app, bad_resp, bad_amts = clean_extracted_fields(decisions)
    print(f"  Removed {bad_app} garbage applicant value(s)")
    print(f"  Removed {bad_resp} garbage respondent value(s)")
    print(f"  Removed {bad_amts} extreme financial amount(s) (>£50M)")

    # Post-extraction stats
    post_stats = {
        "applicant": sum(1 for d in decisions if d.get("applicant")),
        "respondent": sum(1 for d in decisions if d.get("respondent")),
        "region_code": sum(1 for d in decisions if d.get("region_code")),
        "tribunal_members": sum(1 for d in decisions if d.get("tribunal_members")),
        "presiding_judge": sum(1 for d in decisions if d.get("presiding_judge")),
        "decision_outcome": sum(1 for d in decisions if d.get("decision_outcome")),
        "financial_amounts": sum(1 for d in decisions if d.get("financial_amounts")),
        "hearing_date": sum(1 for d in decisions if d.get("hearing_date")),
        "legal_acts_cited": sum(1 for d in decisions if d.get("legal_acts_cited")),
    }

    # Save
    print(f"\nSaving to {args.output}...")
    tmp_path = args.output + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, args.output)

    file_size = os.path.getsize(args.output) / (1024 * 1024)

    # Print report
    print(f"\n{'=' * 60}")
    print(f"EXTRACTION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Processed: {extraction_stats['processed']:,} decisions with text")
    print(f"Time: {elapsed:.1f} seconds")
    print(f"File size: {file_size:.1f} MB")

    print(f"\n{'=' * 60}")
    print(f"COVERAGE REPORT")
    print(f"{'=' * 60}")

    fields_report = [
        ("applicant", pre_stats["applicant"], post_stats["applicant"]),
        ("respondent", pre_stats["respondent"], post_stats["respondent"]),
        ("region_code", pre_stats["region_code"], post_stats["region_code"]),
        ("tribunal_members", 0, post_stats["tribunal_members"]),
        ("presiding_judge", 0, post_stats["presiding_judge"]),
        ("decision_outcome", 0, post_stats["decision_outcome"]),
        ("financial_amounts", 0, post_stats["financial_amounts"]),
        ("hearing_date", 0, post_stats["hearing_date"]),
        ("legal_acts_cited", 0, post_stats["legal_acts_cited"]),
    ]

    print(f"\n{'Field':<25} {'Before':>8} {'After':>8} {'Added':>8} {'Coverage':>10}")
    print(f"{'-' * 25} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 10}")
    for name, before, after in fields_report:
        added = after - before
        pct = after / total * 100
        print(f"{name:<25} {before:>8,} {after:>8,} {added:>+8,} {pct:>9.1f}%")


if __name__ == "__main__":
    main()
