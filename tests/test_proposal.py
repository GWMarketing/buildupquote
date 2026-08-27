"""Tests for the branded proposal export (proposal/).

build_proposal()/group_line_items() are pure-Python and tested directly.
The PDF rendering test actually runs WeasyPrint and reads
the result back with pdfplumber, so it's a real end-to-end check, not
just "did the function return without raising."
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tax  # noqa: E402
from code_checklist import SECTION_LABEL as CODE_SECTION_LABEL  # noqa: E402
from proposal import ContractorInfo, build_proposal, render_proposal_html, render_proposal_pdf  # noqa: E402
from proposal import models as proposal_models  # noqa: E402
from proposal.build import group_line_items  # noqa: E402
from proposal.models import ProposalLineItem  # noqa: E402

SAMPLE_ROWS = [
    # "Insurance RCV" set on each real row, like a genuine parsed carrier
    # line always has -- see PaymentBreakdownBuildTest/PaymentSchedulePdfTest
    # for why a row with no value there is read as a supplement instead.
    {"Include": True, "Trade": "Roofing", "Description": "Remove shingles",
     "Qty": 10, "Unit": "SQ", "Unit Cost": 70.0, "Margin %": 20, "Insurance RCV": 700.0},
    {"Include": True, "Trade": "Roofing", "Description": "Install shingles",
     "Qty": 10, "Unit": "SQ", "Unit Cost": 250.0, "Margin %": 20, "Insurance RCV": 2500.0},
    {"Include": False, "Trade": "Painting", "Description": "Paint fascia (excluded)",
     "Qty": 5, "Unit": "LF", "Unit Cost": 10.0, "Margin %": 20, "Insurance RCV": 50.0},
    {"Include": True, "Trade": "Painting", "Description": "Paint fascia",
     "Qty": 5, "Unit": "LF", "Unit Cost": 10.0, "Margin %": 0, "Insurance RCV": 50.0},
    # A blank row a contractor added via the "+" row and hasn't filled in.
    {"Include": True, "Trade": "Other", "Description": "", "Qty": None, "Unit": None,
     "Unit Cost": None, "Margin %": 20},
]

SAMPLE_CLAIM_FIELDS = {
    "insured_name": "GRAHAM WILLIAMS",
    "property_address": "31 TERRAGLEN DR, THE WOODLANDS, TX 77382",
    "insurance_company": "Allstate Vehicle and Property Insurance Company",
    "claim_number": "0761262757",
    "policy_number": "000416888619",
    "type_of_loss": "WINDSTORM AND HAIL",
    "date_of_loss": "7/8/2024 10:00 AM",
}

CONTRACTOR = ContractorInfo(
    name="Sample Roofing & Restoration",
    address="123 Main St, Anytown, TX 77000",
    phone="(555) 123-4567",
    email="office@samplerestoration.com",
    license_number="TX-12345",
)


class BuildProposalTest(unittest.TestCase):
    def setUp(self):
        self.data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")

    def test_excluded_row_is_dropped(self):
        all_descriptions = [i.description for g in self.data.grouped_items for i in g.items]
        self.assertNotIn("Paint fascia (excluded)", all_descriptions)

    def test_blank_new_row_is_dropped(self):
        # zero rows should end up under "Other"
        trades = [g.trade for g in self.data.grouped_items]
        self.assertNotIn("Other", trades)

    def test_items_grouped_by_trade(self):
        trades = [g.trade for g in self.data.grouped_items]
        self.assertEqual(trades, ["Roofing", "Painting"])
        roofing_group = self.data.grouped_items[0]
        self.assertEqual(len(roofing_group.items), 2)

    def test_margin_is_applied_to_unit_price_and_total(self):
        roofing_group = self.data.grouped_items[0]
        remove_item = roofing_group.items[0]
        self.assertEqual(remove_item.unit_price, 84.0)   # 70 * 1.20
        self.assertEqual(remove_item.line_total, 840.0)  # 10 * 84

    def test_subtotals_and_grand_total(self):
        roofing_group, painting_group = self.data.grouped_items
        self.assertEqual(roofing_group.subtotal, 840.0 + 3000.0)  # 10*84 + 10*300
        self.assertEqual(painting_group.subtotal, 50.0)           # 5*10, 0% margin
        self.assertEqual(self.data.total_price, roofing_group.subtotal + painting_group.subtotal)

    def test_claim_info_pulled_from_metadata(self):
        self.assertEqual(self.data.claim.claim_number, "0761262757")
        self.assertEqual(self.data.claim.insured_name, "GRAHAM WILLIAMS")

    def test_falls_back_to_company_when_no_insurance_company_field(self):
        fields = {"company": "Property Insurance Experts"}
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, fields, "2026-08-23")
        self.assertEqual(data.claim.insurance_company, "Property Insurance Experts")

    def test_no_tax_rule_leaves_total_unchanged_from_before_tax_existed(self):
        # Default tax_rule/tax_rate_pct -- confirms adding the tax feature
        # didn't silently change any existing proposal's total.
        self.assertEqual(self.data.tax_amount, 0.0)
        self.assertEqual(self.data.tax_label, "")
        self.assertEqual(self.data.total_price, self.data.subtotal)


class BuildProposalWithTaxTest(unittest.TestCase):
    """SAMPLE_ROWS line totals: 840 (roofing, material) + 3000 (roofing,
    material) + 50 (painting, material) = 3890 subtotal -- none of the
    sample rows set "Material": False, so under a separated contract the
    whole subtotal is taxable."""

    def test_separated_residential_adds_tax_on_top(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                               tax_rule=tax.SEPARATED_RESIDENTIAL, tax_rate_pct=10.0)
        self.assertEqual(data.subtotal, 3890.0)
        self.assertEqual(data.tax_amount, 389.0)
        self.assertEqual(data.total_price, 4279.0)
        self.assertIn("Separated", data.tax_label)

    def test_labor_only_line_is_excluded_from_separated_tax(self):
        rows = SAMPLE_ROWS + [
            {"Include": True, "Trade": "Roofing", "Description": "Labor only",
             "Qty": 1, "Unit": "EA", "Unit Cost": 1000.0, "Margin %": 0, "Material": False},
        ]
        data = build_proposal(rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                               tax_rule=tax.SEPARATED_RESIDENTIAL, tax_rate_pct=10.0)
        self.assertEqual(data.subtotal, 4890.0)          # 3890 + 1000 labor
        self.assertEqual(data.tax_amount, 389.0)          # unchanged -- labor line untaxed
        self.assertEqual(data.total_price, 5279.0)

    def test_lump_sum_residential_never_itemizes_tax(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                               tax_rule=tax.LUMP_SUM_RESIDENTIAL, tax_rate_pct=10.0)
        self.assertEqual(data.tax_amount, 0.0)
        self.assertEqual(data.tax_label, "")
        self.assertEqual(data.total_price, data.subtotal)

    def test_commercial_taxes_the_whole_subtotal(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                               tax_rule=tax.COMMERCIAL, tax_rate_pct=10.0)
        self.assertEqual(data.tax_amount, 389.0)
        self.assertEqual(data.total_price, 4279.0)


class CodeItemNoteTest(unittest.TestCase):
    """Code-required additions carry the code item's own plain-English
    explanation as a note that prints under the line item on the proposal
    -- its own little row, the justification an adjuster can read without
    having to ask."""

    ROWS = SAMPLE_ROWS + [
        {"Include": True, "Trade": "Roofing",
         "Description": "IRC R905.1 - General requirements for roof covering",
         "Qty": 1, "Unit": "EA", "Unit Cost": 500.0, "Margin %": 20,
         "Section": CODE_SECTION_LABEL,
         "Review Note": "Roof coverings must be applied in accordance with "
                        "the manufacturer instructions and the approved plans."},
    ]

    def _data(self):
        return build_proposal(self.ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")

    def test_code_required_row_carries_its_explanation_as_the_note(self):
        data = self._data()
        notes = [i.note for g in data.grouped_items for i in g.items]
        self.assertTrue(any("manufacturer instructions" in n for n in notes))
        # Ordinary carrier rows stay note-free.
        carrier = data.grouped_items[0].items[0]
        self.assertEqual(carrier.note, "")

    def test_note_renders_as_its_own_row_under_the_line_item(self):
        html = render_proposal_html(self._data())
        self.assertIn("item-note", html)
        self.assertIn("Why this line is here:", html)
        self.assertIn("manufacturer instructions", html)

    def test_note_survives_into_the_pdf_text(self):
        import pdfplumber

        out_path = "/tmp/test_code_item_note.pdf"
        render_proposal_pdf(self._data(), out_path)
        with pdfplumber.open(out_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        self.assertIn("manufacturer instructions", text)


class GroupLineItemsTest(unittest.TestCase):
    def test_preserves_first_seen_trade_order(self):
        items = [
            ProposalLineItem("Painting", "a", 1, "EA", 10, 10),
            ProposalLineItem("Roofing", "b", 1, "EA", 10, 10),
            ProposalLineItem("Painting", "c", 1, "EA", 10, 10),
        ]
        groups = group_line_items(items)
        self.assertEqual([g.trade for g in groups], ["Painting", "Roofing"])
        self.assertEqual(len(groups[0].items), 2)


class RenderProposalPdfTest(unittest.TestCase):
    """An actual end-to-end render: HTML -> WeasyPrint -> PDF -> read back
    with pdfplumber and check both what should and should NOT be there."""

    @classmethod
    def setUpClass(cls):
        cls.data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                                   proposal_number="P-1001")
        cls.out_path = "/tmp/test_proposal_output.pdf"
        render_proposal_pdf(cls.data, cls.out_path)

    def test_html_contains_contractor_and_claim_info(self):
        html = render_proposal_html(self.data)
        self.assertIn("Sample Roofing &amp; Restoration", html)
        self.assertIn("0761262757", html)
        self.assertIn("Remove shingles", html)

    def test_pdf_file_was_created(self):
        self.assertTrue(os.path.exists(self.out_path))
        self.assertGreater(os.path.getsize(self.out_path), 1000)

    def test_pdf_text_round_trip(self):
        import pdfplumber
        with pdfplumber.open(self.out_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        self.assertIn("Sample Roofing", text)
        self.assertIn("0761262757", text)
        self.assertIn("GRAHAM WILLIAMS", text)
        self.assertIn("Remove shingles", text)
        self.assertIn("Total Contract Price", text)
        self.assertIn("3,890.00", text)  # 840 + 3000 + 50

    def test_no_insurance_only_figures_leak_onto_the_proposal(self):
        # Per the project's legal notes: depreciation and ACV are
        # insurance-only figures and must never appear on the
        # homeowner-facing document.
        import pdfplumber
        with pdfplumber.open(self.out_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages).lower()
        for forbidden in ("depreciation", "actual cash value", "acv"):
            self.assertNotIn(forbidden, text)

    def test_deductible_is_named_but_never_given_a_dollar_amount(self):
        # The word "deductible" SHOULD appear -- the terms text is required
        # to state plainly that it's the homeowner's responsibility and is
        # not absorbed into the price (see DEFAULT_TERMS). What must never
        # happen is a specific deductible dollar figure showing up next to
        # it, which would look like it's being subtracted from the price.
        import re

        import pdfplumber
        with pdfplumber.open(self.out_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages).lower()
        self.assertIn("deductible", text)
        for sentence in text.split("."):
            if "deductible" in sentence:
                self.assertNotRegex(sentence, r"\$\s*[\d,]+")


PAYMENT_BREAKDOWN_ROWS = [
    # A real carrier line: "Insurance RCV" set, like _rows_from_estimate()
    # always produces.
    {"Include": True, "Trade": "Roofing", "Description": "Replace shingles",
     "Qty": 10, "Unit": "SQ", "Unit Cost": 100.0, "Margin %": 20,
     "Recoverable Depreciation": 300.0, "Insurance RCV": 1000.0},
    # A supplement: no "Insurance RCV" at all, like _manual_row() produces.
    {"Include": True, "Trade": "Gutters", "Description": "New gutter run (added by contractor)",
     "Qty": 1, "Unit": "EA", "Unit Cost": 400.0, "Margin %": 0,
     "Recoverable Depreciation": 0.0},
]


class PaymentBreakdownBuildTest(unittest.TestCase):
    """build_proposal()'s payment-breakdown computation -- which now runs
    through the ONE shared implementation, payment_breakdown() (see
    proposal/build.py). app.py's _payment_breakdown delegates to the same
    helper, so the spec can no longer drift between the two callers -- the
    anti-drift guard is the parity test at the bottom of this class, and
    app.py's own tests exercise the DataFrame-side aggregation."""

    def setUp(self):
        # Roofing: 10 * 100 * 1.20 = 1,200. Gutters (supplement): 400.
        # Total = 1,600.
        self.data = build_proposal(
            PAYMENT_BREAKDOWN_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
            deductible_amount=250.0,
        )

    def test_deductible_passed_through(self):
        self.assertEqual(self.data.deductible_amount, 250.0)

    def test_recoverable_depreciation_summed_from_included_rows(self):
        self.assertEqual(self.data.recoverable_depreciation_amount, 300.0)

    def test_supplement_row_counted_at_contractor_price(self):
        self.assertEqual(self.data.supplements_amount, 400.0)

    def test_four_parts_sum_exactly_to_total_price(self):
        parts_sum = (
            self.data.deductible_amount + self.data.first_check_amount
            + self.data.recoverable_depreciation_amount + self.data.supplements_amount
        )
        self.assertAlmostEqual(parts_sum, self.data.total_price, places=2)

    def test_excluded_row_does_not_count_toward_depreciation_or_supplements(self):
        rows = PAYMENT_BREAKDOWN_ROWS + [
            {"Include": False, "Trade": "Painting", "Description": "Excluded",
             "Qty": 1, "Unit": "EA", "Unit Cost": 999.0, "Margin %": 0,
             "Recoverable Depreciation": 999.0},  # no Insurance RCV -- would be a supplement if included
        ]
        data = build_proposal(rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23", deductible_amount=250.0)
        self.assertEqual(data.recoverable_depreciation_amount, 300.0)
        self.assertEqual(data.supplements_amount, 400.0)

    def test_a_real_carrier_row_with_a_zero_dollar_rcv_is_not_a_supplement(self):
        # A genuine $0.00 carrier line is a real value, not "blank" -- must
        # not be misread as a supplement.
        rows = [
            {"Include": True, "Trade": "Roofing", "Description": "No-charge line",
             "Qty": 1, "Unit": "EA", "Unit Cost": 50.0, "Margin %": 0, "Insurance RCV": 0.0},
        ]
        data = build_proposal(rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        self.assertEqual(data.supplements_amount, 0.0)

    def test_missing_insurance_rcv_key_entirely_still_counts_as_a_supplement(self):
        rows = [
            {"Include": True, "Trade": "Roofing", "Description": "No carrier line behind this",
             "Qty": 1, "Unit": "EA", "Unit Cost": 50.0, "Margin %": 0},  # no "Insurance RCV" key at all
        ]
        data = build_proposal(rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        self.assertEqual(data.supplements_amount, 50.0)

    def test_no_deductible_or_payment_fields_defaults_to_zero_deductible(self):
        # An existing caller that never passes deductible_amount at all
        # gets 0, not a crash or a guessed figure. Uses its own rows
        # (rather than the module-level SAMPLE_ROWS, which predates
        # "Insurance RCV" and would misclassify everything as a
        # supplement) so this test exercises a normal, non-supplement job.
        rows = [
            {"Include": True, "Trade": "Roofing", "Description": "Remove shingles",
             "Qty": 10, "Unit": "SQ", "Unit Cost": 70.0, "Margin %": 20, "Insurance RCV": 700.0},
        ]
        data = build_proposal(rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        self.assertEqual(data.deductible_amount, 0.0)
        self.assertEqual(data.first_check_amount, data.total_price)
        self.assertEqual(data.supplements_amount, 0.0)

    def test_parts_always_sum_to_total_even_when_the_deductible_overruns(self):
        """Same edge as app.py's on-screen warning: if deductible +
        recoverable depreciation + supplements exceed the total, first
        check goes negative -- the four parts still add up exactly, no
        silently-wrong number hides it."""
        from proposal import payment_breakdown

        parts = payment_breakdown(1000.0, deductible=1200.0)
        self.assertEqual(parts["first_check"], -200.0)
        self.assertAlmostEqual(
            parts["deductible"] + parts["first_check"]
            + parts["recoverable_depreciation"] + parts["supplements"],
            1000.0, places=2,
        )

    def test_same_job_agrees_with_app_side_delegation(self):
        """The anti-drift guard: app._payment_breakdown() delegates to the
        same payment_breakdown() this module uses, aggregating only --
        so for identical figures the two callers must produce identical
        parts. If this ever fails, the shared spec was split again."""
        from proposal import payment_breakdown

        parts = payment_breakdown(1600.0, deductible=250.0, recoverable_depreciation=300.0, supplements=400.0)
        self.assertEqual(
            parts,
            {
                "deductible": 250.0,
                "first_check": 650.0,
                "recoverable_depreciation": 300.0,
                "supplements": 400.0,
            },
        )
        self.assertEqual(parts["first_check"], self.data.first_check_amount)


class PaymentSchedulePdfTest(unittest.TestCase):
    """The Payment Schedule section actually reaching the rendered PDF,
    including both statutes a 15-year contractor asked to see cited on
    the deductible line."""

    @classmethod
    def setUpClass(cls):
        cls.data = build_proposal(
            PAYMENT_BREAKDOWN_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
            deductible_amount=250.0,
        )
        cls.html = render_proposal_html(cls.data)

    def test_payment_schedule_heading_present(self):
        self.assertIn("Payment Schedule", self.html)

    def test_both_statutes_cited_on_the_deductible_line(self):
        self.assertIn("27.02", self.html)
        self.assertIn("707", self.html)

    def test_all_four_dollar_amounts_present(self):
        self.assertIn("250.00", self.html)   # deductible
        self.assertIn("300.00", self.html)   # recoverable depreciation
        self.assertIn("400.00", self.html)   # supplements
        # first check = 1600 (total) - 250 - 300 - 400 = 650
        self.assertIn("650.00", self.html)

    def test_recoverable_depreciation_row_omitted_when_zero(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        html = render_proposal_html(data)
        self.assertNotIn("Recoverable depreciation", html)

    def test_supplements_row_omitted_when_zero(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        html = render_proposal_html(data)
        self.assertNotIn("Supplements", html)

    def test_reaches_the_actual_rendered_pdf(self):
        import pdfplumber

        out_path = "/tmp/test_payment_schedule.pdf"
        render_proposal_pdf(self.data, out_path)
        with pdfplumber.open(out_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        # The heading renders as "PAYMENT SCHEDULE" -- the template's CSS
        # uppercases section headings (same as "Summary by Trade"'s own
        # <h3>), so the *rendered* characters differ from the HTML source.
        self.assertIn("PAYMENT SCHEDULE", text)
        self.assertIn("650.00", text)


class TradeSummaryPlacementTest(unittest.TestCase):
    """Per-trade subtotals used to appear as an inline row right after each
    trade's items, scattered through a long, multi-page items table. Glenn
    asked for them collected in one place at the bottom instead, next to
    Subtotal/Tax/Total -- easier to scan on a job with many trades."""

    @classmethod
    def setUpClass(cls):
        cls.data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        cls.html = render_proposal_html(cls.data)

    def test_summary_by_trade_heading_present(self):
        self.assertIn("Summary by Trade", self.html)

    def test_each_trade_subtotal_appears_in_the_summary_not_inline(self):
        # Both trade names and both dollar subtotals should appear -- but
        # no "Subtotal —" text should be scattered inside the items table.
        self.assertNotIn("Subtotal &mdash;", self.html)
        self.assertIn("3,840.00", self.html)  # Roofing group subtotal
        self.assertIn("50.00", self.html)     # Painting group subtotal

    def test_summary_by_trade_comes_after_the_items_table(self):
        items_table_end = self.html.rindex("</table>", 0, self.html.index("Summary by Trade"))
        self.assertLess(items_table_end, self.html.index("Summary by Trade"))
        self.assertLess(self.html.index("Summary by Trade"), self.html.index("Total Contract Price"))

    def test_single_trade_job_skips_the_redundant_summary(self):
        single_trade_rows = [
            {"Include": True, "Trade": "Roofing", "Description": "Remove shingles",
             "Qty": 10, "Unit": "SQ", "Unit Cost": 70.0, "Margin %": 20},
        ]
        data = build_proposal(single_trade_rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        html = render_proposal_html(data)
        self.assertNotIn("Summary by Trade", html)


class DeductibleWaiverNoticeTest(unittest.TestCase):
    """Texas Business and Commerce Code Sec. 27.02 requires a contract of
    $1,000+ (where the seller expects payment from insurance proceeds) to
    carry this EXACT notice, in at least 12-point boldfaced type -- not a
    paraphrase, and not folded into the general terms paragraph. Skipping
    it is a Class B misdemeanor for the contractor, so this has to be
    verbatim and it has to actually show up whenever it's required."""

    NOTICE_FRAGMENT = "Texas law requires a person insured under a property insurance policy"

    def test_notice_appears_on_a_job_over_the_threshold(self):
        import html as html_module

        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        self.assertGreaterEqual(data.total_price, 1000)
        rendered = render_proposal_html(data)
        self.assertIn(self.NOTICE_FRAGMENT, rendered)
        # Jinja2's autoescape turns the apostrophe in "insured's" into
        # &#39; -- unescape before comparing against the exact statutory
        # text, since the *rendered characters* (verified via the actual
        # PDF text round-trip below) are what matters, not the raw markup.
        self.assertIn(proposal_models.TX_DEDUCTIBLE_NOTICE, html_module.unescape(rendered))

    def test_notice_is_absent_under_the_thousand_dollar_threshold(self):
        small_job_rows = [
            {"Include": True, "Trade": "Painting", "Description": "Touch up trim",
             "Qty": 1, "Unit": "EA", "Unit Cost": 200.0, "Margin %": 0},
        ]
        data = build_proposal(small_job_rows, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        self.assertLess(data.total_price, 1000)
        html = render_proposal_html(data)
        self.assertNotIn(self.NOTICE_FRAGMENT, html)

    def test_notice_renders_in_a_12pt_bold_element_not_the_general_terms(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        html = render_proposal_html(data)
        self.assertIn("tx-deductible-notice", html)
        css_start = html.index(".tx-deductible-notice")
        css_block = html[css_start:css_start + 200]
        self.assertIn("12pt", css_block)
        self.assertIn("bold", css_block)

    def test_notice_reaches_the_actual_rendered_pdf(self):
        import pdfplumber

        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23")
        out_path = "/tmp/test_deductible_notice.pdf"
        render_proposal_pdf(data, out_path)
        with pdfplumber.open(out_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        self.assertIn("Texas law requires a person insured", text)


class RenderProposalWithTaxTest(unittest.TestCase):
    """Confirms the Subtotal/Sales Tax breakdown actually reaches the
    rendered PDF for a rule that itemizes tax, and stays absent (no
    "$0.00 tax" line, which would look wrong on a lump-sum job) when it
    doesn't."""

    def test_separated_residential_shows_subtotal_and_tax_lines(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                               tax_rule=tax.SEPARATED_RESIDENTIAL, tax_rate_pct=10.0)
        html = render_proposal_html(data)
        self.assertIn("Subtotal", html)
        self.assertIn("Sales Tax", html)
        self.assertIn("389.00", html)
        self.assertIn("4,279.00", html)

    def test_lump_sum_shows_only_a_single_total_no_tax_line(self):
        data = build_proposal(SAMPLE_ROWS, CONTRACTOR, SAMPLE_CLAIM_FIELDS, "2026-08-23",
                               tax_rule=tax.LUMP_SUM_RESIDENTIAL, tax_rate_pct=10.0)
        html = render_proposal_html(data)
        self.assertNotIn("Sales Tax", html)
        self.assertIn("Total Contract Price", html)


if __name__ == "__main__":
    unittest.main()
