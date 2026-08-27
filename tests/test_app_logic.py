"""Tests app.py's data-shaping helpers without needing Streamlit
installed. This sandbox couldn't reach PyPI to install it, so a fake
`streamlit` module is registered in sys.modules before importing app.py --
enough to satisfy the module-level `st.set_page_config(...)` call, since
none of the functions tested here (_rows_from_estimate, _best) actually
call into Streamlit themselves. The real widget/layout code in main()
still needs an actual `streamlit run app.py` to verify visually -- see
README's testing note.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("streamlit", MagicMock())

import app  # noqa: E402
from scope_parser import parse_pdf, parse_text  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class RowsFromEstimateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.estimate = parse_pdf(os.path.join(FIXTURES_DIR, "synthetic_sample.pdf"))

    def test_one_row_per_line_item(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        self.assertEqual(len(rows), len(self.estimate.line_items))

    def test_default_margin_and_include_are_set(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=15)
        self.assertTrue((rows["Margin %"] == 15).all())
        self.assertTrue(rows["Include"].all())

    def test_insurance_rcv_column_matches_parsed_rcv(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        parsed_rcvs = [li.rcv for li in self.estimate.line_items]
        self.assertEqual(list(rows["Insurance RCV"]), parsed_rcvs)

    def test_trade_column_is_pre_filled(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        self.assertTrue((rows["Trade"] != "").all())

    def test_material_column_defaults_true(self):
        # Safer default for sales tax purposes -- see tax.py: undercharging
        # tax under a separated contract is the costlier mistake.
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        self.assertTrue(rows["Material"].all())

    def test_insurance_op_column_matches_parsed_overhead_profit(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        parsed_op = [li.overhead_profit for li in self.estimate.line_items]
        self.assertEqual(list(rows["Insurance O&P"]), parsed_op)

    def test_code_cite_column_matches_parsed_code_related_flag(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        parsed_flags = [li.code_related for li in self.estimate.line_items]
        self.assertEqual(list(rows["Code Cite"]), parsed_flags)


class TradeTotalsTest(unittest.TestCase):
    """_trade_totals -- the per-trade subtotal breakdown that mirrors how
    the exported proposal PDF groups its own line items (see
    proposal/build.py's group_line_items)."""

    def test_groups_and_sums_by_trade_highest_first(self):
        import pandas as pd

        included = pd.DataFrame([
            {"Trade": "Roofing", "Your Price": 100.0},
            {"Trade": "Siding", "Your Price": 500.0},
            {"Trade": "Roofing", "Your Price": 50.0},
        ])
        totals = app._trade_totals(included)
        self.assertEqual(list(totals["Trade"]), ["Siding", "Roofing"])
        self.assertEqual(list(totals["Subtotal"]), [500.0, 150.0])

    def test_empty_included_returns_empty_frame_with_right_columns(self):
        import pandas as pd

        totals = app._trade_totals(pd.DataFrame(columns=["Trade", "Your Price"]))
        self.assertEqual(list(totals.columns), ["Trade", "Subtotal"])
        self.assertEqual(len(totals), 0)

    def test_real_estimate_trade_totals_sum_to_the_grand_total(self):
        rows = app._rows_from_estimate(self.estimate, default_margin=20)
        rows["Your Price"] = rows.apply(
            lambda r: app.compute_line_total(r["Qty"], r["Unit Cost"], r["Margin %"]), axis=1
        )
        totals = app._trade_totals(rows)
        self.assertAlmostEqual(totals["Subtotal"].sum(), rows["Your Price"].sum(), places=2)

    @classmethod
    def setUpClass(cls):
        cls.estimate = parse_pdf(os.path.join(FIXTURES_DIR, "synthetic_sample.pdf"))


class EmptyEstimateTest(unittest.TestCase):
    """A PDF the parser can extract text from but can't recognize any line
    items in (wrong/unsupported layout) must not crash the app. Regression
    test for a real bug: pd.DataFrame([]) has NO columns at all -- not even
    "Include" -- so code downstream that does rows["Include"] blew up with
    a raw KeyError instead of the friendly message main() now shows."""

    def test_zero_line_items_still_has_include_column(self):
        estimate = parse_text("this text has no recognizable line items in it at all")
        self.assertEqual(len(estimate.line_items), 0)
        rows = app._rows_from_estimate(estimate, default_margin=20)
        self.assertIn("Include", rows.columns)
        self.assertEqual(len(rows), 0)

    def test_zero_line_items_has_all_expected_columns(self):
        estimate = parse_text("")
        rows = app._rows_from_estimate(estimate, default_margin=20)
        self.assertEqual(list(rows.columns), app._TABLE_COLUMNS)


class ManualRowTest(unittest.TestCase):
    """_manual_row -- the "Add a line item the carrier missed" form's row
    builder. Must match _rows_from_estimate's column shape exactly so a
    hand-typed row and a parsed row are indistinguishable to the rest of
    the pipeline (pricing, trade totals, proposal export)."""

    def test_has_every_expected_column(self):
        row = app._manual_row("New gutter", "Gutters", 10, "LF", 5.0, 20, True)
        self.assertEqual(set(row.keys()), set(app._TABLE_COLUMNS))

    def test_fields_carry_through_as_given(self):
        row = app._manual_row("  New gutter  ", "Gutters", 10, "LF", 5.0, 20, True)
        self.assertEqual(row["Description"], "New gutter")  # whitespace trimmed
        self.assertEqual(row["Trade"], "Gutters")
        self.assertEqual(row["Qty"], 10)
        self.assertEqual(row["Unit"], "LF")
        self.assertEqual(row["Unit Cost"], 5.0)
        self.assertEqual(row["Margin %"], 20)
        self.assertTrue(row["Material"])
        self.assertTrue(row["Include"])

    def test_no_carrier_reference_data_since_there_is_no_carrier_line(self):
        row = app._manual_row("New gutter", "Gutters", 10, "LF", 5.0, 20, True)
        self.assertIsNone(row["Insurance RCV"])
        self.assertIsNone(row["Insurance O&P"])
        self.assertFalse(row["Code Cite"])
        self.assertFalse(row["Needs Review"])

    def test_appends_cleanly_onto_an_existing_rows_dataframe(self):
        import pandas as pd

        empty_rows = app._rows_from_estimate(parse_text(""), 20)
        row = app._manual_row("New gutter", "Gutters", 10, "LF", 5.0, 20, True)
        df = pd.concat(
            [empty_rows, pd.DataFrame([row], columns=app._TABLE_COLUMNS)], ignore_index=True
        )
        self.assertEqual(list(df.columns), app._TABLE_COLUMNS)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["Description"], "New gutter")


