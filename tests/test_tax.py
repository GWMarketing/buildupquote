"""Unit tests for tax.py -- the contractor's own sales-tax math, kept
entirely separate from anything the carrier printed (see tax.py's module
docstring and the project's "Known parsing problems" doc, problem #4).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tax  # noqa: E402

ROWS = [
    {"line_total": 1000.0, "is_material": True},   # materials
    {"line_total": 500.0, "is_material": False},   # labor
]


class ComputeSalesTaxTest(unittest.TestCase):
    def test_no_tax_rule_is_always_zero(self):
        self.assertEqual(tax.compute_sales_tax(ROWS, tax.NONE, 8.25), 0.0)

    def test_lump_sum_residential_is_always_zero(self):
        # Tax was still paid by the contractor at the supply house -- it's
        # just never itemized back to the client. See tax.py docstring.
        self.assertEqual(tax.compute_sales_tax(ROWS, tax.LUMP_SUM_RESIDENTIAL, 8.25), 0.0)

    def test_separated_residential_taxes_materials_only(self):
        # 1000 * 8.25% = 82.50 -- the 500 labor line is untouched.
        self.assertEqual(tax.compute_sales_tax(ROWS, tax.SEPARATED_RESIDENTIAL, 8.25), 82.50)

    def test_commercial_taxes_everything(self):
        # (1000 + 500) * 8.25% = 123.75
        self.assertEqual(tax.compute_sales_tax(ROWS, tax.COMMERCIAL, 8.25), 123.75)

    def test_missing_is_material_defaults_to_taxable(self):
        # The safer default under a separated contract: undercharging tax
        # is the costlier mistake for a contractor to make.
        rows = [{"line_total": 100.0}]  # no "is_material" key at all
        self.assertEqual(tax.compute_sales_tax(rows, tax.SEPARATED_RESIDENTIAL, 10.0), 10.0)

    def test_zero_rate_is_zero_even_when_itemized(self):
        self.assertEqual(tax.compute_sales_tax(ROWS, tax.SEPARATED_RESIDENTIAL, 0), 0.0)

    def test_every_rule_has_a_label(self):
        for rule in tax.TAX_RULE_OPTIONS:
            self.assertIn(rule, tax.TAX_RULE_LABELS)

    def test_itemizes_tax_is_the_right_subset(self):
        self.assertEqual(tax.ITEMIZES_TAX, {tax.SEPARATED_RESIDENTIAL, tax.COMMERCIAL})


if __name__ == "__main__":
    unittest.main()
