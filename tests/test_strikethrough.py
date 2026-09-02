# Tests for strikethrough detection: a line the preparer crossed out is
# flagged (struck_through), excluded from the quote totals (Include=False
# on the workspace row), and surfaced in warnings -- with a one-click
# re-include via the On checkbox. No false positives on table borders or
# highlight boxes.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scope_parser.models import LineItem
from scope_parser.strikethrough import mark_struck_items, struck_lines_from_pdf, struck_lines_on_page
from scope_parser.pipeline import parse_pdf
import workspace


def _item(number, description, quantity=1.0, unit='EA', unit_price=None):
    return LineItem(number=number, description=description, quantity=quantity,
                    unit=unit, unit_price=unit_price)


class MarkStruckItemsTests(unittest.TestCase):
    def test_struck_line_matches_by_item_number(self):
        items = [_item('16', 'Dumpster, 20 Yard'), _item('2', 'Drip edge')]
        struck = ['16 Dumpster, 20 Yard 1 698.49 EA 712.46']
        marked = mark_struck_items(items, struck)
        self.assertEqual(marked, 1)
        self.assertTrue(items[0].struck_through)
        self.assertFalse(items[1].struck_through)

    def test_struck_line_matches_by_description_overlap_without_number(self):
        items = [_item('', 'Dumpster, 20 Yard')]
        mark_struck_items(items, ['Dumpster, 20 Yard 1 698.49 EA'])
        self.assertTrue(items[0].struck_through)

    def test_unrelated_struck_line_marks_nothing(self):
        items = [_item('1', 'Roof shingles')]
        mark_struck_items(items, ['Thank you for insuring with Liberty Mutual Insurance.'])
        self.assertFalse(items[0].struck_through)

    def test_struck_item_is_flagged_for_review_and_warns(self):
        items = [_item('16', 'Dumpster, 20 Yard')]
        warnings = []
        mark_struck_items(items, ['16 Dumpster, 20 Yard'], warnings=warnings)
        self.assertTrue(items[0].needs_review)
        self.assertIn('struck through', items[0].review_reason)
        self.assertTrue(any('struck' in w for w in warnings))


class ParsePdfStrikeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = os.path.join(tempfile.gettempdir(), 'strike_test.pdf')
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(cls.pdf)
        c.setFont('Helvetica', 10)
        c.drawString(50, 720, '16 Dumpster, 20 Yard 1 698.49 EA 0.00 13.97 712.46 0.00 712.46')
        c.line(50, 723, 420, 723)
        c.drawString(50, 700, '2 Install new drip edge 20.00 LF 1.50 30.00 0.00')
        c.drawString(50, 680, 'Total: 742.46')
        c.save()

    def test_struck_line_is_flagged_and_excluded(self):
        est = parse_pdf(self.pdf)
        struck = [li for li in est.line_items if li.struck_through]
        self.assertEqual(len(struck), 1)
        self.assertIn('Dumpster', struck[0].description)
        rows = workspace._rows_from_estimate(est, 20)
        by_desc = {str(r['Description']): r for _, r in rows.iterrows()}
        dump = next(v for k, v in by_desc.items() if 'Dumpster' in k)
        self.assertFalse(dump['Include'])
        self.assertTrue(dump['Struck Through'])
        drip = next(v for k, v in by_desc.items() if 'drip edge' in k)
        self.assertTrue(drip['Include'])

    def test_struck_lines_are_reported(self):
        struck = struck_lines_from_pdf(self.pdf)
        self.assertTrue(any('Dumpster' in s for s in struck))
        self.assertFalse(any('drip edge' in s for s in struck))


if __name__ == '__main__':
    unittest.main()


class PartialStrikeTests(unittest.TestCase):
    def test_figures_only_strike_matches_by_quantity_unit_price(self):
        items = [_item('17', 'Minimum Charge, Glass/Glazing', quantity=1.0, unit='LS', unit_price=324.36)]
        marked = mark_struck_items(items, ['1 324.36 LS 0.00 293.05'])
        self.assertEqual(marked, 1)
        self.assertTrue(items[0].struck_through)

    def test_figures_only_strike_does_not_match_wrong_price(self):
        items = [_item('17', 'Minimum Charge, Glass/Glazing', quantity=1.0, unit='LS', unit_price=324.36)]
        marked = mark_struck_items(items, ['1 999.99 LS 0.00 293.05'])
        self.assertEqual(marked, 0)
        self.assertFalse(items[0].struck_through)


class GeometryRobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = os.path.join(tempfile.gettempdir(), 'strike_robust_test.pdf')
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(cls.pdf)
        c.setFont('Helvetica', 10)
        # Row A: struck with a THICK BAR through the middle -- a real strike.
        c.drawString(50, 720, '1 Replace roof shingles 10.00 SQ 2.00 20.00')
        c.rect(50, 721, 300, 3.5, stroke=0, fill=1)
        # Row B: a full-row highlight box -- must NOT count as a strike.
        c.drawString(50, 690, '2 Clean gutters 5.00 LF 1.00 5.00')
        c.rect(50, 684, 300, 13, stroke=0, fill=1)
        # Row C: an underline -- must NOT count as a strike.
        c.drawString(50, 660, '3 Paint trim 20.00 LF 1.50 30.00')
        c.line(50, 655, 300, 655)
        # Row D: a table border at the top edge of the row -- must NOT.
        c.drawString(50, 630, '4 Haul debris 2.00 EA 50.00 100.00')
        c.line(50, 639, 300, 639)
        c.save()

    def test_only_the_thick_bar_row_is_struck(self):
        import pdfplumber
        with pdfplumber.open(self.pdf) as pdf:
            struck = set(struck_lines_on_page(pdf.pages[0]))
        self.assertTrue(any('shingles' in s for s in struck))
        self.assertFalse(any('gutters' in s for s in struck))
        self.assertFalse(any('trim' in s for s in struck))
        self.assertFalse(any('debris' in s for s in struck))