class ManualRowIsSupplementTest(unittest.TestCase):
    """_manual_row leaves "Insurance RCV" blank -- that's what the
    "Payment breakdown" section reads as "no carrier line behind this,
    it's a supplement." See PaymentBreakdownTest."""

    def test_manual_row_has_no_insurance_rcv(self):
        row = app._manual_row("New gutter", "Gutters", 10, "LF", 5.0, 20, True)
        self.assertIsNone(row["Insurance RCV"])

    def test_manual_row_has_no_recoverable_depreciation(self):
        row = app._manual_row("New gutter", "Gutters", 10, "LF", 5.0, 20, True)
        self.assertEqual(row["Recoverable Depreciation"], 0.0)


class VisibleColumnsTest(unittest.TestCase):
    """The payment-breakdown-only field must never clutter the Scope
    table itself -- it rides along in the DataFrame but is left out of
    what st.data_editor actually displays."""

    def test_payment_field_excluded_from_visible_columns(self):
        self.assertNotIn("Recoverable Depreciation", app._VISIBLE_COLUMNS)

    def test_the_simple_view_is_the_columns_you_actually_edit(self):
        """Fourteen columns on a horizontally-scrolling grid is how a
        contractor loses their place. The default view is these."""
        self.assertEqual(
            app._SIMPLE_COLUMNS,
            ["#", "Include", "Description", "Qty", "Unit", "Unit Cost", "Margin %", "Trade"],
        )

    def test_the_full_view_adds_the_rest_and_loses_nothing(self):
        self.assertEqual(set(app._FULL_COLUMNS), set(app._VISIBLE_COLUMNS))
        self.assertEqual(len(app._FULL_COLUMNS), len(app._VISIBLE_COLUMNS))
        for col in app._SIMPLE_COLUMNS:
            self.assertNotIn(col, app._DETAIL_COLUMNS)

    def test_the_row_number_is_first(self):
        """It's the anchor for checking a line against the PDF, so it
        belongs at the left edge of every table."""
        self.assertEqual(app._TABLE_COLUMNS[0], "#")
        self.assertEqual(app._SIMPLE_COLUMNS[0], "#")
        self.assertEqual(app._FULL_COLUMNS[0], "#")

    def test_visible_columns_is_otherwise_everything(self):
        self.assertEqual(len(app._VISIBLE_COLUMNS), len(app._TABLE_COLUMNS) - 1)
        for col in app._VISIBLE_COLUMNS:
            self.assertIn(col, app._TABLE_COLUMNS)


