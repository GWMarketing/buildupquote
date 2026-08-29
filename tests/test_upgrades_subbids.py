"""Tests for Interactive Client Upgrades and the Subcontractor Bid Link.

  1. Optional client add-ons: marked lines are excluded from the default grand
     total, rendered as an interactive "Recommended Upgrades" card on the
     public proposal, and folded into the agreed total when the client selects
     them at signature.
  2. Subcontractor bid links: a sanitized /sub-bid/<token> page (no pricing,
     margins, or client contact info) where a sub submits a lump-sum bid that
     lands back on the master quote as a subcontractor line.
"""
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "test_upgrades_subbids.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


_EMAIL_SEQ = [0]


def _unique_email(prefix="usb"):
    _EMAIL_SEQ[0] += 1
    return f"{prefix}{_EMAIL_SEQ[0]}@example.com"


class UpgradesSubBidsTestCase(unittest.TestCase):
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

    def make_quote(self, auth):
        r = self.client.post("/api/quotes", headers=auth, json={"title": "Upgrade Suite"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def set_lines(self, auth, qid, lines):
        r = self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=lines)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def public_uuid(self, quote_id):
        db = SessionLocal()
        try:
            return db.query(models.Quote).filter(models.Quote.id == quote_id).first().public_uuid
        finally:
            db.close()

    def add_optional_setup(self):
        """Quote with a $240 base line and a $100 optional upgrade."""
        auth = self.register(_unique_email())
        qid = self.make_quote(auth)
        detail = self.set_lines(auth, qid, [
            {"description": "Drywall install", "item_type": "material",
             "quantity": 10, "unit": "m2", "unit_cost": 20.0, "markup_percent": 20,
             "is_optional": False},
            {"description": "Acoustic ceiling upgrade", "item_type": "material",
             "quantity": 10, "unit": "m2", "unit_cost": 8.3333, "markup_percent": 20,
             "is_optional": True},
        ])
        required = next(l for l in detail["lines"] if not l["is_optional"])
        optional = next(l for l in detail["lines"] if l["is_optional"])
        return auth, qid, required, optional

    # ------------------------------------------------------------------
    # 1. Optional client add-ons
    # ------------------------------------------------------------------
    def test_optional_line_excluded_from_base_total(self):
        auth, qid, required, optional = self.add_optional_setup()
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertEqual(round(detail["total"], 2), 240.00)  # base only
        self.assertEqual(round(optional["line_total"], 2), 100.00)
        self.assertTrue(optional["is_optional"])
        self.assertFalse(required["is_optional"])

    def test_public_page_renders_recommended_upgrades_card(self):
        auth, qid, required, optional = self.add_optional_setup()
        pub = self.public_uuid(qid)
        page = self.client.get(f"/view/quote/{pub}").text
        self.assertIn("Recommended Upgrades", page)
        self.assertIn("Acoustic ceiling upgrade", page)
        self.assertIn("upgrade-cb", page)
        self.assertIn("$100.00", page)

    def test_accept_folds_selected_upgrades_into_total(self):
        auth, qid, required, optional = self.add_optional_setup()
        pub = self.public_uuid(qid)
        r = self.client.post(f"/api/public/quotes/{pub}/accept", json={
            "signature_data": "data:image/png;base64,AAAA",
            "client_name": "Joan Smith",
            "selected_optional_ids": [optional["id"]],
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(round(r.json()["total"], 2), 340.00)  # 240 + 100
        self.assertEqual(r.json()["selected_optional_line_ids"], [optional["id"]])

        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertEqual(round(detail["total"], 2), 340.00)
        self.assertEqual(detail["selected_optional_line_ids"], [optional["id"]])

    def test_accept_without_selection_keeps_base_total(self):
        auth, qid, required, optional = self.add_optional_setup()
        pub = self.public_uuid(qid)
        r = self.client.post(f"/api/public/quotes/{pub}/accept", json={
            "signature_data": "data:image/png;base64,AAAA",
            "client_name": "Joan Smith",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(round(r.json()["total"], 2), 240.00)
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertEqual(round(detail["total"], 2), 240.00)

    # ------------------------------------------------------------------
    # 2. Subcontractor bid links
    # ------------------------------------------------------------------
    def test_sub_bid_link_page_is_sanitized(self):
        auth = self.register("usb-sub@acme.com")
        r = self.client.post("/api/clients", headers=auth, json={
            "name": "Joan Smith", "email": "joan@example.com", "phone": "555-0000",
            "site_address": "7 Contract Ave",
        })
        cid = r.json()["id"]
        qid = self.make_quote(auth)
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "client_id": cid, "site_address": "123 Main St, Springfield",
        })
        detail = self.set_lines(auth, qid, [
            {"description": "Framing labor", "item_type": "labor", "trade": "Framing",
             "quantity": 20, "unit": "hr", "unit_cost": 40.0, "markup_percent": 20},
            {"description": "Drywall sheets", "item_type": "material", "trade": "Drywall",
             "quantity": 10, "unit": "sheet", "unit_cost": 12.0, "markup_percent": 20},
        ])
        labor = next(l for l in detail["lines"] if l["item_type"] == "labor")

        r = self.client.post(f"/api/quotes/{qid}/sub-bids", headers=auth, json={
            "line_ids": [labor["id"]], "notes": "Access via rear gate, 7am start.",
        })
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["token"]
        self.assertTrue(r.json()["url"].endswith(token))

        resp = self.client.get(f"/sub-bid/{token}")
        self.assertEqual(resp.status_code, 200)
        page = resp.text
        self.assertIn("Subcontractor Bid Request", page)
        self.assertIn("123 Main St, Springfield", page)
        self.assertIn("Framing labor", page)
        self.assertIn("Access via rear gate, 7am start.", page)
        self.assertIn("Your Total Bid ($)", page)
        # Sanitized: no client contact info, no margins, no pricing internals.
        self.assertNotIn("joan@example.com", page)
        self.assertNotIn("555-0000", page)
        self.assertNotIn("$960.00", page)   # the quote's priced line total
        self.assertNotIn("markup", page)

    def test_submit_bid_populates_master_quote(self):
        auth = self.register("usb-bid@acme.com")
        qid = self.make_quote(auth)
        self.set_lines(auth, qid, [
            {"description": "Framing labor", "item_type": "labor", "trade": "Framing",
             "quantity": 20, "unit": "hr", "unit_cost": 40.0, "markup_percent": 20},
        ])
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        before = detail["total"]
        line_id = detail["lines"][0]["id"]
        r = self.client.post(f"/api/quotes/{qid}/sub-bids", headers=auth, json={
            "line_ids": [line_id],
        })
        token = r.json()["token"]

        r = self.client.post(f"/sub-bid/{token}/submit", json={
            "bid_amount": 5000, "notes": "Includes dumpster.", "bidder_name": "Sam's Framing",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(round(r.json()["bid_amount"], 2), 5000.0)
        self.assertGreater(r.json()["quote_total"], before)

        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        sub_lines = [l for l in detail["lines"] if l["item_type"] == "subcontractor"]
        self.assertEqual(len(sub_lines), 1)
        self.assertEqual(round(sub_lines[0]["unit_cost"], 2), 5000.0)
        self.assertIn("Subcontractor bid", sub_lines[0]["description"])
        self.assertEqual(round(detail["total"], 2), round(before + 5000.0, 2))

        # A second submission on the same link is rejected.
        r = self.client.post(f"/sub-bid/{token}/submit", json={"bid_amount": 6000})
        self.assertEqual(r.status_code, 400, r.text)


if __name__ == "__main__":
    unittest.main()
