"""Tests for claim_flags.py -- synonym/pattern-based recognition of the
claim-PROCESS realities described in the "Claim Ledger" reference doc
(percentage deductibles, mortgagees, ordinance-or-law coverage, the
cosmetic damage exclusion, appraisal/public-adjuster/supplement
documents, and code-driven line items).

Real-fixture cases confirm this doesn't just work on invented text: the
Travelers fixture's actual printed numbers ($5,300.00 deductible /
$265,000.00 dwelling limit) really do compute to exactly 2%, and the
Williams1 fixture really is written as a self-described appraisal.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scope_parser import parse_text  # noqa: E402
from scope_parser.claim_flags import _infer_deductible_type, compute_claim_flags  # noqa: E402
from scope_parser.codes import CODE_CITATION_RE, mentions_code_citation  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES_DIR, f"{name}.txt")) as f:
        return parse_text(f.read())


SAMPLE = """
Insured: JANE DOE
Claim Number: ABC123
Mortgagee: First National Bank ATIMA
This policy carries Ordinance or Law Coverage of 25% of Coverage A.
A Cosmetic Damage Exclusion (HO-145) applies to this roof.
Public Adjuster License #12345 prepared this supplemental estimate.

DESCRIPTION QUANTITY UNIT PRICE TAX O&P RCV DEPREC. ACV
1. Remove Laminated - comp. shingle rfg. - IRC R905.2.8.2 valley flashing
w/ felt
10.00 SQ 68.57 0.00 0.00 685.70 (0.00) 685.70
Totals: Roof1 685.70 0.00 685.70

