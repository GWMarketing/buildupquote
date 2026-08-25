"""Regression tests for the parsing engine.

Run with:  python3 -m unittest discover -s tests -v
(from the project root -- see README.md)

Two of the three fixtures here are deliberately TRUNCATED excerpts of much
longer real carrier PDFs (kept short so this repo stays readable). That
means their printed section totals will NOT fully reconcile against the
parsed line items -- we removed some of the items that total counts.
That's expected, not a bug, and each test below says so explicitly and
pins down exactly which mismatch warning is allowed, so a *new* or
*different* warning still fails the test and gets caught.

The Allstate fixture (allstate_5410.txt) is the exception: it's the
complete document, so it's the one fixture we hold to a strict
zero-warnings, every-number-reconciles standard. Treat that one as the
gold-standard regression check; if a future parser change breaks it,
something is genuinely wrong.
"""
import os
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scope_parser import parse_text, parse_pdf  # noqa: E402
from scope_parser import noise_filter, schema  # noqa: E402
from scope_parser.tokens import split_fused_tokens  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES_DIR, f"{name}.txt")) as f:
        return parse_text(f.read())


class AllstateFullDocumentTest(unittest.TestCase):
    """The complete Allstate/National Catastrophe Team estimate. Every
    dollar in this document should reconcile -- this is the strictest
    test in the suite on purpose."""

    @classmethod
    def setUpClass(cls):
        cls.est = load("allstate_5410")

    def test_no_warnings_at_all(self):
        self.assertEqual(self.est.warnings, [])

    def test_no_items_need_review(self):
        self.assertEqual(self.est.needs_review_items, [])

    def test_finds_every_line_item(self):
        self.assertEqual(len(self.est.line_items), 27)

    def test_first_item_fields(self):
        li = self.est.line_items[0]
        self.assertEqual(li.number, "1")
        self.assertEqual(li.description, "Remove Laminated - comp. shingle rfg. - w/ felt")
        self.assertEqual(li.quantity, 12.57)
        self.assertEqual(li.unit, "SQ")
        self.assertEqual(li.unit_price, 72.05)
        self.assertEqual(li.rcv, 905.67)
        self.assertEqual(li.section, "Dwelling Roof")

    def test_line_wrap_collision_is_resolved(self):
        """Problem #2 from the project's parsing-problems doc: the words
        'felt' and 'SHINGLE)' print AFTER the numeric row in the raw PDF
        text because of how the column layout wraps -- they must still
        end up as part of the description, not dropped or misplaced."""
        descriptions = [li.description for li in self.est.line_items]
        self.assertIn("Remove Laminated - comp. shingle rfg. - w/ felt", descriptions)
        self.assertIn("Remove Laminated - comp. shingle rfg (per SHINGLE)", descriptions)

    def test_a_genuine_note_is_not_mistaken_for_description(self):
        item12 = next(li for li in self.est.line_items if li.number == "12")
        self.assertIn(
            "Allowance for repair and material cost for damaged chimney base seal due to high winds.",
            item12.notes,
        )
        self.assertNotIn("Allowance", item12.description)

    def test_recoverable_vs_nonrecoverable_depreciation(self):
        # "(300.00)" would be recoverable; the fixture uses "<300.00>",
        # i.e. non-recoverable -- the wrapping punctuation is the only
        # signal for this and it matters for what a homeowner is owed.
        invoice_item = next(li for li in self.est.line_items if li.number == "21")
        self.assertEqual(invoice_item.depreciation, 300.00)
        self.assertFalse(invoice_item.depreciation_recoverable)

    def test_cad_sketch_noise_is_discarded_not_leaked(self):
        joined_descriptions = " ".join(li.description for li in self.est.line_items)
        self.assertNotIn("R10F8", joined_descriptions)
        self.assertNotIn("F2(B)", joined_descriptions)
        self.assertTrue(any("R10F8(A)" in d or "F2(B)" in d for d in self.est.discarded_lines))

    def test_measurement_blocks_survive_noise_stripping(self):
        # Problem #1: don't lose the real measurements while stripping the
        # sketch clutter they're printed next to.
        labels = {(m.label, m.unit): m.value for m in self.est.measurements}
        self.assertEqual(labels[("Surface Area", "")], 3397.66)
        self.assertEqual(labels[("Number of Squares", "")], 33.98)
        self.assertEqual(labels[("Total Ridge Length", "")], 108.26)

    def test_claim_metadata(self):
        f = self.est.metadata.fields
        self.assertEqual(f["claim_number"], "0761262757")
        self.assertEqual(f["policy_number"], "000416888619")
        self.assertEqual(f["insured_name"], "GRAHAM WILLIAMS")
        self.assertEqual(f["type_of_loss"], "WINDSTORM AND HAIL")
        self.assertIn("TERRAGLEN", f["property_address"])

    def test_customer_facing_fields_excludes_acv_and_depreciation(self):
        from scope_parser.models import CUSTOMER_FACING_FIELDS
        self.assertNotIn("acv", CUSTOMER_FACING_FIELDS)
        self.assertNotIn("depreciation", CUSTOMER_FACING_FIELDS)
        self.assertNotIn("age", CUSTOMER_FACING_FIELDS)


