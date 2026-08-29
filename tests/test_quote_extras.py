"""Tests for the Labor-Only toggle, Scope Exclusions, and Instant Deposit
prompt feature pack:

  1. Applying an assembly in labor-only mode skips every material line
     (client supplies materials) while keeping the labor hours + margin.
  2. Standard scope exclusions persist on the quote and render under
     "Scope Exclusions" on the public proposal page and the branded PDF.
  3. Deposit payment instructions persist, and the post-sign success modal
     shows the deposit due with a "Pay Deposit Online" button (or Venmo /
     bank wire instructions).
"""
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "test_quote_extras.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


class QuoteExtrasTestCase(unittest.TestCase):
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

    def make_quote(self, auth, title="Basement Finish"):
        r = self.client.post("/api/quotes", headers=auth, json={"title": title})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def add_line(self, auth, quote_id):
        r = self.client.put(f"/api/quotes/{quote_id}/lines", headers=auth, json=[{
            "description": "Drywall install", "item_type": "material",
            "quantity": 10, "unit": "m2", "unit_cost": 20.0, "markup_percent": 20,
        }])
        self.assertEqual(r.status_code, 200, r.text)

    def public_uuid(self, quote_id):
        db = SessionLocal()
        try:
            return db.query(models.Quote).filter(models.Quote.id == quote_id).first().public_uuid
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 1. Labor-only assembly toggle
    # ------------------------------------------------------------------
    def test_labor_only_assembly_keeps_labor_and_drops_materials(self):
        auth = self.register("extras-lo@acme.com")
        qid = self.make_quote(auth)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "TILE_BATHROOM_FLOOR",
            "dimensions": {"length": 4, "width": 3},
            "labor_only": True,
        })
        self.assertEqual(r.status_code, 200, r.text)
        added = r.json()["added_lines"]
        self.assertTrue(added, "labor-only apply should still produce labor lines")
        self.assertTrue(all(l["item_type"] == "labor" for l in added))

        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        types = {l["item_type"] for l in detail["lines"]}
        self.assertNotIn("material", types)
        self.assertIn("labor", types)
        self.assertGreater(detail["total"], 0)  # labor hours + margin still priced

    def test_full_assembly_keeps_materials(self):
        auth = self.register("extras-full@acme.com")
        qid = self.make_quote(auth)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "TILE_BATHROOM_FLOOR",
            "dimensions": {"length": 4, "width": 3},
        })
        self.assertEqual(r.status_code, 200, r.text)
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        types = {l["item_type"] for l in detail["lines"]}
        self.assertIn("material", types)
        self.assertIn("labor", types)

    # ------------------------------------------------------------------
    # 2. Standard scope exclusions
    # ------------------------------------------------------------------
    def test_exclusions_persist_and_render_on_proposal_and_pdf(self):
        auth = self.register("extras-ex@acme.com")
        qid = self.make_quote(auth)
        self.add_line(auth, qid)
        exclusions = [
            "Hidden water damage or dry rot behind walls excluded.",
            "Lead paint and asbestos remediation excluded.",
            "City permit, engineering, and inspection fees billed separately.",
            "Final primer and paint finish coats not included.",
            "Client supplies the appliance package.",
        ]
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"exclusions": exclusions})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["exclusions"], exclusions)

        pub = self.public_uuid(qid)
        page = self.client.get(f"/view/quote/{pub}")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Scope Exclusions", page.text)
        self.assertIn("Hidden water damage or dry rot behind walls excluded.", page.text)
        self.assertIn("Client supplies the appliance package.", page.text)

        pdf = self.client.get(f"/view/quote/{pub}/download-pdf")
        self.assertEqual(pdf.status_code, 200, pdf.text)
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf.content)) as pdf_doc:
            text = "\n".join((p.extract_text() or "") for p in pdf_doc.pages)
        self.assertIn("Scope Exclusions", text)
        self.assertIn("Lead paint and asbestos remediation excluded.", text)

    # ------------------------------------------------------------------
    # 3. Instant deposit prompt (post-sign)
    # ------------------------------------------------------------------
    def test_deposit_prompt_shows_deposit_due_and_pay_button(self):
        auth = self.register("extras-dep@acme.com")
        qid = self.make_quote(auth)
        self.add_line(auth, qid)  # 10 x 20 x 1.2 = $240.00 total
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "payment_schedule": [
                {"label": "Deposit upon signing", "percent": 50},
                {"label": "Final Completion", "percent": 50},
            ],
            "payment_instructions": {
                "payment_link": "https://buy.stripe.com/test_123",
                "venmo": "AcmeRoofing",
            },
        })
        self.assertEqual(r.status_code, 200, r.text)

        pub = self.public_uuid(qid)
        page = self.client.get(f"/view/quote/{pub}").text
        self.assertIn("Pay Deposit Online", page)
        self.assertIn("https://buy.stripe.com/test_123", page)
        self.assertIn("AcmeRoofing", page)
        # Scan-to-pay QR code renders for the payment link.
        self.assertIn("api.qrserver.com/v1/create-qr-code", page)
        self.assertIn("Prefer to scan?", page)

        # Sign it: the accept response carries the deposit + instructions, and
        # the success modal confirms acceptance.
        r = self.client.post(f"/api/public/quotes/{pub}/accept", json={
            "signature_data": "data:image/png;base64,AAAA",
            "client_name": "Joan Smith",
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["deposit"]["percent"], 50)
        self.assertEqual(body["deposit"]["amount"], 120.0)  # $240 x 50%
        self.assertEqual(body["payment_instructions"]["venmo"], "AcmeRoofing")

        after = self.client.get(f"/view/quote/{pub}").text
        self.assertIn("Proposal Accepted &amp; Signed!", after)
        self.assertIn("Pay Deposit Online", after)
        self.assertIn("$120.00", after)

    def test_deposit_prompt_shows_bank_wire_when_no_link(self):
        auth = self.register("extras-wire@acme.com")
        qid = self.make_quote(auth)
        self.add_line(auth, qid)
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "payment_schedule": [{"label": "Deposit", "percent": 30}],
            "payment_instructions": {"bank_wire": "First National, routing 111000025, acct 00012345"},
        })
        pub = self.public_uuid(qid)
        page = self.client.get(f"/view/quote/{pub}").text
        self.assertNotIn("Pay Deposit Online", page)  # no checkout link configured
        self.assertIn("Bank wire", page)
        self.assertIn("First National", page)

    # ------------------------------------------------------------------
    # Builder renders the new controls
    # ------------------------------------------------------------------
    def test_builder_renders_labor_exclusions_and_deposit_controls(self):
        self.register("extras-ui@acme.com")
        r = self.client.get("/quotes/new")
        for needle in ("Installation Mode", "laborOnly", "Materials Supplied by Client",
                       "exclusionPresets", "Standard Scope Exclusions", "saveExclusions",
                       "Deposit Payment", "payment_instructions", "savePaymentInstructions"):
            self.assertIn(needle, r.text, needle)


if __name__ == "__main__":
    unittest.main()

