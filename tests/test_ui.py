"""Tests for the parts of the workspace that make decisions.

ui.py is mostly drawing code, which needs a real browser to judge. These
cover the two things in it that are logic rather than looks -- what a
search box matches, and what a download ends up being called -- plus the
row numbering that lets a contractor check a line against the PDF.
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ui  # noqa: E402


def frame():
    return pd.DataFrame(
        [
            {"#": "1", "Description": "Remove Carpet", "Trade": "Flooring", "Qty": 172.5, "Include": True},
            {"#": "2", "Description": "R&R Baseboard - 3 1/4\" hardwood", "Trade": "Carpentry", "Qty": 48.0, "Include": True},
            {"#": "3", "Description": "Paint the walls - two coats", "Trade": "Painting", "Qty": 381.67, "Include": False},
            {"#": "A1", "Description": "Extra ridge vent", "Trade": "Roofing", "Qty": 12.0, "Include": True},
        ]
    )


class SearchTests(unittest.TestCase):
    def test_an_empty_query_changes_nothing(self):
        rows = frame()
        for query in ("", "   ", None):
            self.assertEqual(len(ui.filter_rows(rows, query)), 4)

    def test_it_matches_any_column_not_just_the_description(self):
        self.assertEqual(list(ui.filter_rows(frame(), "roofing")["#"]), ["A1"])

    def test_it_is_case_insensitive(self):
        self.assertEqual(list(ui.filter_rows(frame(), "CARPET")["#"]), ["1"])

    def test_numbers_are_searchable_too(self):
        """Typing a quantity off the PDF should find its line."""
        self.assertEqual(list(ui.filter_rows(frame(), "381.67")["#"]), ["3"])

    def test_every_word_must_appear_somewhere_in_the_row(self):
        """Word order shouldn't matter -- "walls paint" finds "Paint the
        walls" the same as "paint walls" does."""
        self.assertEqual(list(ui.filter_rows(frame(), "walls paint")["#"]), ["3"])
        self.assertEqual(list(ui.filter_rows(frame(), "paint carpet")["#"]), [])

    def test_a_miss_returns_an_empty_frame_not_an_error(self):
        result = ui.filter_rows(frame(), "nothing here")
        self.assertTrue(result.empty)
        self.assertEqual(list(result.columns), list(frame().columns))

    def test_the_original_index_survives(self):
        """Edits are written back to the master table by index, so a
        filtered view that renumbered its rows would scatter them."""
        result = ui.filter_rows(frame(), "paint")
        self.assertEqual(list(result.index), [2])

    def test_an_empty_table_is_handled(self):
        empty = pd.DataFrame(columns=["#", "Description"])
        self.assertTrue(ui.filter_rows(empty, "anything").empty)

    def test_it_can_be_limited_to_named_columns(self):
        self.assertEqual(list(ui.filter_rows(frame(), "carpet", columns=["Trade"])["#"]), [])


class FilenameTests(unittest.TestCase):
    def test_a_typed_name_is_used_as_typed(self):
        self.assertEqual(
            ui.sanitize_filename("Doyle kitchen rebuild", "pdf", "proposal.pdf"),
            "Doyle kitchen rebuild.pdf",
        )

    def test_characters_the_operating_system_refuses_are_removed(self):
        self.assertEqual(
            ui.sanitize_filename('Doyle: kitchen/rebuild? v2', "pdf", "proposal.pdf"),
            "Doyle kitchenrebuild v2.pdf",
        )

    def test_a_typed_extension_is_not_doubled(self):
        self.assertEqual(ui.sanitize_filename("scope.csv", "csv", "x.csv"), "scope.csv")
        self.assertEqual(ui.sanitize_filename("proposal.PDF", "pdf", "x.pdf"), "proposal.pdf")

    def test_an_empty_name_falls_back_rather_than_making_a_dotfile(self):
        for name in ("", "   ", "...", None, "///"):
            self.assertEqual(ui.sanitize_filename(name, "pdf", "proposal.pdf"), "proposal.pdf")

    def test_a_very_long_name_is_trimmed(self):
        result = ui.sanitize_filename("A" * 400, "pdf", "proposal.pdf")
        self.assertTrue(result.endswith(".pdf"))
        self.assertLessEqual(len(result), 130)

    def test_an_auto_built_name_joins_words_with_the_given_separator(self):
        """app._slugify passes separator="_" (it joins several separate
        pieces -- business, carrier, claim number -- into one name), while
        a name the contractor typed themselves stays space-separated.
        Ampersands and the like are handled by _slugify's stricter
        pre-filter, not by this function -- this one only strips what the
        OS refuses."""
        result = ui.sanitize_filename("Acme Roofing   State Farm", "", "", separator="_")
        self.assertEqual(result, "Acme_Roofing_State_Farm")
        self.assertEqual(
            ui.sanitize_filename("Doyle's kitchen", "pdf", "proposal.pdf"),
            "Doyle's kitchen.pdf",
        )

    def test_an_empty_extension_returns_the_cleaned_name_itself(self):
        """The default download name has no extension -- the contractor
        types over it before either file is saved (see app._export_basename)."""
        self.assertEqual(
            ui.sanitize_filename("Acme Roofing   State Farm", "", "", separator="_"),
            "Acme_Roofing_State_Farm",
        )

    def test_empty_extension_still_collapses_and_strips(self):
        self.assertEqual(
            ui.sanitize_filename("  Acme Roofing  ", "", "", separator="_"),
            "Acme_Roofing",
        )
        # All-whitespace falls back even with no extension to append.
        self.assertEqual(ui.sanitize_filename("   ", "", "buildupquote"), "buildupquote")


class RowLabelTests(unittest.TestCase):
    def test_a_carrier_line_keeps_its_printed_number(self):
        """This is the number a contractor reads off the PDF."""
        self.assertEqual(ui.row_label("14", 3), "14")
        self.assertEqual(ui.row_label("10a", 7), "10a")

    def test_a_line_with_no_carrier_number_gets_a_position(self):
        self.assertEqual(ui.row_label("", 5), "A5")
        self.assertEqual(ui.row_label(None, 1), "A1")

    def test_lines_you_added_can_never_be_mistaken_for_carrier_lines(self):
        self.assertEqual(ui.row_label("3", 2, added=True), "A2")

    def test_a_custom_prefix_is_used_instead_of_a(self):
        """A code-required addition uses "L" so it reads apart from a
        line the contractor chose to add on their own."""
        self.assertEqual(ui.row_label(None, 4, added=True, prefix="L"), "L4")


class PaletteTests(unittest.TestCase):
    def test_every_tone_has_a_matching_background(self):
        """pill() looks up "<tone>" and "<tone>_bg"; a missing pair would
        be a KeyError on screen rather than a wrong colour."""
        for tone in ("good", "warn", "bad", "info", "added"):
            self.assertIn(tone, ui.PALETTE)
            self.assertIn(tone + "_bg", ui.PALETTE)

    def test_an_unknown_tone_falls_back_instead_of_crashing(self):
        self.assertIn("bq-pill", ui.pill("hello", "chartreuse"))

    def test_the_stylesheet_is_one_self_contained_block(self):
        self.assertTrue(ui.CSS.strip().startswith("<style>"))
        self.assertTrue(ui.CSS.strip().endswith("</style>"))


if __name__ == "__main__":
    unittest.main()