class AppraiserExcerptTest(unittest.TestCase):
    """A truncated excerpt of the "Property Insurance Experts" counter-
    estimate -- a different column layout (explicit PRICE/TAX/O&P columns,
    no AGE/LIFE or COND. at all) for the same underlying claim."""

    @classmethod
    def setUpClass(cls):
        cls.est = load("appraiser_williams1")

    def test_no_items_need_review(self):
        self.assertEqual(self.est.needs_review_items, [])

    def test_finds_expected_item_count(self):
        self.assertEqual(len(self.est.line_items), 27)

    def test_the_only_warning_is_the_known_fixture_truncation(self):
        # This fixture deliberately omits some real items from the source
        # document's Roof1 section, so its running total legitimately
        # won't match the printed one. If this ever fails with a
        # *different* message, something new broke.
        self.assertEqual(len(self.est.warnings), 1)
        self.assertIn("Roof1", self.est.warnings[0])

    def test_price_tax_op_column_layout_is_handled(self):
        li = next(li for li in self.est.line_items if li.number == "1")
        self.assertEqual(li.unit_price, 68.57)
        self.assertEqual(li.tax, 0.00)
        self.assertEqual(li.overhead_profit, 720.20)
        self.assertEqual(li.rcv, 3120.84)
        # This layout has no AGE/LIFE or COND. columns at all.
        self.assertIsNone(li.age)
        self.assertIsNone(li.condition)

    def test_paired_remove_and_install_items_split_correctly(self):
        # "10a."/"10b." style item numbers (remove + install as a pair)
        numbers = [li.number for li in self.est.line_items]
        self.assertIn("10a", numbers)
        self.assertIn("10b", numbers)

    def test_recoverable_depreciation_on_this_layout(self):
        li = next(li for li in self.est.line_items if li.number == "12b")
        self.assertEqual(li.depreciation, 541.05)
        self.assertTrue(li.depreciation_recoverable)
        self.assertEqual(li.acv, 966.16)


class TravelersExcerptTest(unittest.TestCase):
    """A truncated excerpt of a Travelers "YOUR ESTIMATE" PDF -- a third
    column layout, and one where O&P appears in some subsections but not
    others within the very same document (see problem #5 in the parsing
    notes)."""

    @classmethod
    def setUpClass(cls):
        cls.est = load("travelers_erin")

    def test_no_items_need_review(self):
        self.assertEqual(self.est.needs_review_items, [])

    def test_the_only_warning_is_the_known_fixture_truncation(self):
        self.assertEqual(len(self.est.warnings), 1)
        self.assertIn("Masonry", self.est.warnings[0])

    def test_bracket_depreciation_means_non_recoverable(self):
        li = next(li for li in self.est.line_items if li.number == "7")
        self.assertEqual(li.depreciation, 0.00)
        self.assertFalse(li.depreciation_recoverable)

    def test_modifier_flag_after_dep_percent_does_not_break_parsing(self):
        # "90% [M]" -- the [M] means the max allowable depreciation capped
        # this item; it should be consumed without throwing the row off.
        li = next(li for li in self.est.line_items if li.number == "12")
        self.assertEqual(li.depreciation_pct, "90%")
        self.assertEqual(li.depreciation, 162.38)
        self.assertEqual(li.acv, 44.38)

    def test_claim_metadata(self):
        f = self.est.metadata.fields
        self.assertEqual(f["claim_number"], "I8C1849001H")
        self.assertIn("BELMONT", f["property_address"])


