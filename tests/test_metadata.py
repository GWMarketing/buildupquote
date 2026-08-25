"""Tests for the PDF *container* metadata feature -- the file's own
hidden "who made this" info (Producer/Creator/CreationDate/Title in the
/Info dictionary), as opposed to anything printed on a page. This is
what macOS "Get Info" or Adobe's Document Properties show, and it's
where Xactimate stamps its own name and exact version straight into the
file -- a far more reliable "which program wrote this" signal than
guessing from column headers, confirmed against Glenn's real PDFs
(Travelers and Williams1 both carry `Creator: Xactimate 24.x.x.x.x`).
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scope_parser import parse_pdf  # noqa: E402
from scope_parser.metadata import (  # noqa: E402
    fields_from_pdf_info,
    parse_creator,
    parse_pdf_date,
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class ParseCreatorTest(unittest.TestCase):
    def test_program_with_trailing_version(self):
        self.assertEqual(parse_creator("Xactimate 24.4.1001.1"), ("Xactimate", "24.4.1001.1"))

    def test_multi_word_program_name_with_version(self):
        self.assertEqual(
            parse_creator("Adobe Acrobat Pro DC 21.7.20099"),
            ("Adobe Acrobat Pro DC", "21.7.20099"),
        )

    def test_no_trailing_version_returns_whole_string_as_program(self):
        self.assertEqual(parse_creator("anonymous"), ("anonymous", None))
        self.assertEqual(
            parse_creator("ReportLab PDF Library - (opensource)"),
            ("ReportLab PDF Library - (opensource)", None),
        )

    def test_none_and_empty_are_handled(self):
        self.assertEqual(parse_creator(None), (None, None))
        self.assertEqual(parse_creator(""), (None, None))
        self.assertEqual(parse_creator("   "), (None, None))


class ParsePdfDateTest(unittest.TestCase):
    def test_standard_pdf_date_format(self):
        self.assertEqual(parse_pdf_date("D:20240722183217-05'00'"), "2024-07-22 18:32:17")

    def test_utc_offset_variant(self):
        self.assertEqual(parse_pdf_date("D:20260823164349+00'00'"), "2026-08-23 16:43:49")

    def test_unrecognized_format_returned_unchanged_not_dropped(self):
        self.assertEqual(parse_pdf_date("not a pdf date"), "not a pdf date")

    def test_none_and_empty_return_none(self):
        self.assertIsNone(parse_pdf_date(None))
        self.assertIsNone(parse_pdf_date(""))


class FieldsFromPdfInfoTest(unittest.TestCase):
    def test_real_xactimate_info_dict(self):
        # The exact shape pdfplumber returned for Glenn's real Travelers PDF.
        info = {
            "CreationDate": "D:20240722183217-05'00'",
            "Producer": "iTextSharp 4.1.6 by 1T3XT",
            "Title": "Travelers - Insured Copy",
            "ModDate": "D:20240722183217-05'00'",
            "Creator": "Xactimate 24.4.1001.1",
        }
        fields = fields_from_pdf_info(info)
        self.assertEqual(fields["source_program"], "Xactimate")
        self.assertEqual(fields["source_program_version"], "24.4.1001.1")
        self.assertEqual(fields["pdf_created_at"], "2024-07-22 18:32:17")
        self.assertEqual(fields["pdf_title"], "Travelers - Insured Copy")

    def test_generic_untitled_title_is_dropped_not_shown_as_a_real_title(self):
        fields = fields_from_pdf_info({"Title": "untitled", "Creator": "anonymous"})
        self.assertNotIn("pdf_title", fields)

    def test_empty_info_dict_produces_no_fields(self):
        self.assertEqual(fields_from_pdf_info({}), {})


class ParsePdfSurfacesContainerMetadataTest(unittest.TestCase):
    """End-to-end: parse_pdf() on a real (if synthetic) PDF file actually
    picks up the file's own Creator/CreationDate/Title, merged into the
    same metadata.fields dict as everything extract_metadata() finds in
    the page text -- not a separate, easy-to-forget-to-check structure."""

    @classmethod
    def setUpClass(cls):
        from reportlab.pdfgen import canvas

        cls.pdf_bytes = io.BytesIO()
        c = canvas.Canvas(cls.pdf_bytes)
        c.setCreator("Xactimate 24.4.1001.1")
        c.setTitle("Sample Estimate")
        c.drawString(72, 720, "Insured: TEST INSURED")
        c.showPage()
        c.save()
        cls.pdf_bytes.seek(0)

    def test_source_program_and_version_come_from_the_real_file(self):
        estimate = parse_pdf(self.pdf_bytes)
        self.assertEqual(estimate.metadata.fields.get("source_program"), "Xactimate")
        self.assertEqual(estimate.metadata.fields.get("source_program_version"), "24.4.1001.1")

    def test_title_comes_through_too(self):
        self.pdf_bytes.seek(0)
        estimate = parse_pdf(self.pdf_bytes)
        self.assertEqual(estimate.metadata.fields.get("pdf_title"), "Sample Estimate")

    def test_page_text_fields_and_pdf_info_fields_coexist(self):
        # Confirms merging PDF-info fields never clobbers a real field
        # already found in the page text itself (e.g. "Insured:").
        self.pdf_bytes.seek(0)
        estimate = parse_pdf(self.pdf_bytes)
        self.assertEqual(estimate.metadata.fields.get("insured_name"), "TEST INSURED")
        self.assertEqual(estimate.metadata.fields.get("source_program"), "Xactimate")

    def test_parse_text_alone_never_has_container_metadata(self):
        # There is no file behind parse_text() -- this must not be
        # silently guessed or left over from a previous parse_pdf() call.
        from scope_parser import parse_text

        estimate = parse_text("Insured: TEST INSURED")
        self.assertNotIn("source_program", estimate.metadata.fields)

    def test_synthetic_sample_fixture_surfaces_its_real_reportlab_metadata(self):
        # tests/fixtures/synthetic_sample.pdf wasn't built with Xactimate
        # in mind at all -- confirms this doesn't only work when the
        # answer happens to be "Xactimate".
        estimate = parse_pdf(os.path.join(FIXTURES_DIR, "synthetic_sample.pdf"))
        self.assertEqual(estimate.metadata.fields.get("source_program"), "anonymous")
        self.assertIn("pdf_created_at", estimate.metadata.fields)


if __name__ == "__main__":
    unittest.main()
