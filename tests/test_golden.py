"""The regression lock. See tests/golden_support.py for why it exists.

These tests fail loudly whenever the parser's output on a real carrier
fixture changes in ANY way -- a number, a description, a note, a section
name, a warning, an ordering. That is the point: they are the proof that
adding support for a new estimating program did not quietly damage the
Xactimate path that already works.
"""
import json
import os
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
# Project root first (so `scope_parser` imports), then this directory (so
# `golden_support` imports) -- deliberately NOT a `tests` package, so the
# existing `python3 -m unittest discover -s tests` command keeps working
# exactly as the README describes it.
sys.path.insert(0, os.path.join(_TESTS_DIR, ".."))
sys.path.insert(0, _TESTS_DIR)

import golden_support as gs  # noqa: E402


class GoldenSnapshotTests(unittest.TestCase):
    maxDiff = None

    def _check(self, name):
        path = gs.golden_path(name)
        self.assertTrue(
            os.path.exists(path),
            f"missing golden snapshot {path} -- run `python3 tools/refresh_golden.py`",
        )
        with open(path, encoding="utf-8") as fh:
            expected = json.load(fh)
        actual = gs.snapshot(gs.parse_fixture(name))

        # Compare the cheap summary first so a failure message leads with
        # the shape of the change rather than a thousand-line dict diff.
        self.assertEqual(
            len(expected["line_items"]), len(actual["line_items"]),
            f"{name}: line-item COUNT changed",
        )
        self.assertEqual(
            expected["warnings"], actual["warnings"],
            f"{name}: parser warnings changed",
        )
        for i, (exp_item, act_item) in enumerate(zip(expected["line_items"], actual["line_items"])):
            self.assertEqual(exp_item, act_item, f"{name}: line item #{i} changed")
        self.assertEqual(expected, actual, f"{name}: parsed output changed")

    def test_allstate_5410_unchanged(self):
        self._check("allstate_5410")

    def test_travelers_erin_unchanged(self):
        self._check("travelers_erin")

    def test_appraiser_williams1_unchanged(self):
        self._check("appraiser_williams1")

    def test_contractor_doyle_unchanged(self):
        self._check("contractor_doyle")

    def test_symbility_libertymutual_unchanged(self):
        self._check("symbility_libertymutual")


class GoldenCoverageTests(unittest.TestCase):
    """Guards against the lock silently covering less than it claims to."""

    def test_every_fixture_has_a_snapshot(self):
        for name in gs.FIXTURE_NAMES:
            self.assertTrue(
                os.path.exists(gs.golden_path(name)),
                f"fixture '{name}' has no golden snapshot",
            )

    def test_snapshots_are_not_empty(self):
        for name in gs.FIXTURE_NAMES:
            with open(gs.golden_path(name), encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertGreater(
                len(data["line_items"]), 0,
                f"snapshot for '{name}' has no line items -- it would lock in a broken parse",
            )

    def test_snapshot_captures_the_fields_that_matter(self):
        """A snapshot that dropped these keys would pass while proving nothing."""
        with open(gs.golden_path("allstate_5410"), encoding="utf-8") as fh:
            data = json.load(fh)
        for key in ("metadata", "line_items", "measurements", "section_totals",
                    "warnings", "claim_flags"):
            self.assertIn(key, data)
        item = data["line_items"][0]
        for key in ("number", "description", "quantity", "unit", "unit_price",
                    "rcv", "section", "notes", "needs_review"):
            self.assertIn(key, item)


if __name__ == "__main__":
    unittest.main()