class PaymentBreakdownTest(unittest.TestCase):
    """_payment_breakdown -- splits the total into the real payment stages
    a restoration job gets paid in (spec from a 15-year contractor,
    2026-08-24). See the function's own docstring for the full rationale."""

    def _included(self, rows):
        import pandas as pd

        df = pd.DataFrame(rows)
        df["Your Price"] = df.apply(
            lambda r: app.compute_line_total(r["Qty"], r["Unit Cost"], r["Margin %"]), axis=1
        )
        return df

    def test_four_parts_always_sum_to_the_total(self):
        rows = [
            {"Qty": 10, "Unit Cost": 100.0, "Margin %": 20,
             "Recoverable Depreciation": 200.0, "Insurance RCV": 1000.0},  # real carrier line
            {"Qty": 1, "Unit Cost": 500.0, "Margin %": 0,
             "Recoverable Depreciation": 0.0, "Insurance RCV": None},  # supplement
        ]
        included = self._included(rows)
        total = included["Your Price"].sum()
        breakdown = app._payment_breakdown(included, deductible=1000.0, total=total)
        parts_sum = (
            breakdown["deductible"] + breakdown["first_check"]
            + breakdown["recoverable_depreciation"] + breakdown["supplements"]
        )
        self.assertAlmostEqual(parts_sum, total, places=2)

    def test_first_check_is_the_remainder(self):
        rows = [{"Qty": 1, "Unit Cost": 10000.0, "Margin %": 0,
                  "Recoverable Depreciation": 1500.0, "Insurance RCV": 10000.0}]
        included = self._included(rows)
        total = included["Your Price"].sum()
        breakdown = app._payment_breakdown(included, deductible=1000.0, total=total)
        self.assertEqual(breakdown["deductible"], 1000.0)
        self.assertEqual(breakdown["recoverable_depreciation"], 1500.0)
        self.assertEqual(breakdown["supplements"], 0.0)
        self.assertEqual(breakdown["first_check"], total - 1000.0 - 1500.0)

    def test_recoverable_depreciation_is_not_scaled_by_margin(self):
        # Confirmed with the user: this is the carrier's own fixed figure,
        # not a percentage of the contractor's price.
        rows = [{"Qty": 1, "Unit Cost": 1000.0, "Margin %": 50,
                  "Recoverable Depreciation": 100.0, "Insurance RCV": 1000.0}]
        included = self._included(rows)
        total = included["Your Price"].sum()  # 1500 at 50% margin
        breakdown = app._payment_breakdown(included, deductible=0.0, total=total)
        self.assertEqual(breakdown["recoverable_depreciation"], 100.0)  # unchanged by the 50%

    def test_supplement_row_counts_at_contractor_price(self):
        # No "Insurance RCV" at all -- exactly what _manual_row() produces.
        rows = [{"Qty": 1, "Unit Cost": 100.0, "Margin %": 20,
                  "Recoverable Depreciation": 0.0, "Insurance RCV": None}]
        included = self._included(rows)
        total = included["Your Price"].sum()  # 120
        breakdown = app._payment_breakdown(included, deductible=0.0, total=total)
        self.assertEqual(breakdown["supplements"], 120.0)

    def test_a_real_carrier_row_with_a_zero_dollar_rcv_is_not_a_supplement(self):
        # A genuine $0.00 carrier line is a real value, not "blank" --
        # must not be misread as a supplement.
        rows = [{"Qty": 1, "Unit Cost": 100.0, "Margin %": 0,
                  "Recoverable Depreciation": 0.0, "Insurance RCV": 0.0}]
        included = self._included(rows)
        total = included["Your Price"].sum()
        breakdown = app._payment_breakdown(included, deductible=0.0, total=total)
        self.assertEqual(breakdown["supplements"], 0.0)

    def test_none_deductible_treated_as_zero_not_guessed(self):
        rows = [{"Qty": 1, "Unit Cost": 100.0, "Margin %": 0,
                  "Recoverable Depreciation": 0.0, "Insurance RCV": 100.0}]
        included = self._included(rows)
        total = included["Your Price"].sum()
        breakdown = app._payment_breakdown(included, deductible=None, total=total)
        self.assertEqual(breakdown["deductible"], 0.0)
        self.assertEqual(breakdown["first_check"], total)

    def test_empty_included_returns_all_zero_except_first_check(self):
        import pandas as pd

        included = pd.DataFrame(columns=["Recoverable Depreciation", "Insurance RCV", "Your Price"])
        breakdown = app._payment_breakdown(included, deductible=500.0, total=500.0)
        self.assertEqual(breakdown["recoverable_depreciation"], 0.0)
        self.assertEqual(breakdown["supplements"], 0.0)
        self.assertEqual(breakdown["first_check"], 0.0)