Coverage Deductible Policy Limit
Dwelling $5,000.00 $250,000.00
Less Deductible (5,000.00)
"""


class CodeCitationRecognitionTest(unittest.TestCase):
    def test_recognizes_irc_and_ibc_prefixed_citations(self):
        self.assertTrue(mentions_code_citation("Per IRC R905.2.8.2, valleys require..."))
        self.assertTrue(mentions_code_citation("IBC 1504.1.1 wind resistance"))

    def test_recognizes_bare_r9xx_section_number(self):
        self.assertTrue(mentions_code_citation("R903.2.1 kick-out flashing required"))

    def test_does_not_fire_on_ordinary_roofing_language(self):
        self.assertFalse(mentions_code_citation("Remove and replace laminated shingles"))
        self.assertFalse(mentions_code_citation("Roofing labor required."))

    def test_checks_every_argument_given(self):
        self.assertTrue(mentions_code_citation("no code here", "but IRC R905.1.1 here"))

    def test_ignores_none_and_empty(self):
        self.assertFalse(mentions_code_citation(None, "", None))


class InferDeductibleTypeTest(unittest.TestCase):
    def test_two_percent_is_recognized(self):
        kind, pct = _infer_deductible_type(5300.0, 265000.0)
        self.assertEqual(kind, "percentage")
        self.assertEqual(pct, 2.0)

    def test_one_percent_is_recognized(self):
        kind, pct = _infer_deductible_type(3000.0, 300000.0)
        self.assertEqual(kind, "percentage")
        self.assertEqual(pct, 1.0)

    def test_flat_dollar_amount_is_not_mistaken_for_a_percentage(self):
        # $500 against a $300,000 limit is 0.167% -- not a real TX
        # wind/hail percentage point, so this should read as flat.
        kind, pct = _infer_deductible_type(500.0, 300000.0)
        self.assertEqual(kind, "flat")
        self.assertIsNone(pct)

    def test_no_policy_limit_is_unknown_not_guessed(self):
        kind, pct = _infer_deductible_type(6754.0, None)
        self.assertEqual(kind, "unknown")
        self.assertIsNone(pct)

    def test_zero_deductible_is_not_a_percentage(self):
        kind, pct = _infer_deductible_type(0.0, 300000.0)
        self.assertEqual(kind, "unknown")
        self.assertIsNone(pct)


class ComputeClaimFlagsOnSyntheticDocTest(unittest.TestCase):
    """Every synonym-based flag, all in one document, so a regression that
    breaks one detector's regex can't hide behind another one passing."""

    @classmethod
    def setUpClass(cls):
        cls.est = parse_text(SAMPLE)
        cls.flags = cls.est.claim_flags

    def test_percentage_deductible_computed_from_table(self):
        self.assertEqual(self.flags.dwelling_deductible, 5000.0)
        self.assertEqual(self.flags.dwelling_policy_limit, 250000.0)
        self.assertEqual(self.flags.deductible_type, "percentage")
        self.assertEqual(self.flags.deductible_pct, 2.0)

    def test_mortgagee_detected(self):
        self.assertTrue(self.flags.mortgagee_mentioned)

    def test_ordinance_or_law_detected(self):
        self.assertTrue(self.flags.ordinance_or_law_mentioned)

    def test_cosmetic_exclusion_detected(self):
        self.assertTrue(self.flags.cosmetic_exclusion_mentioned)

    def test_public_adjuster_detected(self):
        self.assertTrue(self.flags.is_public_adjuster_document)

    def test_supplement_detected(self):
        self.assertTrue(self.flags.is_supplement_document)

    def test_not_flagged_as_appraisal(self):
        # A public-adjuster/supplement document isn't automatically an
        # appraisal -- distinct signals, shouldn't cross-fire.
        self.assertFalse(self.flags.is_appraisal_document)

    def test_code_related_line_item_flagged_with_rcv_total(self):
        self.assertEqual(self.flags.code_related_item_count, 1)
        self.assertEqual(self.flags.code_related_rcv_total, 685.70)
        self.assertTrue(self.est.line_items[0].code_related)

    def test_every_set_flag_has_an_explanatory_note(self):
        # Seven distinct signals fire in SAMPLE -- confirms `notes` isn't
        # silently dropping any of them (see ClaimFlags docstring).
        self.assertGreaterEqual(len(self.flags.notes), 7)


class ComputeClaimFlagsQuietOnPlainDocTest(unittest.TestCase):
    """An ordinary estimate with none of this going on should come back
    quiet -- these flags must never fire on vocabulary that merely sounds
    similar (e.g. "adjuster" alone, without "public")."""

    def test_no_false_positives_on_plain_estimate_language(self):
        text = (
            "Insured: JOHN SMITH\n"
            "Claim Rep.: Some Adjuster\n"
            "DESCRIPTION QUANTITY UNIT PRICE TAX O&P RCV DEPREC. ACV\n"
            "1. Remove Laminated - comp. shingle rfg.\n"
            "10.00 SQ 68.57 0.00 0.00 685.70 (0.00) 685.70\n"
            "Totals: Roof1 685.70 0.00 685.70\n"
        )
        flags = parse_text(text).claim_flags
        self.assertIsNone(flags.dwelling_deductible)
        self.assertFalse(flags.mortgagee_mentioned)
        self.assertFalse(flags.ordinance_or_law_mentioned)
        self.assertFalse(flags.cosmetic_exclusion_mentioned)
        self.assertFalse(flags.is_appraisal_document)
        self.assertFalse(flags.is_public_adjuster_document)
        self.assertFalse(flags.is_supplement_document)
        self.assertEqual(flags.code_related_item_count, 0)
        self.assertEqual(flags.notes, [])


class ComputeClaimFlagsOnRealFixturesTest(unittest.TestCase):
    """Regression against the checked-in real-carrier fixtures -- ties
    every claim above back to an actual document, not just invented text."""

    def test_travelers_deductible_really_is_two_percent(self):
        # The fixture's own printed numbers: $5,300.00 / $265,000.00.
        flags = load("travelers_erin").claim_flags
        self.assertEqual(flags.dwelling_deductible, 5300.0)
        self.assertEqual(flags.dwelling_policy_limit, 265000.0)
        self.assertEqual(flags.deductible_type, "percentage")
        self.assertEqual(flags.deductible_pct, 2.0)

    def test_allstate_gold_standard_has_no_spurious_flags(self):
        # The strictest fixture in the suite (see test_pipeline.py) --
        # none of these process-level signals are present in it, and none
        # should fire.
        flags = load("allstate_5410").claim_flags
        self.assertFalse(flags.mortgagee_mentioned)
        self.assertFalse(flags.ordinance_or_law_mentioned)
        self.assertFalse(flags.cosmetic_exclusion_mentioned)
        self.assertFalse(flags.is_appraisal_document)
        self.assertFalse(flags.is_public_adjuster_document)
        self.assertFalse(flags.is_supplement_document)
        self.assertEqual(flags.code_related_item_count, 0)

    def test_allstate_deductible_found_but_honestly_unknown_type(self):
        # This fixture is a trimmed excerpt with no cover-sheet coverage
        # table -- only the claim-summary "Less Deductible" fallback line
        # is present, so there's no policy limit to compare it against.
        # Must NOT guess a type it can't support.
        flags = load("allstate_5410").claim_flags
        self.assertEqual(flags.dwelling_deductible, 6754.0)
        self.assertIsNone(flags.dwelling_policy_limit)
        self.assertEqual(flags.deductible_type, "unknown")

    def test_williams1_appraiser_document_recognized_as_an_appraisal(self):
        flags = load("appraiser_williams1").claim_flags
        self.assertTrue(flags.is_appraisal_document)
        self.assertFalse(flags.is_public_adjuster_document)  # not stated in this excerpt

    def test_williams1_floating_code_citation_still_counted(self):
        # "IBC 1511.3 Roof Replacement" is printed as section-level scope
        # language ahead of item 1 -- never attached to one specific
        # LineItem's notes, so code_related_item_count alone would miss
        # it entirely. document_code_citation_count must still catch it.
        flags = load("appraiser_williams1").claim_flags
        self.assertGreaterEqual(flags.document_code_citation_count, 1)


if __name__ == "__main__":
    unittest.main()
