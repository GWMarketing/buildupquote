"""Tests for the cockpit upgrade:

  1. Material waste factor -- applied to material quantities/costs ONLY
     (labor untouched), with a raw/waste/labor breakdown.
  2. Payment milestones -- persisted on the quote, rendered on the public
     proposal page and in the exported PDF.
  3. The builder UI renders the room presets, waste pills, and payment
     schedule controls.
"""
import io
import os
import tempfile
import unittest
from types import SimpleNamespace

_DB = os.path.join(tempfile.gettempdir(), "test_cockpit.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
from app.services import assembly_service  # noqa: E402


def _assembly_fixture():
    """A formula assembly with one material + one labor component."""
    return SimpleNamespace(
        calculator=None,
        required_inputs=["length", "width", "height"],
        components=[
            SimpleNamespace(
                id=1, description="Drywall sheets", item_type="material",
                formula="length * width / 32", unit="sheet",
                default_unit_cost=12.0, default_markup_percent=20.0,
            ),
            SimpleNamespace(
                id=2, description="Framing labor", item_type="labor",
                formula="length * width / 100 * 2", unit="hr",
                default_unit_cost=40.0, default_markup_percent=0.0,
            ),
        ],
    )


class WasteFactorTestCase(unittest.TestCase):
    """Pure engine tests for the material waste multiplier."""

    def test_waste_inflates_material_but_not_labor(self):
        lines, summary = assembly_service.calculate_assembly_with_summary(
            _assembly_fixture(), {"length": 10, "width": 10, "height": 8},
            waste_percent=10,
        )
        material = next(l for l in lines if l["item_type"] == "material")
        labor = next(l for l in lines if l["item_type"] == "labor")

        raw_qty = 10 * 10 / 32          # 3.125 sheets
        self.assertEqual(material["quantity"], round(raw_qty * 1.1, 3))
        self.assertEqual(labor["quantity"], round(10 * 10 / 100 * 2, 3))  # untouched

        raw_sub = round(raw_qty * 12 * 1.2, 2)
        self.assertEqual(summary["waste_percent"], 10.0)
        self.assertEqual(summary["materials_raw"], raw_sub)
        self.assertEqual(summary["waste_added"], round(material["subtotal"] - raw_sub, 2))
        self.assertAlmostEqual(summary["total"], material["subtotal"] + labor["subtotal"], places=2)

    def test_zero_waste_leaves_materials_unchanged(self):
        lines, summary = assembly_service.calculate_assembly_with_summary(
            _assembly_fixture(), {"length": 10, "width": 10, "height": 8},
            waste_percent=0,
        )
        material = next(l for l in lines if l["item_type"] == "material")
        self.assertEqual(material["quantity"], round(10 * 10 / 32, 3))
        self.assertEqual(summary["waste_added"], 0.0)

class CockpitApiTestCase(unittest.TestCase):
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

    def make_client(self, auth, name="Joan Smith", email="joan@example.com"):
        r = self.client.post("/api/clients", headers=auth, json={"name": name, "email": email})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def make_quote(self, auth, client_id=None):
        r = self.client.post("/api/quotes", headers=auth, json={
            "title": "Basement Finish", "client_id": client_id,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def add_line(self, auth, quote_id):
        r = self.client.put(f"/api/quotes/{quote_id}/lines", headers=auth, json=[{
            "description": "Drywall install", "item_type": "material",
            "quantity": 10, "unit": "m2", "unit_cost": 20.0, "markup_percent": 20,
        }])
        self.assertEqual(r.status_code, 200, r.text)

    # ------------------------------------------------------------------
    # Waste factor via the API
    # ------------------------------------------------------------------
    def test_assembly_calculate_returns_waste_summary(self):
        auth = self.register("ckp-calc@acme.com")
        body = {"dimensions": {"length": 4, "width": 3}, "waste_percent": 10}
        r = self.client.post("/api/assemblies/TILE_BATHROOM_FLOOR/calculate",
                             headers=auth, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["summary"]["waste_percent"], 10.0)
        self.assertIn("materials_raw", r.json()["summary"])
        self.assertIn("waste_added", r.json()["summary"])
        self.assertIn("labor_total", r.json()["summary"])

        # Labor quantities are identical at 0% and 10%; materials inflate.
        r0 = self.client.post("/api/assemblies/TILE_BATHROOM_FLOOR/calculate",
                              headers=auth, json={**body, "waste_percent": 0})
        labor0 = next(l for l in r0.json()["lines"] if l["item_type"] == "labor")
        labor10 = next(l for l in r.json()["lines"] if l["item_type"] == "labor")
        self.assertEqual(labor0["quantity"], labor10["quantity"])
        material0 = next(l for l in r0.json()["lines"] if l["item_type"] == "material")
        material10 = next(l for l in r.json()["lines"] if l["item_type"] == "material")
        self.assertGreater(material10["quantity"], material0["quantity"])

    def test_apply_assembly_accepts_waste_and_returns_summary(self):
        auth = self.register("ckp-apply@acme.com")
        qid = self.make_quote(auth)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "TILE_BATHROOM_FLOOR",
            "dimensions": {"length": 4, "width": 3},
            "waste_percent": 15,
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["summary"]["waste_percent"], 15.0)
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertGreater(detail["total"], 0)

    # ------------------------------------------------------------------
    # Payment milestones
    # ------------------------------------------------------------------
    def test_payment_schedule_persists_and_renders(self):
        auth = self.register("ckp-pay@acme.com")
        cid = self.make_client(auth)
        qid = self.make_quote(auth, client_id=cid)
        self.add_line(auth, qid)

        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "payment_schedule": [
                {"label": "Deposit upon signing", "percent": 50},
                {"label": "Final Completion", "percent": 50},
            ],
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["payment_schedule"][0]["label"], "Deposit upon signing")

        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertEqual(len(detail["payment_schedule"]), 2)
        public_uuid = detail["public_uuid"]

        # Rendered on the client-facing page.
        r = self.client.get(f"/view/quote/{public_uuid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Payment Schedule", r.text)
        self.assertIn("Deposit upon signing", r.text)
        self.assertIn("Final Completion", r.text)

        # Rendered in the exported PDF.
        import pdfplumber
        r = self.client.get(f"/api/quotes/{qid}/export-pdf", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        self.assertIn("Payment Schedule", text)
        self.assertIn("Deposit upon signing", text)

    # ------------------------------------------------------------------
    # Builder UI
    # ------------------------------------------------------------------
    def test_builder_renders_cockpit_controls(self):
        r = self.client.get("/quotes/new")
        self.assertEqual(r.status_code, 200, r.text)
        html = r.text
        self.assertIn("1-Tap Room Presets", html)
        self.assertIn("10' x 10' Bedroom", html)
        self.assertIn("12' x 20' Garage / Bay", html)
        self.assertIn("Material Waste Factor", html)
        self.assertIn("+10%", html)
        self.assertIn("waste_percent", html)
        self.assertIn("Payment Schedule", html)
        self.assertIn("50 / 50", html)
        self.assertIn("33 / 33 / 34", html)
        self.assertIn("previewAssembly", html)
        self.assertIn("applyRoomPreset", html)


if __name__ == "__main__":
    unittest.main()