class BestFieldHelperTest(unittest.TestCase):
    def test_returns_first_present_key(self):
        self.assertEqual(app._best({"a": "x", "b": "y"}, "a", "b"), "x")

    def test_falls_through_to_second_key(self):
        self.assertEqual(app._best({"b": "y"}, "a", "b"), "y")

    def test_default_when_nothing_present(self):
        self.assertEqual(app._best({}, "a", "b"), "--")


class SlugifyTest(unittest.TestCase):
    def test_strips_special_characters(self):
        self.assertEqual(app._slugify("State Farm & Co."), "State_Farm_Co")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(app._slugify("Liberty   Mutual"), "Liberty_Mutual")

    def test_keeps_hyphens_and_digits(self):
        self.assertEqual(app._slugify("Claim-0761262757"), "Claim-0761262757")

    def test_strips_leading_trailing_whitespace(self):
        self.assertEqual(app._slugify("  Acme Roofing  "), "Acme_Roofing")


class ExportFilenameTest(unittest.TestCase):
    """_export_filename -- "name your export file" feature: contractor
    name, then insurance company, then claim number, so a folder full of
    downloads is distinguishable instead of "proposal.pdf", "proposal
    (1).pdf"..."""

    def test_all_three_pieces_present(self):
        name = app._export_filename("Acme Roofing", "State Farm", "0761262757", "pdf", "proposal.pdf")
        self.assertEqual(name, "Acme_Roofing_State_Farm_0761262757.pdf")

    def test_special_characters_stripped(self):
        name = app._export_filename("Acme Roofing", "State Farm & Co.", "0761262757", "pdf", "proposal.pdf")
        self.assertEqual(name, "Acme_Roofing_State_Farm_Co_0761262757.pdf")

    def test_missing_claim_number_skipped(self):
        name = app._export_filename("Acme Roofing", "State Farm", "", "pdf", "proposal.pdf")
        self.assertEqual(name, "Acme_Roofing_State_Farm.pdf")

    def test_best_default_placeholder_excluded(self):
        # _best() returns "--" for a field it couldn't find -- that
        # literal shouldn't end up baked into the file name.
        name = app._export_filename("Acme Roofing", "--", "--", "pdf", "proposal.pdf")
        self.assertEqual(name, "Acme_Roofing.pdf")

    def test_all_pieces_missing_falls_back(self):
        name = app._export_filename("", "", "", "pdf", "proposal.pdf")
        self.assertEqual(name, "proposal.pdf")

    def test_none_pieces_treated_as_missing(self):
        name = app._export_filename(None, None, None, "pdf", "proposal.pdf")
        self.assertEqual(name, "proposal.pdf")

    def test_extension_and_fallback_are_generic(self):
        name = app._export_filename("Acme Roofing", "State Farm", "0761262757", "csv", "scope.csv")
        self.assertEqual(name, "Acme_Roofing_State_Farm_0761262757.csv")

        name = app._export_filename("", "", "", "csv", "scope.csv")
        self.assertEqual(name, "scope.csv")