class NoiseFilterUnitTest(unittest.TestCase):
    def test_sketch_labels_are_noise(self):
        self.assertTrue(noise_filter.is_noise_line("R4 (2) F1 F2(B)"))
        self.assertTrue(noise_filter.is_noise_line("R10F8(A)"))
        self.assertTrue(noise_filter.is_noise_line("29' 7\" 58' 6\""))
        self.assertTrue(noise_filter.is_noise_line("R10"))

    def test_real_content_is_not_noise(self):
        self.assertFalse(noise_filter.is_noise_line("1. Remove Laminated - comp. shingle rfg."))
        self.assertFalse(noise_filter.is_noise_line("3397.66 Surface Area"))
        self.assertFalse(noise_filter.is_noise_line("Bathroom Height: 8'"))


class SplitFusedTokensUnitTest(unittest.TestCase):
    def test_splits_quantity_glued_to_unit(self):
        self.assertEqual(split_fused_tokens(["35.01SQ"]), ["35.01", "SQ"])
        self.assertEqual(split_fused_tokens(["40.26SQ"]), ["40.26", "SQ"])
        self.assertEqual(split_fused_tokens(["1.00EA"]), ["1.00", "EA"])

    def test_leaves_normal_tokens_alone(self):
        self.assertEqual(split_fused_tokens(["35.01", "SQ", "68.57"]), ["35.01", "SQ", "68.57"])
        self.assertEqual(split_fused_tokens(["Roofing", "felt"]), ["Roofing", "felt"])

    def test_does_not_touch_a_word_that_merely_ends_in_unit_like_letters(self):
        # Must not fire on ordinary words that happen to end the same way a
        # unit abbreviation is spelled -- only a *numeric* prefix counts.
        self.assertEqual(split_fused_tokens(["AREA"]), ["AREA"])


class RealWorldPdfQuirksTest(unittest.TestCase):
    """Two real bugs found via an actual carrier PDF (Property Insurance
    Experts / "Williams1") that the hand-transcribed fixture below didn't
    catch, because it was typed with normal spacing -- the real PDF's own
    text extraction doesn't put a space between certain tokens."""

    def test_quantity_fused_to_unit_with_no_space_still_parses(self):
        # pdfplumber's text extraction on this carrier's PDF drops the space
        # between a quantity and its unit ("35.01SQ" instead of "35.01 SQ")
        # -- a font/kerning quirk of that specific document, not something a
        # different extraction choice would necessarily avoid.
        text = (
            "DESCRIPTION QUANTITY UNIT PRICE TAX O&P RCV DEPREC. ACV\n"
            "1. Remove Laminated - comp. shingle rfg. - 35.01SQ 68.57 0.00 720.20 3,120.84 (0.00) 3,120.84\n"
        )
        est = parse_text(text)
        self.assertEqual(len(est.line_items), 1)
        item = est.line_items[0]
        self.assertFalse(item.needs_review)
        self.assertEqual(item.quantity, 35.01)
        self.assertEqual(item.unit, "SQ")
        self.assertEqual(item.rcv, 3120.84)

    def test_page_number_after_a_finished_item_does_not_pollute_description(self):
        # A bare page number ("3") sitting right after an item's data row,
        # at a page break, used to be swallowed by the continuation-line
        # heuristic (it's short and has no terminal punctuation) BEFORE the
        # page-furniture check ever ran -- appending it straight into the
        # item's own description ("Roofing felt - 15 lb. 3"). Page furniture
        # must be recognized and dropped before that heuristic applies.
        text = (
            "DESCRIPTION QUANTITY UNIT PRICE TAX O&P RCV DEPREC. ACV\n"
            "5. Roofing felt - 15 lb. 40.26SQ 34.42 24.51 423.08 1,833.34 (0.00) 1,833.34\n"
            "\n"
            "3\n"
            "6. Some other item 1.00 EA 10.00 0.00 1.00 10.00 (0.00) 10.00\n"
        )
        est = parse_text(text)
        five = [li for li in est.line_items if li.number == "5"][0]
        self.assertEqual(five.description, "Roofing felt - 15 lb.")


