#!/usr/bin/env python3
"""Unit tests for the extraction regexes.

Deliberately dependency-free so it runs anywhere with `python3 scripts/test_extraction.py`.

Most of these are regression tests. Each one corresponds to a defect that
reached the published site: a group(0)/group(1) slip that prefixed 598 outcomes
with "The tribunal The tribunal", a missing negative lookbehind that invented
2,882 citations of a "Leasehold Reform Act 2002" that does not exist, a money
pattern that read £10.5 as £10, and a date parser that emitted three
incompatible formats including "31 November".
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_structured_fields as E  # noqa: E402


class TestDecisionOutcome(unittest.TestCase):
    def test_does_not_duplicate_the_tribunal_prefix(self):
        text = "The tribunal determines that the service charge is payable.  Next."
        outcome = E.extract_decision_outcome(text)
        self.assertEqual(outcome, "The tribunal determines that the service charge is payable")
        self.assertNotIn("The tribunal The tribunal", outcome)

    def test_keeps_the_verb(self):
        text = "The tribunal orders that the landlord repay 4,500 pounds to the tenant.  Next."
        self.assertIn("orders", E.extract_decision_outcome(text))

    def test_rejects_layout_rules(self):
        self.assertIsNone(E._truncate_outcome("_________________________________"))

    def test_rejects_bare_headings(self):
        self.assertIsNone(E._truncate_outcome("The Decision of the Tribunal"))

    def test_keeps_a_real_finding(self):
        finding = "The application is dismissed because the notice was invalid"
        self.assertEqual(E._truncate_outcome(finding), finding)


class TestLegalActs(unittest.TestCase):
    def setUp(self):
        E.rejected_act_years.clear()

    def test_commonhold_act_does_not_invent_a_leasehold_reform_act(self):
        acts = E.extract_legal_acts("under the Commonhold and Leasehold Reform Act 2002")
        self.assertIn("Commonhold and Leasehold Reform Act 2002", acts)
        self.assertNotIn("Leasehold Reform Act 2002", acts)

    def test_local_government_act_does_not_invent_a_housing_act(self):
        acts = E.extract_legal_acts("Schedule 10 to the Local Government and Housing Act 1989")
        self.assertIn("Local Government and Housing Act 1989", acts)
        self.assertNotIn("Housing Act 1989", acts)

    def test_real_leasehold_reform_act_still_matches(self):
        self.assertIn("Leasehold Reform Act 1967",
                      E.extract_legal_acts("section 9 of the Leasehold Reform Act 1967"))

    def test_ocr_corrupted_years_are_dropped(self):
        for corrupt in ("Landlord and Tenant Act 1085", "Housing Act 2994",
                        "Landlord and Tenant Act 1298"):
            self.assertEqual(E.extract_legal_acts(corrupt), [], corrupt)

    def test_dropped_years_are_reported(self):
        E.extract_legal_acts("Housing Act 2994")
        self.assertEqual(E.rejected_act_years["Housing Act 2994"], 1)

    def test_multiple_acts_in_one_document(self):
        acts = E.extract_legal_acts(
            "the Landlord and Tenant Act 1985 and the Housing Act 2004 and the Rent Act 1977")
        self.assertEqual(
            set(acts),
            {"Landlord and Tenant Act 1985", "Housing Act 2004", "Rent Act 1977"})


class TestFinancialAmounts(unittest.TestCase):
    def test_single_decimal_place_is_not_truncated(self):
        self.assertEqual(E.extract_financial_amounts("£10.5"), [10.5])

    def test_space_after_the_sign(self):
        self.assertEqual(E.extract_financial_amounts("£ 500"), [500.0])

    def test_thousands_separators(self):
        self.assertEqual(E.extract_financial_amounts("£1,234.56"), [1234.56])

    def test_deduplicates_preserving_order(self):
        self.assertEqual(E.extract_financial_amounts("£20 then £10 then £20"), [20.0, 10.0])


class TestDates(unittest.TestCase):
    def test_hearing_date_is_iso(self):
        self.assertEqual(E.extract_hearing_date("Date of Hearing :  12th March 2021\n"),
                         "2021-03-12")

    def test_numeric_hearing_date_is_iso_and_day_first(self):
        self.assertEqual(E.extract_hearing_date("Hearing Date:  10/5/19\n"), "2019-05-10")

    def test_ordinal_without_a_space(self):
        self.assertEqual(E._normalise_date("12thMarch 2021"), "2021-03-12")

    def test_uppercase_ordinal(self):
        self.assertEqual(E._normalise_date("12TH MARCH 2021"), "2021-03-12")

    def test_impossible_date_is_rejected(self):
        self.assertIsNone(E._normalise_date("31 November 2023"))
        self.assertIsNone(E._to_iso(2024, 2, 30))

    def test_valid_leap_day(self):
        self.assertEqual(E._to_iso(2024, 2, 29), "2024-02-29")


class TestDecisionDateRepair(unittest.TestCase):
    def test_typo_year_is_corrected_from_published_at(self):
        decisions = [{"decision_date": "2925-03-13", "published_at": "2025-03-20T10:00:00Z"}]
        self.assertEqual(E.fix_decision_dates(decisions), 1)
        self.assertEqual(decisions[0]["decision_date"], "2025-03-13")

    def test_correction_producing_an_impossible_date_is_skipped(self):
        # 29 February is real in 2924 (divisible by 4) but not in 2023.
        decisions = [{"decision_date": "2924-02-29", "published_at": "2023-03-05T10:00:00Z"}]
        self.assertEqual(E.fix_decision_dates(decisions), 0)
        self.assertEqual(decisions[0]["decision_date"], "2924-02-29")

    def test_a_sane_date_is_left_alone(self):
        decisions = [{"decision_date": "2024-06-01", "published_at": "2024-06-10T10:00:00Z"}]
        self.assertEqual(E.fix_decision_dates(decisions), 0)
        self.assertEqual(decisions[0]["decision_date"], "2024-06-01")


class TestRegionCodes(unittest.TestCase):
    def test_alternation_is_deterministic(self):
        # A set's iteration order varies between interpreter runs, which made
        # the compiled pattern — and the regex cache key — unstable.
        self.assertEqual(E.REGION_CODE_ALTERNATION,
                         '|'.join(sorted(E.VALID_REGION_CODES, key=lambda c: (-len(c), c))))

    def test_longer_codes_sort_first(self):
        codes = E.REGION_CODE_ALTERNATION.split('|')
        self.assertGreaterEqual(len(codes[0]), len(codes[-1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