class EffectiveDeductibleTest(unittest.TestCase):
    """_effective_deductible -- the one deductible the app actually uses,
    falling back from the coverage table to the summary ladder. Regression
    test for a real gap: a document that only prints its deductible in the
    summary (Symbility's "Deductible: $3,073.00" ladder) used to prompt
    for a manual entry even though the number was right there on the page."""

    @staticmethod
    def _estimate(claim_deductible=None, summary_deductible=None):
        class _Flags:
            dwelling_deductible = claim_deductible

        class _Summary:
            deductible = summary_deductible

        class _Estimate:
            claim_flags = _Flags()
            carrier_summary = _Summary() if summary_deductible is not None else None

        return _Estimate()

    def test_uses_the_coverage_table_when_both_exist(self):
        est = self._estimate(claim_deductible=2650.0, summary_deductible=1000.0)
        self.assertEqual(app._effective_deductible(est), 2650.0)

    def test_falls_back_to_the_summary_when_coverage_table_is_absent(self):
        est = self._estimate(claim_deductible=None, summary_deductible=3073.0)
        self.assertEqual(app._effective_deductible(est), 3073.0)

    def test_returns_none_when_neither_printed_one_exists(self):
        est = self._estimate()
        self.assertIsNone(app._effective_deductible(est))

    def test_survives_a_void_carrier_summary(self):
        est = self._estimate(claim_deductible=500.0)
        self.assertEqual(app._effective_deductible(est), 500.0)

    def test_survives_a_zero_summary_deductible(self):
        # A printed $0.00 deductible is a real value, not "blank" -- but
        # it's also "nothing to add," so the fallback stops there and the
        # coverage-table figure wins anyway.
        est = self._estimate(claim_deductible=500.0, summary_deductible=0.0)
        self.assertEqual(app._effective_deductible(est), 500.0)


if __name__ == "__main__":
    unittest.main()


class RowNumberingTest(unittest.TestCase):
    """The "#" at the left of every table -- the carrier's own line number
    where there is one, so a row can be checked against the PDF in
    seconds."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(FIXTURES_DIR, "allstate_5410.txt")
        with open(path, encoding="utf-8") as fh:
            cls.estimate = parse_text(fh.read())
        cls.rows = app._rows_from_estimate(cls.estimate, 20)

    def test_carrier_line_numbers_are_carried_through(self):
        printed = [li.number for li in self.estimate.line_items]
        self.assertEqual(list(self.rows["#"]), printed)

    def test_added_rows_are_labelled_so_they_cannot_be_confused(self):
        row = app._manual_row("Extra ridge vent", "Roofing", 12, "LF", 8.0, 20, True, position=4)
        self.assertEqual(row["#"], "A4")

    def test_the_next_added_label_comes_from_the_labels_in_use(self):
        """Counting rows would collide after a deletion; reading the
        labels can't."""
        import pandas as pd

        self.assertEqual(app._next_added_label(self.rows), 1)
        with_added = pd.concat(
            [self.rows, pd.DataFrame([
                app._manual_row("a", "Roofing", 1, "EA", 1.0, 0, True, position=1),
                app._manual_row("b", "Roofing", 1, "EA", 1.0, 0, True, position=7),
            ], columns=app._TABLE_COLUMNS)],
            ignore_index=True,
        )
        self.assertEqual(app._next_added_label(with_added), 8)

    def test_it_handles_an_empty_table(self):
        import pandas as pd

        self.assertEqual(app._next_added_label(pd.DataFrame(columns=app._TABLE_COLUMNS)), 1)


