#!/usr/bin/env python3
"""Validate the published site data before it goes live.

Every check here corresponds to a defect that actually shipped: dates in the
year 3034, citations of Acts of Parliament that do not exist, duplicate URLs,
region codes that are typos of real ones, and a search index whose vocabulary
had been stripped of every term anyone would search for.

Exits non-zero if any check fails, so CI blocks the commit.

Usage:
    python3 scripts/verify_data.py [--data FILE] [--strict]
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "docs" / "data" / "decisions.json"
SEARCH_DIR = ROOT / "docs" / "data" / "search"

VALID_REGION_CODES = {
    'LON', 'CHI', 'MAN', 'BIR', 'CAM', 'HAV', 'NS', 'TR', 'NT', 'VG',
    'NAT', 'GB', 'RC', 'WAL', 'Unknown',
}

# Acts that do not exist but which nested-name regex leaks used to produce.
FABRICATED_ACTS = {
    "Leasehold Reform Act 2002",
    "Housing Act 1989",
    "Housing Act 2016",
}

# Terms a legal search must be able to find. The previous index dropped every
# one of them, and nothing caught it.
REQUIRED_SEARCH_TERMS = [
    "landlord", "tenant", "lease", "leasehold", "rent", "service",
    "charge", "tribunal", "premises", "hearing", "deposit", "dispensation",
]

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Report:
    def __init__(self):
        self.failures = []
        self.warnings = []

    def check(self, ok, message):
        print(("  PASS  " if ok else "  FAIL  ") + message)
        if not ok:
            self.failures.append(message)

    def warn(self, ok, message):
        if not ok:
            print("  WARN  " + message)
            self.warnings.append(message)


def verify_records(data, report):
    decisions = data["decisions"]
    stats = data["stats"]
    today = date.today().isoformat()

    print("\nRecords")
    report.check(len(decisions) > 0, f"{len(decisions):,} decisions present")
    report.check(stats["total"] == len(decisions),
                 f"stats.total ({stats['total']:,}) matches the array length")

    urls = Counter(d.get("url") for d in decisions if d.get("url"))
    dupes = sum(c - 1 for c in urls.values() if c > 1)
    report.check(dupes == 0, f"no duplicate URLs (found {dupes})")
    report.check(len(urls) == len(decisions), "every record has a URL")

    print("\nDates")
    bad_format = [d for d in decisions
                  if d.get("decision_date") and not ISO_DATE.match(d["decision_date"])]
    report.check(not bad_format, f"all decision dates are ISO-8601 ({len(bad_format)} malformed)")

    future = [d["decision_date"] for d in decisions
              if d.get("decision_date", "") > today]
    report.check(not future,
                 f"no decision dates in the future ({len(future)} found: {sorted(set(future))[:5]})")

    too_old = [d["decision_date"] for d in decisions
               if d.get("decision_date") and d["decision_date"] < "1990-01-01"]
    report.check(not too_old, f"no decision dates before 1990 ({len(too_old)} found)")

    print("\nRegions")
    codes = Counter(d.get("region_code") or "Unknown" for d in decisions)
    unknown_codes = {c for c in codes if c not in VALID_REGION_CODES}
    report.check(not unknown_codes,
                 f"all region codes are documented (unexpected: {sorted(unknown_codes)})")

    print("\nLegal citations")
    # legal_acts_cited is not shipped per-record, but the stats block carries the
    # aggregate, which is where a fabricated Act would show up publicly.
    cited = set(stats.get("legal_acts", {}))
    fabricated = cited & FABRICATED_ACTS
    report.check(not fabricated,
                 f"no citations of non-existent Acts (found: {sorted(fabricated)})")
    implausible = {a for a in cited
                   if (m := re.search(r"(\d{4})$", a))
                   and not (1700 <= int(m.group(1)) <= date.today().year + 1)}
    report.check(not implausible,
                 f"no implausible Act years (found: {sorted(implausible)})")

    print("\nPayload")
    size_mb = SITE_DATA.stat().st_size / 1048576
    report.check(size_mb < 20,
                 f"decisions.json is {size_mb:.1f} MB (budget: 20 MB)")
    extra_fields = set()
    for d in decisions[:2000]:
        extra_fields |= set(d)
    report.warn("full_text" not in extra_fields, "full_text must not ship to the browser")
    report.warn("search_keywords" not in extra_fields,
                "search_keywords is superseded by the sharded index")


def verify_search_index(report):
    print("\nSearch index")
    manifest_path = SEARCH_DIR / "shards.json"
    if not manifest_path.exists():
        report.check(False, "search index is missing — run build_site_data.py")
        return

    shards = json.loads(manifest_path.read_text(encoding="utf-8"))
    report.check(len(shards) > 100, f"{len(shards)} shards present")

    cache = {}
    missing = []
    for term in REQUIRED_SEARCH_TERMS:
        prefix = term[:2]
        if prefix not in cache:
            path = SEARCH_DIR / f"{prefix}.json"
            cache[prefix] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if term not in cache[prefix]:
            missing.append(term)
    report.check(
        not missing,
        f"common legal terms are searchable (missing: {missing})")

    # A term that matches nearly everything is as useless as one that is absent.
    sample = cache.get("le", {})
    if "lease" in sample:
        postings = sample["lease"].count(".") + 1
        report.warn(postings > 500, f"'lease' matches {postings:,} decisions — index looks sane")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", default=None, help=f"Site data JSON (default: {SITE_DATA})")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    args = parser.parse_args()

    path = Path(args.data) if args.data else SITE_DATA
    if not path.exists():
        print(f"ERROR: {path} not found. Run scripts/build_site_data.py first.")
        return 2

    print(f"Verifying {path}")
    data = json.loads(path.read_text(encoding="utf-8"))

    report = Report()
    verify_records(data, report)
    verify_search_index(report)

    print("\n" + "=" * 60)
    if report.failures:
        print(f"FAILED — {len(report.failures)} check(s) did not pass:")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    if report.warnings and args.strict:
        print(f"FAILED (strict) — {len(report.warnings)} warning(s)")
        return 1
    print(f"All checks passed ({len(report.warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