class SchemaUnitTest(unittest.TestCase):
    def test_allstate_style_header_infers_missing_price_column(self):
        cols = schema.parse_header("DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND. DEP % DEPREC. ACV")
        self.assertEqual(
            cols,
            ["unit_price", "rcv", "age_life", "condition", "depreciation_pct", "depreciation", "acv"],
        )

    def test_appraiser_style_header_keeps_explicit_price_column(self):
        cols = schema.parse_header("DESCRIPTION QUANTITY UNIT PRICE TAX O&P RCV DEPREC. ACV")
        self.assertEqual(cols, ["unit_price", "tax", "overhead_profit", "rcv", "depreciation", "acv"])

    def test_non_header_line_is_rejected(self):
        self.assertIsNone(schema.parse_header("1. Remove Laminated - comp. shingle rfg."))


class RealPdfExtractionTest(unittest.TestCase):
    """The rest of this suite tests the parsing logic against plain-text
    fixtures (see the module docstring for why: it lets the row/column
    logic be tested without a real PDF on disk). This one test exercises
    the actual PDF -> text step (extract.py, via pdfplumber) end to end
    against a small synthetic PDF, so a break in that wiring -- as
    opposed to the parsing logic itself -- still gets caught."""

    def test_parse_pdf_end_to_end(self):
        pdf_path = os.path.join(FIXTURES_DIR, "synthetic_sample.pdf")
        est = parse_pdf(pdf_path)
        self.assertEqual(est.warnings, [])
        self.assertEqual(len(est.line_items), 2)
        self.assertEqual(est.metadata.fields["claim_number"], "TEST12345")
        first = est.line_items[0]
        self.assertEqual(first.description, "R&R Drywall - 1/2 inch")
        self.assertEqual(first.rcv, 250.00)


class BoilerplateGuidePageTest(unittest.TestCase):
    """Some carrier PDFs (Travelers, confirmed via a real customer PDF)
    include a generic "how to read your estimate" insert page with a
    made-up example claim, laid out as an annotated diagram that badly
    scrambles pdfplumber's text extraction -- and which reuses item
    numbers 1, 2, 3... just like a real claim, so its fake dollar figures
    were bleeding into real totals-consistency checks. That insert is
    reliably fingerprinted by the literal string "GUIDE_EXAMPLE" (the
    carrier's own internal name for the fake example), and extract.py now
    drops any whole page carrying that marker before line-item parsing
    ever sees it."""

    def test_boilerplate_guide_page_is_excluded_entirely(self):
        pdf_path = os.path.join(FIXTURES_DIR, "boilerplate_guide_page.pdf")
        est = parse_pdf(pdf_path)
        self.assertEqual(len(est.line_items), 1)
        self.assertIn("Remove Laminated", est.line_items[0].description)
        self.assertNotIn("Fake example", est.line_items[0].description)
        self.assertEqual(est.metadata.fields.get("claim_number"), "REAL123456")


class SafetyValveTest(unittest.TestCase):
    """The anti-guessing behavior the project's legal/parsing notes call
    for: an unrecognized row shape must be flagged, never silently
    mis-mapped into the wrong dollar figure."""

    def test_malformed_row_is_flagged_needs_review_not_guessed(self):
        text = (
            "DESCRIPTION QUANTITY UNIT RCV AGE/LIFE COND. DEP % DEPREC. ACV\n"
            "1. Some new carrier's totally different row shape 5.00 SQ 12.34 nonsense-token\n"
        )
        est = parse_text(text)
        self.assertEqual(len(est.line_items), 1)
        self.assertTrue(est.line_items[0].needs_review)
        self.assertTrue(est.needs_review_items)


if __name__ == "__main__":
    unittest.main()
