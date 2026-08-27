"""Unit tests for the app's pure logic (pricing.py, trades.py).

These deliberately don't import any framework -- the math and
classification logic lives in plain functions (pricing.py, trades.py,
workspace.py) so it can be unit-tested here, and the same functions are
exercised for real by the FastAPI deployment (fastapi_app.py).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricing import compute_line_total  # noqa: E402
from trades import guess_trade, TRADE_OPTIONS  # noqa: E402


class ComputeLineTotalTest(unittest.TestCase):
    def test_basic_calc(self):
        self.assertEqual(compute_line_total(10, 5, 0), 50.0)

    def test_margin_is_applied_on_top(self):
        self.assertEqual(compute_line_total(10, 5, 20), 60.0)

    def test_hundred_percent_margin_doubles_it(self):
        self.assertEqual(compute_line_total(10, 5, 100), 100.0)

    def test_missing_values_treated_as_zero_not_an_error(self):
        self.assertEqual(compute_line_total(None, 5, 20), 0.0)
        self.assertEqual(compute_line_total(10, None, 20), 0.0)
        self.assertEqual(compute_line_total(10, 5, None), 50.0)

    def test_credit_line_items_stay_negative(self):
        # e.g. a carrier "invoice" reimbursement line with a negative qty
        self.assertEqual(compute_line_total(-1, 300, 0), -300.0)


class GuessTradeTest(unittest.TestCase):
    def test_roofing_keywords(self):
        self.assertEqual(guess_trade("Remove Laminated - comp. shingle rfg. - w/ felt"), "Roofing")
        self.assertEqual(guess_trade("R&R Drip edge"), "Roofing")

    def test_specific_trade_wins_over_generic_remove(self):
        # "Remove" alone would fall through to Demolition -- a more
        # specific keyword earlier in the description should win instead.
        self.assertEqual(guess_trade("Remove Additional charge for steep roof"), "Roofing")

    def test_generic_demolition_fallback(self):
        self.assertEqual(guess_trade("Tear out wet drywall, cleanup, bag - Cat 3"), "Drywall")
        self.assertEqual(guess_trade("Haul debris - per pickup truck load"), "Tree/Debris Removal")

    def test_painting(self):
        self.assertEqual(guess_trade("Paint the ceiling - one coat"), "Painting")

    def test_unknown_description_falls_back_to_other(self):
        self.assertEqual(guess_trade("Some completely novel scope of work"), "Other")

    def test_empty_description(self):
        self.assertEqual(guess_trade(""), "Other")
        self.assertEqual(guess_trade(None), "Other")

    def test_every_guess_is_a_valid_option(self):
        samples = [
            "Remove Laminated - comp. shingle", "R&R Gutter - aluminum",
            "R&R Fascia - fiber cement", "R&R Brick veneer", "Window - Detach & reset",
            "Paint the walls - two coats", "1/2\" drywall - hung, taped",
            "R&R Carpet", "Batt insulation - 10\"", "Service Panel",
            "R&R Ductwork - flexible", "R&R Wood fence", "Castle Tree Surgery",
            "Final cleaning - construction", "Contents - move out then reset",
            "General Laborer - per hour", "Something nobody has ever written",
        ]
        for desc in samples:
            self.assertIn(guess_trade(desc), TRADE_OPTIONS)


if __name__ == "__main__":
    unittest.main()