class ReviewScreenTest(unittest.TestCase):
    """The Review tab holds the two kinds of line that aren't plain
    carrier rows: ones the contractor added, and ones the parser flagged."""

    def setUp(self):
        import pandas as pd

        with open(os.path.join(FIXTURES_DIR, "allstate_5410.txt"), encoding="utf-8") as fh:
            estimate = parse_text(fh.read())
        rows = app._rows_from_estimate(estimate, 20)
        rows.loc[rows.index[1], "Needs Review"] = True
        added = app._manual_row("Extra ridge vent", "Roofing", 12, "LF", 8.0, 20, True, position=1)
        self.rows = pd.concat(
            [rows, pd.DataFrame([added], columns=app._TABLE_COLUMNS)], ignore_index=True
        )

    def test_added_rows_are_the_ones_with_no_carrier_line_behind_them(self):
        added = self.rows[app._added_mask(self.rows)]
        self.assertEqual(len(added), 1)
        self.assertEqual(added.iloc[0]["Description"], "Extra ridge vent")

    def test_flagged_rows_are_the_ones_the_parser_was_unsure_about(self):
        flagged = self.rows[app._flagged_mask(self.rows)]
        self.assertEqual(len(flagged), 1)

    def test_the_scope_tab_shows_carrier_lines_only(self):
        carrier = self.rows[~app._added_mask(self.rows)]
        self.assertEqual(len(carrier), len(self.rows) - 1)
        self.assertNotIn("Extra ridge vent", list(carrier["Description"]))

    def test_pricing_still_covers_every_row_including_the_added_one(self):
        """Splitting the screen must not split the money."""
        priced = app._priced(self.rows[self.rows["Include"].fillna(False)])
        self.assertIn("Your Price", priced.columns)
        self.assertEqual(len(priced), len(self.rows))
        expected = 12 * 8.0 * 1.20
        self.assertAlmostEqual(
            priced[priced["Description"] == "Extra ridge vent"]["Your Price"].iloc[0],
            expected, places=2,
        )

    def test_priced_does_not_write_back_into_the_table_it_was_given(self):
        app._priced(self.rows)
        self.assertNotIn("Your Price", self.rows.columns)

    def test_an_empty_selection_prices_to_an_empty_table(self):
        empty = self.rows.iloc[0:0]
        priced = app._priced(empty)
        self.assertTrue(priced.empty)
        self.assertIn("Your Price", priced.columns)


class ExportNameTest(unittest.TestCase):
    def test_the_default_name_is_business_carrier_claim(self):
        fields = {"insurance_company": "State Farm", "claim_number": "0761262757"}
        self.assertEqual(
            app._export_basename("Acme Roofing", fields),
            "Acme_Roofing_State_Farm_0761262757",
        )

    def test_missing_pieces_are_skipped_rather_than_left_as_gaps(self):
        self.assertEqual(app._export_basename("Acme Roofing", {}), "Acme_Roofing")

    def test_it_carries_no_extension_since_the_contractor_types_over_it(self):
        name = app._export_basename("Acme Roofing", {"claim_number": "123"})
        self.assertFalse(name.endswith("."))
        self.assertNotIn(".csv", name)
        self.assertNotIn(".pdf", name)


