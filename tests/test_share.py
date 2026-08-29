"""Tests for the 1-click share feature: the quote API exposes what the share
UI needs (public_uuid + client_phone), and both the quote builder and the
quotes list render the Share modal wired to BQShare.
"""
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "test_share.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402


class ShareFeatureTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(fastapi_app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def register(self, email, org="Acme Roofing"):
        r = self.client.post("/api/auth/register", json={
            "email": email, "password": "pw12345678", "organization_name": org,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    def _make_quote_with_client(self, email):
        auth = self.register(email)
        r = self.client.post("/api/clients", headers=auth, json={
            "name": "Joan Smith", "phone": "+1 (555) 010-1234",
            "email": "joan@example.com",
        })
        self.assertEqual(r.status_code, 201, r.text)
        cid = r.json()["id"]
        r = self.client.post("/api/quotes", headers=auth, json={
            "title": "Basement Finish", "client_id": cid,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return auth, r.json()["id"]

    # ------------------------------------------------------------------
    # API: quote payload carries the share data
    # ------------------------------------------------------------------
    def test_quote_out_exposes_public_uuid_and_client_phone(self):
        auth, qid = self._make_quote_with_client("shr-api@acme.com")

        r = self.client.get(f"/api/quotes/{qid}", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIsNotNone(body["public_uuid"])
        self.assertEqual(body["client_phone"], "+1 (555) 010-1234")

        r = self.client.get("/api/quotes", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        row = next(q for q in r.json() if q["id"] == qid)
        self.assertIsNotNone(row["public_uuid"])
        self.assertEqual(row["client_phone"], "+1 (555) 010-1234")

    def test_public_share_link_resolves(self):
        auth, qid = self._make_quote_with_client("shr-link@acme.com")
        r = self.client.get(f"/api/quotes/{qid}", headers=auth)
        public_uuid = r.json()["public_uuid"]

        r = self.client.get(f"/view/quote/{public_uuid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Basement Finish", r.text)

    # ------------------------------------------------------------------
    # UI: builder + list render the share UI
    # ------------------------------------------------------------------
    def test_quote_builder_renders_share_button_and_modal(self):
        r = self.client.get("/quotes/new")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Share Proposal", r.text)
        self.assertIn("Send via WhatsApp", r.text)
        self.assertIn("Send via Text (SMS)", r.text)
        self.assertIn("Copy Link", r.text)
        self.assertIn("BQShare.buildLinks", r.text)
        self.assertIn("/view/quote/' + (this.quote.public_uuid", r.text)

    def test_quotes_list_renders_share_button_and_modal(self):
        r = self.client.get("/quotes")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("openShare(", r.text)
        self.assertIn("Send via WhatsApp", r.text)
        self.assertIn("shareUrlFor", r.text)

    def test_share_js_is_loaded_and_exposes_bqshare(self):
        r = self.client.get("/static/js/share.js")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("BQShare", r.text)
        self.assertIn("wa.me", r.text)
        self.assertIn("PROPOSAL_SHARED_VIA_", r.text)
        # And every page loads it (base.html includes it after app.js).
        r = self.client.get("/dashboard")
        self.assertIn("/static/js/share.js", r.text)


if __name__ == "__main__":
    unittest.main()
