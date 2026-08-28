"""Unit tests for the contact import parsers (lead text, vCard via vobject,
CSV). These are pure functions -- no database involved."""
import unittest

from app.services import contact_import


class LeadTextTest(unittest.TestCase):
    def test_labeled_name_and_address_with_uk_phone(self):
        lead = contact_import.parse_lead_text(
            "Name: Jane Doe\n"
            "Tel: +44 7700 900123\n"
            "jane@example.com\n"
            "Site: 123 High St, Manchester\n"
        )
        self.assertEqual(lead["name"], "Jane Doe")
        self.assertEqual(lead["email"], "jane@example.com")
        self.assertEqual(lead["phone"], "+44 7700 900123")
        self.assertEqual(lead["site_address"], "123 High St, Manchester")

    def test_first_line_name_and_us_phone(self):
        lead = contact_import.parse_lead_text(
            "Bob Smith\n"
            "bob@example.com\n"
            "(555) 010-1234\n"
            "456 Oak Ave\n"
        )
        self.assertEqual(lead["name"], "Bob Smith")
        self.assertEqual(lead["email"], "bob@example.com")
        self.assertEqual(lead["phone"], "(555) 010-1234")
        self.assertEqual(lead["site_address"], "456 Oak Ave")

    def test_uk_phone_variants_extract(self):
        for raw in ("+44 7700 900123", "07700 900123", "+447700900123"):
            lead = contact_import.parse_lead_text(f"Call {raw} today")
            self.assertEqual(lead["phone"], raw, raw)

    def test_first_line_is_used_as_name_fallback(self):
        lead = contact_import.parse_lead_text("just some words")
        self.assertEqual(lead["name"], "just some words")
        self.assertIsNone(lead["email"])
        self.assertIsNone(lead["phone"])
        self.assertIsNone(lead["site_address"])

    def test_empty_text_falls_back_to_new_lead(self):
        lead = contact_import.parse_lead_text("")
        self.assertEqual(lead["name"], "New Lead")
        self.assertIsNone(lead["email"])
        self.assertIsNone(lead["phone"])
        self.assertIsNone(lead["site_address"])

    def test_quick_text_parses_multiple_blocks(self):
        text = ("Name: Jane Doe\n+44 7700 900123\njane@example.com\n\n"
                "Bob Smith\nbob@example.com\n")
        contacts = contact_import.parse_quick_text(text)
        self.assertEqual(len(contacts), 2)
        self.assertEqual(contacts[0]["name"], "Jane Doe")
        self.assertEqual(contacts[0]["phone"], "+44 7700 900123")
        self.assertEqual(contacts[1]["name"], "Bob Smith")
        self.assertEqual(contacts[1]["email"], "bob@example.com")


class VCardTest(unittest.TestCase):
    def test_vobject_parses_structured_vcard(self):
        vcf = (
            "BEGIN:VCARD\nVERSION:3.0\nFN:Jane Doe\n"
            "EMAIL;TYPE=INTERNET:jane@example.com\nTEL;TYPE=CELL:+44 7700 900123\n"
            "ADR;TYPE=HOME:;;123 Main St;Anytown;CA;90210\nEND:VCARD\n"
        )
        contacts = contact_import.parse_vcard(vcf)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Jane Doe")
        self.assertEqual(contacts[0]["email"], "jane@example.com")
        self.assertEqual(contacts[0]["phone"], "+44 7700 900123")
        # vobject preserves the structured ADR parts incl. region.
        self.assertEqual(contacts[0]["site_address"], "123 Main St, Anytown, CA, 90210")

    def test_vobject_picks_first_of_multiple_emails(self):
        vcf = (
            "BEGIN:VCARD\nVERSION:3.0\nFN:Bob Smith\n"
            "EMAIL;TYPE=WORK:bob@work.example.com\nEMAIL;TYPE=HOME:bob@home.example.com\n"
            "END:VCARD\n"
        )
        contacts = contact_import.parse_vcard(vcf)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["email"], "bob@work.example.com")


