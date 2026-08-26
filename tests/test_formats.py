"""Tests for multi-format support: rule sheets, format fingerprinting,
document-type detection, the generic reader, and the confidence score.

The golden-snapshot lock (test_golden.py) proves the Xactimate path did
not change. These prove the new behaviour actually works.
"""
import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_TESTS_DIR, ".."))
sys.path.insert(0, _TESTS_DIR)

from scope_parser import confidence, doc_type, generic_columns, noise_filter, profiles  # noqa: E402
from scope_parser.carrier_summary import find_summary  # noqa: E402
from scope_parser.fingerprint import fingerprint  # noqa: E402
from scope_parser.generic_reader import parse_generic  # noqa: E402
from scope_parser.pipeline import parse_text  # noqa: E402
from scope_parser.tokens import find_qty_and_unit, split_fused_tokens, strip_tail_noise  # noqa: E402

FIXTURES = os.path.join(_TESTS_DIR, "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".txt"), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------
class RuleSheetTests(unittest.TestCase):
    def test_xactimate_unit_vocabulary_is_exactly_the_original_sixteen(self):
        """Widening this is the one sabotage the golden lock could NOT
        catch, so it is pinned here explicitly instead."""
        self.assertEqual(len(profiles.XACTIMATE.unit_tokens), 16)
        self.assertIn("SQ", profiles.XACTIMATE.unit_tokens)
        self.assertNotIn("FT", profiles.XACTIMATE.unit_tokens)

    def test_generic_vocabulary_is_a_superset_and_kept_separate(self):
        self.assertTrue(profiles.XACTIMATE.unit_tokens < profiles.GENERIC.unit_tokens)
        self.assertIn("FT", profiles.GENERIC.unit_tokens)

    def test_unverified_sheets_cannot_be_selected_for_parsing(self):
        """A sheet built from documentation rather than a real fixture may
        name a program but must never be trusted to read one."""
        self.assertIn("symbility", profiles.IDENTIFY_ONLY)
        self.assertNotIn("symbility", profiles.REGISTRY)

    def test_unknown_key_falls_back_to_the_generic_sheet(self):
        self.assertIs(profiles.get("nonesuch"), profiles.GENERIC)


# ---------------------------------------------------------------------
class FingerprintTests(unittest.TestCase):
    def test_all_three_real_fixtures_are_identified_as_xactimate(self):
        for name in ("allstate_5410", "travelers_erin", "appraiser_williams1"):
            fp = fingerprint(fixture(name).splitlines())
            self.assertEqual(fp.profile_key, "xactimate", name)
            self.assertTrue(fp.signals, name)

    def test_file_metadata_raises_confidence_to_high(self):
        lines = fixture("allstate_5410").splitlines()
        self.assertEqual(fingerprint(lines).confidence, "medium")
        with_meta = fingerprint(lines, {"Creator": "Xactimate 24.6.1000.2"})
        self.assertEqual(with_meta.confidence, "high")
        self.assertEqual(with_meta.program_name, "Xactimate 24.6.1000.2")

    def test_price_list_index_gives_the_state(self):
        fp = fingerprint(fixture("allstate_5410").splitlines())
        self.assertEqual(fp.jurisdiction_state, "TX")
        self.assertEqual(fp.price_list_code, "TXHO8X_AUG24")

    def test_a_print_to_pdf_file_still_identifies_from_the_page(self):
        """The exact case that made metadata-only routing wrong: a real
        Cotality estimate whose Producer is just the print driver."""
        fp = fingerprint(
            fixture("travelers_erin").splitlines(),
            {"Producer": "Microsoft: Print To PDF", "Creator": ""},
        )
        self.assertEqual(fp.profile_key, "xactimate")
        self.assertEqual(fp.confidence, "medium")

    def test_an_unidentifiable_document_falls_to_the_generic_reader(self):
        fp = fingerprint(["Some Company", "Widget 3.00 EA 5.00 15.00"])
        self.assertFalse(fp.is_recognised)
        self.assertEqual(fp.profile_key, "generic")


# ---------------------------------------------------------------------
class DocumentTypeTests(unittest.TestCase):
    def test_a_markup_column_means_the_prices_are_already_marked_up(self):
        """The money bug: this document parses cleanly and is wrong."""
        lines = [
            "Category Description Qty Unit Contractor Unit Price Markup % Total",
            "Roofing Shingles 10.00 SQ 250.00 20% 3000.00",
        ]
        kind = doc_type.detect(lines, item_count=1)
        self.assertEqual(kind.kind, doc_type.CONTRACTOR_PROPOSAL)
        self.assertTrue(kind.prices_already_marked_up)

    def test_side_by_side_scopes_are_a_supplement_package(self):
        lines = ["Carrier Scoped Requested Scope IRC Code Justification Supplement Delta ($)"]
        self.assertEqual(doc_type.detect(lines, item_count=2).kind, doc_type.SUPPLEMENT_PACKAGE)

    def test_a_self_described_supplement_is_recognised_by_its_own_text(self):
        """A document that SAYS it's a supplement/reinspection -- unlike
        the side-by-side supplement PACKAGE layout above -- is its own
        kind, because the totals need to be read as an addition to an
        earlier claim, not the whole claim."""
        class _Flags:
            is_supplement_document = True
            is_appraisal_document = False
            is_public_adjuster_document = False

        kind = doc_type.detect(
            ["Item 1. Remove shingles 20.00 SQ 200.00 4000.00", "Total: 4,000.00"],
            item_count=1,
            claim_flags=_Flags(),
        )
        self.assertEqual(kind.kind, doc_type.SUPPLEMENT_REINSPECTION)
        self.assertIn("addition to a prior claim", kind.advice)

    def test_the_structural_supplement_package_wins_over_the_text_signal(self):
        """A document with side-by-side carrier/requested columns IS a
        supplement package regardless of what its prose says -- the more
        specific, structural label wins."""
        class _Flags:
            is_supplement_document = True  # the prose signal
            is_appraisal_document = False
            is_public_adjuster_document = False

        lines = ["Carrier Scoped Requested Scope Supplement Delta ($)"]
        kind = doc_type.detect(lines, item_count=2, claim_flags=_Flags())
        self.assertEqual(kind.kind, doc_type.SUPPLEMENT_PACKAGE)

    def test_a_settlement_statement_is_not_a_failed_parse(self):
        lines = ["Gross RCV 48,000.00", "Net Claim Payable 39,900.00", "Deductible 2,500.00"]
        kind = doc_type.detect(lines, item_count=0, has_anchors=False)
        self.assertEqual(kind.kind, doc_type.SETTLEMENT_STATEMENT)
        self.assertFalse(kind.line_items_expected)
        self.assertIn("scope", kind.advice.lower())

    def test_ordinary_carrier_estimates_are_not_mislabelled(self):
        for name in ("allstate_5410", "travelers_erin"):
            est = parse_text(fixture(name))
            self.assertEqual(est.document_type.kind, doc_type.CARRIER_SCOPE, name)
            self.assertFalse(est.margin_locked_at_zero, name)


# ---------------------------------------------------------------------
class ColumnSolvingTests(unittest.TestCase):
    def test_simple_quantity_times_price_equals_total(self):
        rows = [(140.0, [2.86, 400.40]), (35.01, [58.24, 2039.02]), (12.5, [10.0, 125.0])]
        solution = generic_columns.solve(rows)
        self.assertEqual((solution.price_index, solution.total_index), (0, 1))
        self.assertFalse(solution.total_includes_extras)

    def test_columns_added_into_the_total_are_found_and_named(self):
        """Tax and O&P sit between the price and the total on several
        carriers -- the identity has to allow for them."""
        rows = [
            (10.0, [100.0, 8.25, 216.5, 1224.75]),
            (5.0, [20.0, 1.65, 20.33, 121.98]),
            (2.0, [50.0, 4.13, 20.83, 124.96]),
        ]
        solution = generic_columns.solve(rows)
        self.assertEqual((solution.price_index, solution.total_index), (0, 3))
        self.assertEqual(solution.addend_indexes, (1, 2))
        self.assertTrue(solution.total_includes_extras)

    def test_all_quantities_of_one_is_reported_as_ambiguous(self):
        rows = [(1.0, [50.0, 50.0, 50.0]), (1.0, [75.0, 75.0, 75.0]), (1.0, [20.0, 20.0, 20.0])]
        self.assertIsNone(generic_columns.solve(rows))
        self.assertIn("quantity", generic_columns.diagnose(rows))

    def test_nothing_balancing_returns_nothing_rather_than_a_guess(self):
        rows = [(3.0, [7.0, 99.0]), (4.0, [8.0, 123.0]), (5.0, [9.0, 150.0])]
        self.assertIsNone(generic_columns.solve(rows))

    def test_a_quantity_of_one_row_cannot_outvote_a_proven_one(self):
        """Replacement cost equals actual cash value whenever depreciation
        is zero, which makes those two columns satisfy the identity for
        free on any qty-1 row. Found on the real appraiser fixture."""
        rows = [
            (1.0, [312.07, 40.0, 53.62, 405.69, 405.69]),
            (2.0, [100.0, 10.0, 20.0, 230.0, 230.0]),
            (4.0, [25.0, 5.0, 10.0, 115.0, 115.0]),
        ]
        solution = generic_columns.solve(rows)
        self.assertEqual(solution.price_index, 0)
        self.assertEqual(solution.total_index, 3)


# ---------------------------------------------------------------------
class GenericReaderTests(unittest.TestCase):
    """The acceptance test for the generic reader: told nothing at all
    about Xactimate, it must still find the same items and the same money
    on real Xactimate documents."""

    def _compare(self, name):
        reference = parse_text(fixture(name))
        kept, _ = noise_filter.strip_noise(fixture(name).splitlines())
        items, _, _ = parse_generic(kept)
        by_number = {}
        for item in items:
            by_number.setdefault(item.number, item)
        return reference, items, by_number

    def test_allstate_is_read_identically_with_no_format_knowledge(self):
        reference, items, by_number = self._compare("allstate_5410")
        self.assertEqual(len(items), len(reference.line_items))
        for ref in reference.line_items:
            got = by_number[ref.number]
            self.assertEqual(got.unit_price, ref.unit_price, f"item {ref.number} price")
            self.assertEqual(got.rcv, ref.rcv, f"item {ref.number} total")
            self.assertEqual(got.quantity, ref.quantity, f"item {ref.number} qty")

    def test_travelers_is_read_identically_including_tax_and_op_columns(self):
        reference, items, by_number = self._compare("travelers_erin")
        self.assertEqual(len(items), len(reference.line_items))
        for ref in reference.line_items:
            got = by_number[ref.number]
            self.assertEqual(got.unit_price, ref.unit_price, f"item {ref.number} price")
            self.assertEqual(got.rcv, ref.rcv, f"item {ref.number} total")

    def test_measurement_blocks_are_not_mistaken_for_line_items(self):
        """A bare "346.13 SF" has a quantity and a unit but no description
        and no figures -- seventeen of these became phantom line items
        before the guard was added."""
        _, items, _ = self._compare("allstate_5410")
        self.assertTrue(all(item.description for item in items))

    def test_unreadable_rows_are_flagged_not_invented(self):
        _, items, _ = self._compare("appraiser_williams1")
        for item in items:
            if item.needs_review:
                self.assertTrue(item.review_reason)
                self.assertTrue(item.raw_tail_tokens or item.quantity is None)

    def test_symbility_style_rows_anchor_correctly(self):
        """Action-prefixed rows with per-unit price notation ("$3.99 / LF")."""
        units = profiles.GENERIC.unit_tokens
        tokens = split_fused_tokens("1 Remove - Drip edge 140.00 LF $3.99 / LF $558.60".split(), units)
        index = find_qty_and_unit(tokens, units)
        self.assertEqual((tokens[index], tokens[index + 1]), ("140.00", "LF"))
        self.assertEqual(strip_tail_noise(tokens[index + 2:], units), ["$3.99", "$558.60"])

    def test_a_row_with_no_quantity_refuses_to_anchor_on_the_price_unit(self):
        units = profiles.GENERIC.unit_tokens
        tokens = split_fused_tokens("2 Replace - Drip edge $3.99 / LF $558.60".split(), units)
        self.assertIsNone(find_qty_and_unit(tokens, units))

    def test_an_unnumbered_format_still_gets_usable_row_numbers(self):
        lines = [
            "Remove and replace drip edge 140.00 LF 2.86 400.40",
            "Replace ridge cap 98.63 LF 4.85 478.36",
            "Install underlayment 20.00 SQ 45.00 900.00",
            "Total: 1,778.76",
        ]
        items, totals, _ = parse_generic(lines)
        self.assertEqual(len(items), 3)
        self.assertEqual([i.number for i in items], ["1", "2", "3"])
        self.assertEqual(items[0].unit_price, 2.86)
        self.assertEqual(items[0].rcv, 400.40)
        self.assertEqual(totals[0][2], 1778.76)


# ---------------------------------------------------------------------
class ContractorExportTests(unittest.TestCase):
    """Xactimate's contractor-facing export (the Doyle repair scope).

    Same program, completely different printed layout: three action cost
    columns (RESET / REMOVE / REPLACE) instead of one price column, no
    UNIT column header at all, and a line total that already includes
    sales tax. Before this was handled it parsed 131 items with the money
    scrambled -- prices read as totals and totals read as prices -- which
    is worse than failing, because it looked like it had worked.
    """

    @classmethod
    def setUpClass(cls):
        cls.est = parse_text(fixture("contractor_doyle"))

    def test_the_action_column_header_is_recognised(self):
        from scope_parser import schema

        header = "DESCRIPTION QTY RESET REMOVE REPLACE *TOTAL"
        self.assertTrue(schema.is_header_line(header))
        fields = schema.parse_header(header)
        self.assertEqual(
            fields, ["action_reset", "action_remove", "action_replace", "rcv"]
        )
        # No unit price is printed on this layout, so the "insert the
        # missing PRICE column" correction must NOT fire here.
        self.assertNotIn("unit_price", fields)

    def test_action_costs_add_up_to_the_unit_price(self):
        """An R&R line costs its tear-out plus its install."""
        item = next(i for i in self.est.line_items if i.number == "9")
        self.assertEqual(item.quantity, 172.50)
        self.assertEqual(item.unit, "SF")
        self.assertEqual(item.unit_price, 4.73)  # 1.77 remove + 2.96 replace
        self.assertEqual(item.rcv, 834.72)

    def test_tax_inside_the_printed_total_is_recorded_not_buried(self):
        """The total is tax-inclusive. The gap is captured as tax, so the
        contractor's margin still applies to the cost of the work."""
        item = next(i for i in self.est.line_items if i.number == "9")
        self.assertEqual(item.tax, 18.79)
        self.assertAlmostEqual(
            item.quantity * item.unit_price + item.tax, item.rcv, places=2
        )

    def test_labour_only_rows_carry_no_tax(self):
        item = next(i for i in self.est.line_items if i.number == "10")
        self.assertIsNone(item.tax)
        self.assertAlmostEqual(item.quantity * item.unit_price, item.rcv, places=2)

    def test_every_line_parses_and_the_document_reconciles(self):
        self.assertEqual(len(self.est.line_items), 131)
        self.assertEqual(self.est.needs_review_items, [])
        self.assertTrue(all(s.matched for s in self.est.section_totals))
        # The document prints its own "Line Item Totals: 57,976.93".
        self.assertAlmostEqual(
            sum(i.rcv for i in self.est.line_items if i.rcv), 57976.93, places=2
        )

    def test_it_is_recognised_as_xactimate_from_its_file_metadata(self):
        est = parse_text(
            fixture("contractor_doyle"), {"Creator": "Xactimate 24.8.1004.1"}
        )
        self.assertEqual(est.fingerprint.profile_key, "xactimate")
        self.assertEqual(est.fingerprint.confidence, "high")
        self.assertEqual(est.confidence.state, confidence.RECOGNISED)


class SymbilityTests(unittest.TestCase):
    """A real Liberty Mutual claim priced off Cotality.

    Its quantity is followed by the PRICE, with the unit after that
    ("6.00 $1.63 LF"), so the quantity/unit anchor never fired and the
    whole document produced ONE phantom line item. Its file metadata says
    `Microsoft: Print To PDF` -- the exact case that makes metadata-only
    format routing wrong.
    """

    @classmethod
    def setUpClass(cls):
        cls.est = parse_text(
            fixture("symbility_libertymutual"),
            {"Producer": "Microsoft: Print To PDF", "Title": "Claim 060929297"},
        )

    def test_the_quantity_price_unit_layout_is_read(self):
        item = next(i for i in self.est.line_items if i.number == "1")
        self.assertEqual(item.quantity, 6.0)
        self.assertEqual(item.unit, "LF")
        self.assertEqual(item.unit_price, 1.63)
        self.assertEqual(item.rcv, 9.78)

    def test_the_bracketed_ordered_quantity_is_the_priced_one(self):
        """"22.21 (22.33)" -- bundle rounding. Pricing off the measured
        22.21 leaves the line $12.96 short and the section out by $2,606."""
        item = next(i for i in self.est.line_items if i.number == "13")
        self.assertEqual(item.quantity, 22.33)
        self.assertEqual(item.unit_price, 107.81)
        self.assertEqual(item.rcv, 2606.01)
        self.assertTrue(any("22.21" in note for note in item.notes))

    def test_plan_measurements_are_not_read_as_line_items(self):
        """"Roof area: 2,799.23 SF Squares: 28.0 SQ Soffit: 690.70 SF"
        invented a $690.70 row -- exactly the amount the roof subtotal was
        out by."""
        self.assertFalse(
            any("Roof area" in i.description for i in self.est.line_items)
        )

    def test_the_roof_section_reconciles_exactly(self):
        roof = next(
            s for s in self.est.section_totals if s.section == "Roof" and not s.skipped
        )
        self.assertEqual(roof.parsed_rcv_sum, 17811.00)
        self.assertIn(17811.00, roof.printed_numbers)
        self.assertTrue(roof.matched)

    def test_subtotal_sections_are_named_the_way_the_document_names_them(self):
        names = {s.section for s in self.est.section_totals}
        self.assertIn("Roof", names)
        self.assertIn("Exterior Plan", names)
        self.assertNotIn("(2 items)", names)

    def test_the_program_is_named_even_though_it_cannot_be_trusted_to_parse(self):
        fp = self.est.fingerprint
        self.assertIsNotNone(fp)
        self.assertIn("Symbility", fp.identified_as)
        self.assertEqual(fp.profile_key, "generic")
        self.assertTrue(fp.is_identified)
        self.assertFalse(fp.is_recognised)

    def test_the_banner_names_the_program_rather_than_saying_unrecognised(self):
        conf = self.est.confidence
        self.assertIsNotNone(conf)
        self.assertIn("Symbility", conf.headline)
        self.assertIn("general reader", conf.detail)

    def test_claim_details_survive_the_different_label_wording(self):
        fields = self.est.metadata.fields
        self.assertEqual(fields.get("claim_number"), "060929297")
        self.assertEqual(fields.get("policy_number"), "OY8566165")
        self.assertEqual(fields.get("type_of_loss"), "Hail")
        self.assertEqual(fields.get("date_of_loss"), "05/06/2025")
        # The loss address, not the insured's mailing address.
        self.assertIn("PALMER DR", fields.get("property_address", ""))

    def test_the_state_comes_from_the_pricing_database_line(self):
        self.assertEqual(self.est.fingerprint.jurisdiction_state, "TX")

    def test_a_row_that_does_not_multiply_out_is_flagged(self):
        flagged = self.est.needs_review_items
        self.assertEqual(len(flagged), 1)
        self.assertIn("Minimum Charge", flagged[0].description)


class CarrierSummaryTests(unittest.TestCase):
    """The document-level Overhead/Profit/Net Claim ladder -- previously
    never read at all, so the app understated every Xactimate document's
    real total by exactly its printed Overhead + Profit. See
    carrier_summary.py.
    """

    def test_a_single_coverage_contractor_export_reconciles_clean(self):
        """Doyle: no coverage split, so our own parsed sum must match the
        document's own "Line Item Total" exactly, and the 10%/10% O&P is
        readable as a clean, single figure."""
        est = parse_text(fixture("contractor_doyle"))
        cs = est.carrier_summary
        self.assertIsNotNone(cs)
        self.assertIsNone(cs.coverage_label)
        self.assertEqual(cs.line_item_total, 57976.93)
        self.assertEqual(cs.overhead, 5797.87)
        self.assertEqual(cs.profit, 5797.87)
        self.assertEqual(cs.overhead_pct, 10.0)
        self.assertEqual(cs.profit_pct, 10.0)
        self.assertEqual(cs.combined_markup_pct, 20.0)
        self.assertEqual(cs.replacement_cost_value, 69572.67)
        self.assertEqual(cs.net_claim, 69572.67)
        self.assertEqual(cs.source_label, "Net Claim")
        self.assertTrue(cs.reconciles_with_parsed_items)
        self.assertEqual(cs.parsed_items_sum, 57976.93)
        self.assertEqual(est.warnings, [])

    def test_overhead_and_profit_are_a_percentage_of_the_taxed_subtotal(self):
        """Williams1: Overhead/Profit are 15%/15% of Line Item Total PLUS
        Material Sales Tax, not of Line Item Total alone -- dividing by
        the bare line item total would read this as a misleading
        15.41%/15.41% instead of the document's actual clean 15%/15%."""
        est = parse_text(fixture("appraiser_williams1"))
        cs = est.carrier_summary
        self.assertIsNotNone(cs)
        self.assertEqual(cs.line_item_total, 26235.67)
        self.assertEqual(cs.material_sales_tax, 720.79)
        self.assertEqual(cs.overhead, 4043.46)
        self.assertEqual(cs.overhead_pct, 15.0)
        self.assertEqual(cs.profit_pct, 15.0)
        self.assertEqual(cs.combined_markup_pct, 30.0)

    def test_a_multi_coverage_document_is_labelled_and_not_falsely_flagged(self):
        """Allstate's Summary block is scoped to AA-Dwelling only -- our
        own parsed sum spans Dwelling + Other Structures + Personal
        Property, so it will never equal that one coverage's total. The
        coverage_label is what tells the app (and this test) that's
        expected, not a parse failure -- no warning should fire."""
        est = parse_text(fixture("allstate_5410"))
        cs = est.carrier_summary
        self.assertIsNotNone(cs)
        self.assertEqual(cs.coverage_label, "AA-Dwelling")
        self.assertEqual(cs.line_item_total, 14410.37)
        self.assertIsNone(cs.reconciles_with_parsed_items)
        self.assertEqual(est.warnings, [])

    def test_the_deductible_ladder_reads_net_claim_after_deductible(self):
        est = parse_text(fixture("allstate_5410"))
        cs = est.carrier_summary
        self.assertIsNotNone(cs)
        self.assertEqual(cs.replacement_cost_value, 14652.30)
        self.assertEqual(cs.deductible, 6754.00)
        self.assertEqual(cs.net_claim, 7805.52)
        self.assertEqual(cs.net_claim_if_depreciation_recovered, 7898.30)

    def test_symbility_uses_different_words_for_the_same_ladder(self):
        """Symbility has no Overhead/Profit lines at all on this claim
        (every line item's own O&P column was already $0.00) but does
        print the rest of the ladder under its own vocabulary -- "Net
        Estimate" instead of "Net Claim"."""
        est = parse_text(fixture("symbility_libertymutual"))
        cs = est.carrier_summary
        self.assertIsNotNone(cs)
        self.assertIsNone(cs.overhead)
        self.assertIsNone(cs.profit)
        self.assertIsNone(cs.line_item_total)  # no reconciliation base on this format
        self.assertEqual(cs.replacement_cost_value, 18139.98)
        self.assertEqual(cs.actual_cash_value, 18139.98)
        self.assertEqual(cs.deductible, 3073.00)
        self.assertEqual(cs.net_claim, 15066.98)
        self.assertEqual(cs.source_label, "Net Estimate")

    def test_a_tabular_op_recap_is_not_misread_as_the_simple_ladder(self):
        """Travelers prints Overhead/Profit as a wide table ("Overhead
        Profit (10%) Material Sales ...") rather than the simple
        label-then-number lines every other fixture uses. That format
        isn't read by this module yet -- it must not crash, and it must
        not misfire on the table header as if it were a summary line."""
        est = parse_text(fixture("travelers_erin"))
        self.assertIsNone(est.carrier_summary)

    def test_a_document_with_no_summary_block_returns_none(self):
        self.assertIsNone(find_summary(["just some ordinary text", "nothing here"]))


class ConfidenceTests(unittest.TestCase):
    def test_a_clean_recognised_document_says_so(self):
        est = parse_text(fixture("allstate_5410"))
        conf = est.confidence
        self.assertIsNotNone(conf)
        self.assertEqual(conf.state, confidence.RECOGNISED)
        self.assertIn("Xactimate", conf.headline)
        self.assertEqual(conf.items_flagged, 0)
        self.assertTrue(conf.all_reconciled)

    def test_every_parse_gets_a_state_and_a_sentence(self):
        """There is no silent parse: three real documents, three verdicts,
        each with something a contractor can actually read."""
        for name in ("allstate_5410", "travelers_erin", "appraiser_williams1"):
            est = parse_text(fixture(name))
            conf = est.confidence
            self.assertIsNotNone(conf)
            self.assertIn(
                conf.state,
                (confidence.RECOGNISED, confidence.GENERIC_OK, confidence.LOW),
                name,
            )
            self.assertTrue(conf.headline, name)
            self.assertTrue(conf.detail, name)

    def test_a_document_with_no_scope_is_not_called_a_failure(self):
        est = parse_text(
            "Statement of Loss\nGross RCV 48,000.00\nNet Claim Payable 39,900.00\n"
        )
        conf = est.confidence
        self.assertIsNotNone(conf)
        self.assertEqual(conf.state, confidence.NOT_A_SCOPE)
        self.assertIn("settlement statement", conf.headline.lower())

    def test_an_unrecognised_format_is_told_apart_from_a_bad_parse(self):
        est = parse_text(
            "ACME ESTIMATING\n"
            "Remove and replace drip edge 140.00 LF 2.86 400.40\n"
            "Replace ridge cap 98.63 LF 4.85 478.36\n"
            "Install underlayment 20.00 SQ 45.00 900.00\n"
            "Total: 1,778.76\n"
        )
        fp = est.fingerprint
        conf = est.confidence
        self.assertIsNotNone(fp)
        self.assertIsNotNone(conf)
        self.assertFalse(fp.is_recognised)
        self.assertEqual(len(est.line_items), 3)
        self.assertEqual(conf.state, confidence.GENERIC_OK)
        self.assertIn("don't recognise", conf.headline)


if __name__ == "__main__":
    unittest.main()
