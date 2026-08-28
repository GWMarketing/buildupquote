"""Unit tests for the Google Contacts (People API) service -- the pure
mapping and consent-URL builders (no network)."""
import unittest
import urllib.parse

from app.services import google_contacts


class MapPeopleTest(unittest.TestCase):
    def test_maps_full_person(self):
        people = [{
            "names": [{"displayName": "Jane Doe"}],
            "emailAddresses": [{"value": "jane@example.com"}],
            "phoneNumbers": [{"value": "+44 7700 900123"}],
            "addresses": [{"formattedValue": "123 High St, Manchester"}],
        }]
        contacts = google_contacts.map_people_to_contacts(people)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["name"], "Jane Doe")
        self.assertEqual(contacts[0]["email"], "jane@example.com")
        self.assertEqual(contacts[0]["phone"], "+44 7700 900123")
        self.assertEqual(contacts[0]["site_address"], "123 High St, Manchester")

    def test_skips_people_with_no_contact_info(self):
        people = [{"names": []}, {}, {"emailAddresses": [{"value": "a@b.com"}]}]
        contacts = google_contacts.map_people_to_contacts(people)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["email"], "a@b.com")

    def test_empty_and_none_input(self):
        self.assertEqual(google_contacts.map_people_to_contacts([]), [])
        self.assertEqual(google_contacts.map_people_to_contacts(None), [])


class BuildAuthUrlTest(unittest.TestCase):
    def test_contains_required_params(self):
        url = google_contacts.build_auth_url(
            "cid.apps.googleusercontent.com", "https://example.com/cb", "state123",
        )
        self.assertIn("https://accounts.google.com/o/oauth2/v2/auth?", url)
        self.assertIn("client_id=cid.apps.googleusercontent.com", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fexample.com%2Fcb", url)
        self.assertIn("response_type=code", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("state=state123", url)
        self.assertIn(
            "scope=" + urllib.parse.quote(google_contacts.GOOGLE_CONTACTS_SCOPE, safe=""), url,
        )


if __name__ == "__main__":
    unittest.main()