class CsvTest(unittest.TestCase):
    def test_first_last_name_and_email_address_headers(self):
        csv_text = (
            "First Name,Last Name,Email Address,Mobile,Street\n"
            "Jane,Doe,jane@example.com,07700 900123,1 Market St\n"
        )
        contacts = contact_import.parse_csv(csv_text)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Jane Doe")
        self.assertEqual(contacts[0]["email"], "jane@example.com")
        self.assertEqual(contacts[0]["phone"], "07700 900123")
        self.assertEqual(contacts[0]["site_address"], "1 Market St")

    def test_single_name_column_still_works(self):
        csv_text = "name,email,phone,site_address\nAcme,acme@x.com,555-010-9999,9 High St\n"
        contacts = contact_import.parse_csv(csv_text)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Acme")


class PhoneNormalizeTest(unittest.TestCase):
    def test_uk_and_us_variants_dedupe(self):
        self.assertEqual(contact_import.normalize_phone("+44 7700 900123"), "7700900123")
        self.assertEqual(contact_import.normalize_phone("07700 900123"), "7700900123")
        self.assertEqual(contact_import.normalize_phone("(555) 010-1234"), "5550101234")


class ContactServiceTest(unittest.TestCase):
    """The public contact_service facade (the 1-Click Sync Hub's contract)
    must expose the same parsing behaviour under its stable names."""

    def test_parse_lead_text_via_service(self):
        from app.services import contact_service

        lead = contact_service.parse_lead_text(
            "Name: Jane Doe\nTel: +44 7700 900123\njane@example.com\nSite: 123 High St, Manchester\n"
        )
        self.assertEqual(lead["name"], "Jane Doe")
        self.assertEqual(lead["email"], "jane@example.com")
        self.assertEqual(lead["phone"], "+44 7700 900123")
        self.assertEqual(lead["site_address"], "123 High St, Manchester")

    def test_parse_vcard_data_via_service(self):
        from app.services import contact_service

        vcf = "BEGIN:VCARD\nVERSION:3.0\nFN:Bob Smith\nEMAIL;TYPE=WORK:bob@smith.co.uk\nTEL;TYPE=CELL:07700 900456\nEND:VCARD\n"
        contacts = contact_service.parse_vcard_data(vcf)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Bob Smith")
        self.assertEqual(contacts[0]["email"], "bob@smith.co.uk")
        self.assertEqual(contacts[0]["phone"], "07700 900456")

    def test_parse_csv_contacts_via_service(self):
        from app.services import contact_service

        csv_text = "First Name,Last Name,Email Address,Mobile,Street\nAlice,Jones,alice@example.com,07999 123456,9 Market St\n"
        contacts = contact_service.parse_csv_contacts(csv_text)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Alice Jones")
        self.assertEqual(contacts[0]["email"], "alice@example.com")
        self.assertEqual(contacts[0]["phone"], "07999 123456")
        self.assertEqual(contacts[0]["site_address"], "9 Market St")

    def test_parse_quick_text_via_service(self):
        from app.services import contact_service

        contacts = contact_service.parse_quick_text("Jane Doe\njane@example.com\n\nBob Smith\nbob@example.com\n")
        self.assertEqual(len(contacts), 2)

    def test_has_contact_signal_rejects_junk(self):
        from app.services import contact_service

        self.assertFalse(contact_service.has_contact_signal(""))
        self.assertFalse(contact_service.has_contact_signal("123456"))
        self.assertFalse(contact_service.has_contact_signal("just some words"))
        self.assertTrue(contact_service.has_contact_signal("Jane\njane@example.com"))
        self.assertTrue(contact_service.has_contact_signal("Name: Jane"))
        self.assertTrue(contact_service.has_contact_signal("Call +44 7700 900123 today"))


if __name__ == "__main__":
    unittest.main()