class MergeTableEditsTest(unittest.TestCase):
    """The editable tables are the single way lines get added now (the
    '+' row at the bottom). _merge_table_edits() must turn an editor's
    added/deleted/edited rows into the right master-table changes without
    ever touching the rows the current view wasn't showing."""

    @staticmethod
    def _carrier_row(number, description, cost=100.0):
        return {
            "#": str(number), "Include": True, "Trade": "Roofing", "Section": "Roofing",
            "Description": description, "Qty": 1.0, "Unit": "EA", "Unit Cost": cost,
            "Margin %": 20, "Material": True, "Insurance RCV": 100.0,
            "Insurance O&P": 0.0, "Code Cite": False, "Needs Review": False,
            "Review Note": "", "Recoverable Depreciation": 0.0,
        }

    def _master(self):
        import pandas as pd

        return pd.DataFrame(
            [
                self._carrier_row(1, "Carrier A"),
                self._carrier_row(2, "Carrier B"),
                app._manual_row("Added line", "Roofing", 1, "EA", 50.0, 20, True, position=1),
            ],
            columns=app._TABLE_COLUMNS,
        )

    def test_edits_to_shown_rows_are_written_back(self):
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 1]]
        edited = shown.copy()
        edited.loc[0, "Unit Cost"] = 999.0
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertEqual(added, 0)
        self.assertEqual(result.loc[0, "Unit Cost"], 999.0)
        self.assertEqual(result.loc[1, "Unit Cost"], 100.0)  # untouched row
        self.assertEqual(len(result), 3)

    def test_added_row_becomes_a_counter_offer_supplement(self):
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 1]]
        new = pd.DataFrame(
            [{"Description": "Contractor add", "Trade": "Roofing", "Qty": 2.0,
              "Unit": "EA", "Unit Cost": 75.0, "Margin %": 20,
              "Include": True, "Material": True}],
            index=[3],  # Streamlit hands added rows an index past the shown view
        )
        edited = pd.concat([shown, new])
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertEqual(added, 1)
        self.assertEqual(len(result), 4)
        last = result.iloc[-1]
        self.assertEqual(last["#"], "A2")  # next label after the existing A1
        self.assertTrue(pd.isna(last["Insurance RCV"]))  # supplement classification
        self.assertEqual(last["Section"], "Added by you")
        self.assertEqual(last["Description"], "Contractor add")

    def test_added_row_never_clobbers_a_hidden_row_with_the_same_index(self):
        # The editor can hand back an added row whose index collides with a
        # master row the current view was NOT showing -- the merge must
        # treat it as new, not as an edit of the hidden row.
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 1]]  # hides master index 2
        new = pd.DataFrame(
            [{"Description": "Colliding add", "Trade": "Roofing", "Qty": 1.0,
              "Unit": "EA", "Unit Cost": 60.0, "Margin %": 20,
              "Include": True, "Material": True}],
            index=[2],  # collides with the hidden row
        )
        edited = pd.concat([shown, new])
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertEqual(added, 1)
        self.assertEqual(len(result), 4)
        self.assertEqual(result.loc[2, "Description"], "Added line")  # hidden row intact
        self.assertEqual(result.iloc[-1]["Description"], "Colliding add")

    def test_deleting_a_shown_row_drops_only_that_row(self):
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 1, 2]]
        edited = shown.drop(index=[1])
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertEqual(added, 0)
        self.assertNotIn(1, result.index)
        self.assertEqual(list(result["Description"]), ["Carrier A", "Added line"])

    def test_hidden_rows_survive_edits_and_deletes_elsewhere(self):
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 2]]  # a filtered view (carrier A + the added line)
        edited = shown.copy()
        edited.loc[2, "Unit Cost"] = 777.0
        edited = edited.drop(index=[0])  # user deleted carrier A from this view
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertNotIn(0, result.index)  # shown-and-deleted: gone
        self.assertEqual(result.loc[2, "Unit Cost"], 777.0)  # shown-and-edited: updated
        self.assertEqual(result.loc[1, "Description"], "Carrier B")  # never shown: untouched

    def test_blank_added_row_is_skipped(self):
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 1]]
        blank = pd.DataFrame(
            [{"Description": None, "Qty": None, "Unit": None, "Unit Cost": None}],
            index=[9],
        )
        edited = pd.concat([shown, blank])
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertEqual(added, 0)
        self.assertEqual(len(result), 3)

    def test_multiple_added_rows_get_sequential_labels(self):
        import pandas as pd

        master = self._master()
        shown = master.iloc[[0, 1]]
        new_rows = pd.DataFrame(
            [
                {"Description": "Add one", "Trade": "Roofing", "Qty": 1.0, "Unit": "EA",
                 "Unit Cost": 10.0, "Margin %": 20, "Include": True, "Material": True},
                {"Description": "Add two", "Trade": "Painting", "Qty": 2.0, "Unit": "EA",
                 "Unit Cost": 20.0, "Margin %": 20, "Include": True, "Material": True},
            ],
            index=[3, 4],
        )
        edited = pd.concat([shown, new_rows])
        result, added = app._merge_table_edits(master, shown, edited, 20)
        self.assertEqual(added, 2)
        self.assertEqual(list(result.iloc[-2:]["#"]), ["A2", "A3"])
