"""Tests for the Batch Room Replicator, Taxes & Permit Surcharge, Milestone
Draw Approvals, and Warranty & Guarantee Presets.

  1. Duplicate a recorded assembly room with new dimensions -- specs, waste,
     and labor recomputed, appended to the active quote.
  2. Material tax applies to the post-waste material subtotal only (labor
     exempt) and a flat permit fee is itemized on the total.
  3. Milestone draw: request a draw on an unreleased stage (photos + notes),
     homeowner approves via /milestone/<token>, stage becomes released.
  4. Warranty clauses persist and render on the public proposal + PDF.
"""
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "test_replicator_taxes.db")
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


def _unique_email():
    _EMAIL_SEQ[0] += 1
    return f"rtw{_EMAIL_SEQ[0]}@example.com"


class ReplicatorTaxesWarrantyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(fastapi_app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def register(self, email=None, org="Acme Roofing"):
        r = self.client.post("/api/auth/register", json={
            "email": email or _unique_email(), "password": "pw12345678",
            "organization_name": org,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    def make_quote(self, auth, title="Multi-Room Suite"):
        r = self.client.post("/api/quotes", headers=auth, json={"title": title})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    # ------------------------------------------------------------------
    # 1. Batch room replicator
    # ------------------------------------------------------------------
    def test_duplicate_room_recomputes_specs_with_new_dimensions(self):
        auth = self.register()
        qid = self.make_quote(auth)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "TILE_BATHROOM_FLOOR",
            "dimensions": {"length": 4, "width": 3},
            "waste_percent": 10,
            "room_name": "Bathroom 1",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["rooms"]), 1)
        room0 = r.json()["rooms"][0]
        self.assertEqual(room0["name"], "Bathroom 1")
        self.assertEqual(room0["key"], 0)
        self.assertTrue(room0["line_ids"])

        before = r.json()["quote_total"]
        r = self.client.post(f"/api/quotes/{qid}/duplicate-room", headers=auth, json={
            "room_key": 0, "name": "Bathroom 2",
            "dimensions": {"length": 6, "width": 4, "height": 8},
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["rooms"]), 2)
        self.assertGreater(r.json()["quote_total"], before)
        room1 = r.json()["rooms"][1]
        self.assertEqual(room1["name"], "Bathroom 2")
        self.assertEqual(room1["dimensions"]["length"], 6.0)
        self.assertEqual(room1["waste_percent"], 10.0)

        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertEqual(len(detail["lines"]), len(room0["line_ids"]) * 2)

    def test_duplicate_unknown_room_404(self):
        auth = self.register()
        qid = self.make_quote(auth)
        self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "TILE_BATHROOM_FLOOR", "dimensions": {"length": 4, "width": 3},
        })
        r = self.client.post(f"/api/quotes/{qid}/duplicate-room", headers=auth, json={
            "room_key": 99, "name": "Nope", "dimensions": {"length": 5, "width": 4},
        })
        self.assertEqual(r.status_code, 404, r.text)

    # ------------------------------------------------------------------
    # 2. Material tax + permit surcharge
    # ------------------------------------------------------------------
    def test_material_tax_and_permit_fee_itemized(self):
        auth = self.register()
        qid = self.make_quote(auth)
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Drywall material", "item_type": "material",
             "quantity": 10, "unit": "sq ft", "unit_cost": 20.0, "markup_percent": 20},
            {"description": "Install labor", "item_type": "labor",
             "quantity": 10, "unit": "hr", "unit_cost": 50.0, "markup_percent": 20},
        ])
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "tax_rate_percent": 8.25, "permit_fee": 150,
        })
        self.assertEqual(r.status_code, 200, r.text)
        detail = r.json()
        self.assertEqual(round(detail["subtotal"], 2), 840.00)      # 240 + 600
        self.assertEqual(round(detail["tax_amount"], 2), 19.80)     # 240 x 8.25% only
        self.assertEqual(round(detail["permit_fee"], 2), 150.00)
        self.assertEqual(round(detail["total"], 2), 1009.80)        # 840 + 19.80 + 150

        pub = self.public_uuid(qid)
        page = self.client.get(f"/view/quote/{pub}").text
        self.assertIn("Material Tax", page)
        self.assertIn("Permit &amp; Municipal Fee", page)
        self.assertIn("$19.80", page)
        self.assertIn("$150.00", page)

        pdf = self.client.get(f"/view/quote/{pub}/download-pdf")
        self.assertEqual(pdf.status_code, 200)
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf.content)) as pdf_doc:
            text = "\n".join((p.extract_text() or "") for p in pdf_doc.pages)
        self.assertIn("Material Tax", text)
        self.assertIn("Permit", text)

    def public_uuid(self, quote_id):
        db = SessionLocal()
        try:
            return db.query(models.Quote).filter(models.Quote.id == quote_id).first().public_uuid
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 3. Milestone draw approvals
    # ------------------------------------------------------------------
    def test_milestone_draw_flow_releases_stage(self):
        auth = self.register()
        qid = self.make_quote(auth)
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[{
            "description": "Drywall material", "item_type": "material",
            "quantity": 10, "unit": "sq ft", "unit_cost": 20.0, "markup_percent": 20,
        }])
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "status": "sent",
            "payment_schedule": [
                {"label": "Deposit", "percent": 50},
                {"label": "Rough-in", "percent": 50},
            ],
        })

        r = self.client.post(f"/api/quotes/{qid}/milestone-draws", headers=auth, json={
            "milestone_index": 0, "notes": "Dry-in complete",
        })
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["token"]

        page = self.client.get(f"/milestone/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Milestone Draw Request", page.text)
        self.assertIn("Deposit", page.text)
        self.assertIn("Dry-in complete", page.text)
        self.assertIn("Approve &amp; Release Payment", page.text)

        r = self.client.post(f"/milestone/{token}/approve")
        self.assertEqual(r.status_code, 200, r.text)

        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertTrue(detail["payment_schedule"][0]["released"])
        self.assertFalse(detail["payment_schedule"][1]["released"])

        after = self.client.get(f"/milestone/{token}").text
        self.assertIn("Payment Released", after)

        # A released stage can't be drawn again.
        r = self.client.post(f"/api/quotes/{qid}/milestone-draws", headers=auth, json={
            "milestone_index": 0,
        })
        self.assertEqual(r.status_code, 400, r.text)

    def test_milestone_draw_rejects_too_many_photos(self):
        auth = self.register()
        qid = self.make_quote(auth)
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "status": "sent",
            "payment_schedule": [{"label": "Deposit", "percent": 100}],
        })
        r = self.client.post(f"/api/quotes/{qid}/milestone-draws", headers=auth, json={
            "milestone_index": 0,
            "photos": ["data:image/jpeg;base64,AAAA"] * 4,
        })
        self.assertEqual(r.status_code, 400, r.text)

    # ------------------------------------------------------------------
    # 4. Warranty & guarantee presets
    # ------------------------------------------------------------------
    def test_warranty_terms_render_on_proposal_and_pdf(self):
        auth = self.register()
        qid = self.make_quote(auth)
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[{
            "description": "Drywall material", "item_type": "material",
            "quantity": 10, "unit": "sq ft", "unit_cost": 20.0, "markup_percent": 20,
        }])
        warranty = [
            "1-Year craftsmanship warranty on all labor.",
            "Manufacturer warranty only — no workmanship warranty beyond the manufacturer\u2019s terms.",
            "Lifetime workmanship guarantee on structural repairs.",
        ]
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"warranty_terms": warranty})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["warranty_terms"], warranty)

        pub = self.public_uuid(qid)
        page = self.client.get(f"/view/quote/{pub}").text
        self.assertIn("Warranty &amp; Guarantee", page)
        self.assertIn("1-Year craftsmanship warranty on all labor.", page)
        self.assertIn("Lifetime workmanship guarantee on structural repairs.", page)

        pdf = self.client.get(f"/view/quote/{pub}/download-pdf")
        self.assertEqual(pdf.status_code, 200)
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf.content)) as pdf_doc:
            text = "\n".join((p.extract_text() or "") for p in pdf_doc.pages)
        self.assertIn("Warranty", text)
        self.assertIn("1-Year craftsmanship warranty on all labor.", text)

    # ------------------------------------------------------------------
    # Builder renders the new controls
    # ------------------------------------------------------------------
    def test_builder_renders_replicator_taxes_draw_and_warranty_controls(self):
        self.register()
        r = self.client.get("/quotes/new")
        for needle in ("Rooms / Scopes", "Duplicate Assembly", "duplicate-room",
                       "Taxes &amp; Surcharges", "Material Tax", "Permit &amp; Municipal Fee",
                       "Request Milestone Draw", "milestone-draws", "Warranty &amp; Guarantee",
                       "warrantyPresets"):
            self.assertIn(needle, r.text, needle)


if __name__ == "__main__":
    unittest.main()
