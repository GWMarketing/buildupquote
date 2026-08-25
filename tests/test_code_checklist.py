"""Tests for the Texas code checklist (code_checklist.py) -- pure data
and matching logic, no Streamlit needed."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from code_checklist import (  # noqa: E402
    CATEGORY_ORDER,
    CODE_ITEMS,
    SECTION_LABEL,
    STATUTORY_CONTEXT,
    check_coverage,
    labor_line,
    material_description,
)
from trades import TRADE_OPTIONS  # noqa: E402


class CodeItemShapeTests(unittest.TestCase):
    """Every item has to be well-formed, or the "Add to scope" button
    either breaks (an invalid Trade) or silently never matches (no
    keywords)."""

    def test_every_default_trade_is_a_real_trade_option(self):
        for item in CODE_ITEMS:
            self.assertIn(
                item.default_trade, TRADE_OPTIONS,
                f"{item.id}: {item.default_trade!r} isn't in trades.TRADE_OPTIONS",
            )

    def test_every_id_is_unique(self):
        ids = [item.id for item in CODE_ITEMS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_item_has_at_least_one_keyword(self):
        for item in CODE_ITEMS:
            self.assertTrue(item.keywords, f"{item.id} has no keywords -- can never match")

    def test_every_item_is_in_a_known_category_in_order(self):
        for item in CODE_ITEMS:
            self.assertIn(item.category, CATEGORY_ORDER)

    def test_every_item_has_a_citation_title_and_requirement(self):
        for item in CODE_ITEMS:
            self.assertTrue(item.citation.strip())
            self.assertTrue(item.title.strip())
            self.assertTrue(item.requirement.strip())

    def test_osha_items_are_included_and_addable(self):
        """Glenn's explicit call (2026-08-25): OSHA gets its own group,
        with the same Add-to-scope treatment as every other category."""
        osha_items = [i for i in CODE_ITEMS if i.category == "Workplace Safety (OSHA)"]
        self.assertEqual(len(osha_items), 3)

    def test_all_four_categories_from_the_source_document_are_present(self):
        present = {item.category for item in CODE_ITEMS}
        self.assertEqual(present, set(CATEGORY_ORDER))


class StatutoryContextTests(unittest.TestCase):
    """Section 1 of the source doc -- reference-only, no keywords/trade,
    never an "Add to scope" item."""

    def test_every_entry_has_citation_title_and_detail(self):
        self.assertTrue(STATUTORY_CONTEXT)
        for entry in STATUTORY_CONTEXT:
            self.assertTrue(entry["citation"].strip())
            self.assertTrue(entry["title"].strip())
            self.assertTrue(entry["detail"].strip())

    def test_the_deductible_waiver_law_is_not_duplicated_here(self):
        """That law is already enforced elsewhere (TX_DEDUCTIBLE_NOTICE on
        every proposal) -- repeating it in this checklist would be a
        second, disconnected copy of the same legal requirement."""
        text = " ".join(e["citation"] + e["title"] + e["detail"] for e in STATUTORY_CONTEXT)
        self.assertNotIn("707.002", text)


class CheckCoverageTests(unittest.TestCase):
    def test_an_empty_scope_matches_nothing(self):
        result = check_coverage([])
        self.assertTrue(result)  # every item id present
        self.assertFalse(any(result.values()))

    def test_a_matching_description_is_found(self):
        result = check_coverage(["Remove and replace drip edge, aluminum"])
        self.assertTrue(result["irc_r905_2_8_5_drip_edge"])

    def test_matching_is_case_insensitive(self):
        result = check_coverage(["INSTALL DRIP EDGE"])
        self.assertTrue(result["irc_r905_2_8_5_drip_edge"])

    def test_a_description_that_does_not_mention_it_is_not_found(self):
        result = check_coverage(["Remove and replace gutters, 5 inch"])
        self.assertFalse(result["irc_r905_2_8_5_drip_edge"])

    def test_multiple_items_can_match_across_different_lines(self):
        result = check_coverage([
            "Install GFCI outlet, exterior",
            "Ice & water shield, valleys",
            "Paint room, two coats",
        ])
        self.assertTrue(result["nec_210_8_gfci"])
        self.assertTrue(result["irc_r905_1_2_ice_barrier"])
        self.assertFalse(result["nec_210_12_afci"])

    def test_blank_and_none_descriptions_do_not_crash(self):
        result = check_coverage(["", None, "Drip edge"])
        self.assertTrue(result["irc_r905_2_8_5_drip_edge"])


class MaterialDescriptionTests(unittest.TestCase):
    def test_it_is_the_citation_and_title(self):
        item = CODE_ITEMS[0]
        self.assertEqual(material_description(item), f"{item.citation} — {item.title}")


class LaborLineTests(unittest.TestCase):
    """Glenn, 2026-08-25: "the labor for each one should be on a
    separate line item... like 8 workers, 30 hours, $10 hour." There's
    no separate worker-count column, so this folds crew size into
    Quantity as total crew-hours -- Qty x Unit Cost still comes out to
    the real labor cost."""

    def test_workers_times_hours_becomes_the_quantity(self):
        item = CODE_ITEMS[0]
        description, total_hours = labor_line(item, workers=8, hours=30, rate=10)
        self.assertEqual(total_hours, 240.0)
        self.assertIn("8 workers", description)
        self.assertIn("30 hrs", description)
        self.assertIn("$10.00/hr", description)
        self.assertIn(item.citation, description)

    def test_the_math_still_comes_out_to_the_real_labor_cost(self):
        """8 workers x 30 hrs x $10/hr should price out to $2,400 once
        Qty x Unit Cost runs, even though there's no "workers" column."""
        item = CODE_ITEMS[0]
        _, total_hours = labor_line(item, workers=8, hours=30, rate=10)
        rate = 10
        self.assertEqual(round(total_hours * rate, 2), 2400.00)

    def test_a_single_worker_is_not_pluralised(self):
        _, _ = labor_line(CODE_ITEMS[0], workers=1, hours=4, rate=45)
        description, _ = labor_line(CODE_ITEMS[0], workers=1, hours=4, rate=45)
        self.assertIn("1 worker ", description)
        self.assertNotIn("1 workers", description)

    def test_a_zero_or_blank_input_is_skipped_not_added_as_a_free_line(self):
        for workers, hours, rate in [(0, 30, 10), (8, 0, 10), (8, 30, 0), ("", 30, 10)]:
            description, total_hours = labor_line(CODE_ITEMS[0], workers, hours, rate)
            self.assertIsNone(description)
            self.assertIsNone(total_hours)

    def test_a_non_numeric_input_does_not_crash(self):
        description, total_hours = labor_line(CODE_ITEMS[0], "abc", 30, 10)
        self.assertIsNone(description)
        self.assertIsNone(total_hours)


class SectionLabelTests(unittest.TestCase):
    def test_it_is_distinct_from_added_by_you(self):
        """This is what keeps a code-required row out of the Scope tab
        and out of the "Added by you" list in Review -- see app.py."""
        self.assertNotEqual(SECTION_LABEL, "Added by you")


if __name__ == "__main__":
    unittest.main()
