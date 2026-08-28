"""Integration tests for the CRM API layer (auth, organization, clients,
quotes, assemblies, and the parser->quote pipeline) against a throwaway
SQLite database.

Runs inside `unittest discover -s tests` next to the pure-logic suites:
this module sets DATABASE_URL/SECRET_KEY *before* importing the app, so the
whole run never touches the real postgres database.
"""
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "test_crm_api.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")


class CrmApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(fastapi_app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def register(self, email, org="Acme Roofing", full_name=None):
        payload = {
            "email": email, "password": "pw12345678", "organization_name": org,
        }
        if full_name:
            payload["full_name"] = full_name
        r = self.client.post("/api/auth/register", json=payload)
        self.assertEqual(r.status_code, 201, r.text)
        token = r.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def make_quote(self, auth, title="Test quote"):
        r = self.client.post("/api/quotes", headers=auth, json={"title": title})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    # ------------------------------------------------------------------
    # Auth + organization
    # ------------------------------------------------------------------
    def test_register_issues_jwt_and_me_roundtrips(self):
        auth = self.register("owner@acme.com")
        r = self.client.get("/api/auth/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["email"], "owner@acme.com")
        self.assertEqual(r.json()["role"], "owner")

    def test_login_roundtrip(self):
        self.register("login@acme.com")
        r = self.client.post("/api/auth/token", data={
            "username": "login@acme.com", "password": "pw12345678",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["access_token"])
        r = self.client.post("/api/auth/token", data={
            "username": "login@acme.com", "password": "wrong",
        })
        self.assertEqual(r.status_code, 401)

    def test_protected_endpoints_require_auth(self):
        r = self.client.get("/api/quotes")
        self.assertEqual(r.status_code, 401)

    def test_org_profile_update_all_fields(self):
        auth = self.register("profile@acme.com", full_name="Glenn Westman")
        r = self.client.put("/api/organization/me", headers=auth, json={
            "name": "Acme Roofing Co",
            "bio": "Licensed roof repair & restoration specialists",
            "website": "https://acme.example.com",
            "email": "office@acme.example.com",
            "phone": "555-0000",
            "address": "1 Market St, Anytown",
            "license_number": "CSLB #123456",
            "tax_id": "TX-1",
            "default_payment_terms": "Due on receipt. 10% discount for upfront payment.",
            "currency_symbol": "£",
            "full_name": "Glenn Westman",
            "job_title": "Owner / Lead Contractor",
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["name"], "Acme Roofing Co")
        self.assertEqual(body["bio"], "Licensed roof repair & restoration specialists")
        self.assertEqual(body["website"], "https://acme.example.com")
        self.assertEqual(body["email"], "office@acme.example.com")
        self.assertEqual(body["phone"], "555-0000")
        self.assertEqual(body["address"], "1 Market St, Anytown")
        self.assertEqual(body["license_number"], "CSLB #123456")
        self.assertEqual(body["tax_id"], "TX-1")
        self.assertEqual(body["default_payment_terms"], "Due on receipt. 10% discount for upfront payment.")
        self.assertEqual(body["currency_symbol"], "£")
        self.assertEqual(body["full_name"], "Glenn Westman")
        self.assertEqual(body["job_title"], "Owner / Lead Contractor")
        # Persisted: a fresh GET returns the same values, including the rep.
        r = self.client.get("/api/organization/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["license_number"], "CSLB #123456")
        self.assertEqual(r.json()["bio"], "Licensed roof repair & restoration specialists")
        self.assertEqual(r.json()["job_title"], "Owner / Lead Contractor")

    def test_users_me(self):
        auth = self.register("usersme@acme.com", full_name="Ada Lovelace")
        r = self.client.get("/api/users/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["email"], "usersme@acme.com")
        self.assertEqual(r.json()["full_name"], "Ada Lovelace")
        self.assertEqual(r.json()["role"], "owner")

    def test_profile_persists_across_fresh_login(self):
        # Register, save a full profile, then "log out" and sign back in with
        # a brand-new token: the next session must see exactly what was saved.
        self.register("relogin@acme.com", full_name="Glenn Westman")
        r = self.client.post("/api/auth/token", data={
            "username": "relogin@acme.com", "password": "pw12345678",
        })
        auth = {"Authorization": "Bearer " + r.json()["access_token"]}
        payload = {
            "name": "Glenn's Roofing & Co",
            "bio": "Roof repair specialists",
            "website": "https://glennwestman.com",
            "email": "office@example.com",
            "phone": "555-1234",
            "address": "1 Market St",
            "license_number": "CSLB #1",
            "tax_id": "EIN-1",
            "default_payment_terms": "Net 14",
            "currency_symbol": "£",
            "full_name": "Glenn Westman",
            "job_title": "Owner / Lead Contractor",
        }
        r = self.client.put("/api/organization/me", headers=auth, json=payload)
        self.assertEqual(r.status_code, 200, r.text)

        # Simulate next-day login: a completely fresh token, no prior context.
        r = self.client.post("/api/auth/token", data={
            "username": "relogin@acme.com", "password": "pw12345678",
        })
        auth2 = {"Authorization": "Bearer " + r.json()["access_token"]}
        r = self.client.get("/api/organization/me", headers=auth2)
        self.assertEqual(r.status_code, 200, r.text)
        for k, v in payload.items():
            self.assertEqual(r.json()[k], v, k)
        r = self.client.get("/api/users/me", headers=auth2)
        self.assertEqual(r.json()["job_title"], "Owner / Lead Contractor")
        self.assertEqual(r.json()["full_name"], "Glenn Westman")

    def test_logo_upload_roundtrip(self):
        auth = self.register("logo@acme.com")
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        r = self.client.post("/api/organization/logo", headers=auth,
                             files={"file": ("logo.png", png, "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        logo_url = r.json()["logo_url"]
        self.assertTrue(logo_url.startswith("/static/uploads/logos/org-"), logo_url)
        # The uploaded file is served through the static mount.
        r = self.client.get(logo_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        # Clean the test logo off the working tree (uploaded logos are gitignored).
        logo_path = os.path.join(_STATIC, "uploads", "logos", os.path.basename(logo_url))
        if os.path.exists(logo_path):
            os.remove(logo_path)
        # Logo upload is rejected for disallowed extensions.
        r = self.client.post("/api/organization/logo", headers=auth,
                             files={"file": ("logo.txt", b"not an image", "text/plain")})
        self.assertEqual(r.status_code, 400)

    def test_org_me_autoprovisions_for_orgless_user(self):
        # Registered without a company name -> no org, but GET /me creates one.
        auth = self.register("solopreneur@acme.com", org="", full_name="Solopreneur")
        r = self.client.get("/api/organization/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        org = r.json()
        self.assertEqual(org["name"], "Solopreneur")  # falls back to full_name
        # The same org now backs the user's quotes.
        r = self.client.post("/api/quotes", headers=auth, json={"title": "First job"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["organization_id"], org["id"])

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------
    def test_client_create_list_delete(self):
        auth = self.register("client@acme.com")
        r = self.client.post("/api/clients", headers=auth, json={
            "name": "Jane Doe", "email": "jane@example.com", "phone": "555-010-1234",
        })
        self.assertEqual(r.status_code, 201, r.text)
        client_id = r.json()["id"]
        r = self.client.get("/api/clients", headers=auth)
        self.assertEqual(len(r.json()), 1)
        r = self.client.delete(f"/api/clients/{client_id}", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.client.get("/api/clients", headers=auth).json()), 0)

    def test_client_delete_blocked_when_quoted(self):
        auth = self.register("blocked@acme.com")
        cid = self.client.post("/api/clients", headers=auth, json={"name": "Quoted"}).json()["id"]
        qid = self.make_quote(auth)
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"client_id": cid})
        r = self.client.delete(f"/api/clients/{cid}", headers=auth)
        self.assertEqual(r.status_code, 400)
        self.assertIn("active quote", r.json()["detail"])

    def test_import_vcf_file(self):
        auth = self.register("vcf@acme.com")
        vcf = (
            "BEGIN:VCARD\nVERSION:3.0\nFN:Jane Doe\n"
            "EMAIL;TYPE=INTERNET:jane@example.com\nTEL;TYPE=CELL:(555) 010-1234\n"
            "ADR;TYPE=HOME:;;123 Main St;Anytown;CA;90210\nEND:VCARD\n"
            "BEGIN:VCARD\nVERSION:3.0\nFN:Bob Smith\nEMAIL:bob@example.com\nEND:VCARD\n"
        )
        r = self.client.post("/api/clients/import-file", headers=auth,
                             files={"file": ("contacts.vcf", vcf, "text/x-vcard")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["created"], 2)
        self.assertEqual(body["skipped"], 0)
        by_name = {c["name"]: c for c in body["clients"]}
        self.assertEqual(by_name["Jane Doe"]["email"], "jane@example.com")
        self.assertEqual(by_name["Jane Doe"]["site_address"], "123 Main St, Anytown, CA, 90210")
        # Re-uploading the same file skips everything.
        r = self.client.post("/api/clients/import-file", headers=auth,
                             files={"file": ("contacts.vcf", vcf, "text/x-vcard")})
        self.assertEqual(r.json()["created"], 0)
        self.assertEqual(r.json()["skipped"], 2)

    def test_import_csv_file(self):
        auth = self.register("csv@acme.com")
        csv_data = "name,email,phone,site_address\nAcme Client,acme@example.com,555-010-9999,1 Market St\n"
        r = self.client.post("/api/clients/import-file", headers=auth,
                             files={"file": ("clients.csv", csv_data, "text/csv")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["clients"][0]["name"], "Acme Client")

    def test_import_unsupported_file_rejected(self):
        auth = self.register("badfile@acme.com")
        r = self.client.post("/api/clients/import-file", headers=auth,
                             files={"file": ("notes.txt", "hello", "text/plain")})
        self.assertEqual(r.status_code, 400)

    def test_quick_parse_text(self):
        auth = self.register("quick@acme.com")
        text = ("Jane Doe\njane@example.com\n(555) 010-1234\n123 Main St\n\n"
                "Bob Smith\nbob@example.com\n")
        r = self.client.post("/api/clients/quick-parse-text", headers=auth, json={"text": text})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["created"], 2)
        names = {c["name"] for c in body["clients"]}
        self.assertEqual(names, {"Jane Doe", "Bob Smith"})
        # Same email imported via quick-paste is deduped.
        r = self.client.post("/api/clients/quick-parse-text", headers=auth,
                             json={"text": "Someone Else\njane@example.com\n"})
        self.assertEqual(r.json()["created"], 0)
        self.assertEqual(r.json()["skipped"], 1)

    def test_clients_require_organization(self):
        auth = self.register("noorgan@acme.com", org="")
        r = self.client.post("/api/clients", headers=auth, json={"name": "X"})
        self.assertEqual(r.status_code, 400)


    # ------------------------------------------------------------------
    # Quotes: CRUD, lines, tax, assemblies
    # ------------------------------------------------------------------
    def test_quote_crud_and_lines_with_tax(self):
        auth = self.register("quote@acme.com")
        qid = self.make_quote(auth)
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth,
                              json={"title": "Roof repair", "status": "sent", "tax_rate_percent": 8.25})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tax_rate_percent"], 8.25)

        r = self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Shingles", "item_type": "material", "trade": "Roofing",
             "quantity": 10, "unit": "m2", "unit_cost": 20, "markup_percent": 20},
            {"description": "Labour", "item_type": "labor", "trade": "Roofing",
             "quantity": 4, "unit": "hr", "unit_cost": 50, "markup_percent": 0},
        ])
        self.assertEqual(r.status_code, 200, r.text)
        detail = r.json()
        self.assertEqual(detail["line_count"], 2)
        # (10*20*1.2) + (4*50*1.0) = 240 + 200 = 440 subtotal; tax 8.25% = 36.30
        self.assertAlmostEqual(detail["subtotal"], 440.0, places=2)
        self.assertAlmostEqual(detail["tax_amount"], 36.30, places=2)
        self.assertAlmostEqual(detail["total"], 476.30, places=2)

        # Deleting one persisted line recalculates totals.
        line_id = detail["lines"][0]["id"]
        r = self.client.delete(f"/api/quotes/{qid}/lines/{line_id}", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["line_count"], 1)
        self.assertAlmostEqual(r.json()["subtotal"], 200.0, places=2)

    def test_apply_assembly_persists_lines_and_tags_trade(self):
        auth = self.register("assembly@acme.com")
        qid = self.make_quote(auth)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "WALL_STUD_PARTITION", "dimensions": {"length": 10, "height": 3},
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["added_lines"]), 5)
        self.assertGreater(r.json()["quote_subtotal"], 0)
        # Persisted lines carry a trade tag from the lexicon.
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertTrue(all(l["trade"] for l in detail["lines"]))

    def test_apply_assembly_rejects_bad_dimensions(self):
        auth = self.register("assembly2@acme.com")
        qid = self.make_quote(auth)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "WALL_STUD_PARTITION", "dimensions": {"length": 10},  # height missing
        })
        self.assertEqual(r.status_code, 422)

    def test_quote_export_pdf_is_an_attachment(self):
        auth = self.register("export@acme.com")
        qid = self.make_quote(auth)
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Shingles", "item_type": "material", "quantity": 10,
             "unit": "m2", "unit_cost": 20, "markup_percent": 20},
        ])
        r = self.client.get(f"/api/quotes/{qid}/export-pdf", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertIn("attachment", r.headers.get("content-disposition", ""))
        self.assertTrue(r.content.startswith(b"%PDF"))
        # The url endpoint still serves the download-history record.
        r = self.client.get(f"/api/quotes/{qid}/pdf", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        pdf_url = r.json()["url"]
        self.assertTrue(pdf_url.startswith("/static/exports/pdf/"))
        # Clean the generated PDFs off the working tree (they're gitignored).
        export_dir = os.path.join(_STATIC, "exports", "pdf")
        for name in os.listdir(export_dir):
            if name.startswith(f"quote-{qid}-") and name.endswith(".pdf"):
                try:
                    os.remove(os.path.join(export_dir, name))
                except OSError:
                    pass


    # ------------------------------------------------------------------
    # Parser -> Quote pipeline
    # ------------------------------------------------------------------
    @staticmethod
    def parsed_row(label, description, qty, unit_cost, include=True, material=True,
                   trade="Carpentry", needs_review=False, margin=20):
        return {
            "#": label, "Include": include, "Trade": trade, "Section": "Scope",
            "Description": description, "Qty": qty, "Unit": "m2",
            "Unit Cost": unit_cost, "Margin %": margin, "Material": material,
            "Insurance RCV": 0.0, "Insurance O&P": None, "Code Cite": False,
            "Needs Review": needs_review, "Review Note": "", "Recoverable Depreciation": 0.0,
        }

    def test_from_parse_creates_quote_with_title_and_lines(self):
        auth = self.register("parse@acme.com")
        r = self.client.post("/api/quotes/from-parse", headers=auth, json={
            "claim_fields": {"Policyholder": "Jane Doe", "Claim number": "CL-100",
                             "Insurance Company": "State Farm"},
            "rows": [
                self.parsed_row("L1", "Roof shingles", 10, 25.0),
                self.parsed_row("L2", "Ridge cap", 4, 30.0),
            ],
        })
        self.assertEqual(r.status_code, 201, r.text)
        quote = r.json()
        self.assertEqual(quote["title"], "Estimate — Jane Doe (Claim CL-100)")
        self.assertEqual(quote["line_count"], 2)
        self.assertAlmostEqual(quote["subtotal"], 444.0, places=2)  # (10*25*1.2)+(4*30*1.2)
        line = quote["lines"][0]
        self.assertEqual(line["description"], "Roof shingles")
        self.assertEqual(line["trade"], "Carpentry")
        self.assertEqual(line["item_type"], "material")
        self.assertEqual(line["quantity"], 10)
        self.assertEqual(line["unit_cost"], 25.0)
        self.assertEqual(line["markup_percent"], 20)

    def test_from_parse_skips_unincluded_and_marks_review(self):
        auth = self.register("parse2@acme.com")
        r = self.client.post("/api/quotes/from-parse", headers=auth, json={
            "claim_fields": {},
            "rows": [
                self.parsed_row("L1", "Keep me", 1, 10.0, include=True),
                self.parsed_row("L2", "Drop me", 1, 10.0, include=False),
                self.parsed_row("L3", "Check this", 1, 10.0, include=True, needs_review=True),
            ],
        })
        self.assertEqual(r.status_code, 201, r.text)
        quote = r.json()
        self.assertEqual(quote["line_count"], 2)
        self.assertEqual(quote["lines"][0]["description"], "Keep me")
        self.assertTrue(quote["lines"][1]["description"].startswith("⚠ "))

    def test_from_parse_client_ownership(self):
        auth = self.register("parse3@acme.com")
        r = self.client.post("/api/quotes/from-parse", headers=auth,
                             json={"rows": [], "client_id": 9999})
        self.assertEqual(r.status_code, 404)

    def test_from_parse_orgless_user_rejected(self):
        auth = self.register("parse4@acme.com", org="")
        r = self.client.post("/api/quotes/from-parse", headers=auth,
                             json={"rows": [self.parsed_row("L1", "X", 1, 1.0)]})
        self.assertEqual(r.status_code, 400)

    def test_cross_tenant_quote_is_forbidden(self):
        auth_a = self.register("tenant-a@acme.com", org="Org A")
        auth_b = self.register("tenant-b@acme.com", org="Org B")
        qid = self.make_quote(auth_a)
        r = self.client.get(f"/api/quotes/{qid}", headers=auth_b)
        self.assertEqual(r.status_code, 403)
        r = self.client.get(f"/api/quotes/{qid}", headers=auth_a)
        self.assertEqual(r.status_code, 200)

    def test_parse_pdf_endpoint_returns_rows_and_claim_fields(self):
        auth = self.register("parsepdf@acme.com")
        with open(os.path.join(_FIXTURES, "synthetic_sample.pdf"), "rb") as fh:
            r = self.client.post("/api/parse", headers=auth,
                                 files={"file": ("sample.pdf", fh, "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIsInstance(body["rows"], list)
        self.assertGreaterEqual(len(body["rows"]), 1)
        self.assertIn("claim_fields", body)
        self.assertIn("warnings", body)

    # ------------------------------------------------------------------
    # Trade catalog autocorrect
    # ------------------------------------------------------------------
    def test_catalog_autocorrect_returns_canonical_items(self):
        auth = self.register("catalog@acme.com")
        r = self.client.get("/api/catalog/autocorrect", params={"q": "dryw"}, headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        results = r.json()["results"]
        hit = next((res for res in results if res["canonical_name"] == "Drywall board (12.5mm)"), None)
        self.assertIsNotNone(hit, results)
        self.assertEqual(hit["trade"], "Drywall")
        self.assertEqual(hit["unit"], "m2")
        self.assertGreater(hit["default_unit_cost"], 0)
        self.assertEqual(hit["default_trade_type"], "material")

    def test_catalog_autocorrect_short_query_returns_empty(self):
        auth = self.register("catalog2@acme.com")
        r = self.client.get("/api/catalog/autocorrect", params={"q": "d"}, headers=auth)
        self.assertEqual(r.json()["results"], [])

    def test_catalog_autocorrect_matches_slang_aliases(self):
        auth = self.register("catalog3@acme.com")
        r = self.client.get("/api/catalog/autocorrect", params={"q": "sheetrock"}, headers=auth)
        self.assertTrue(any(res["canonical_name"] == "Drywall board (12.5mm)"
                            for res in r.json()["results"]), r.text)
        r = self.client.get("/api/catalog/autocorrect", params={"q": "2x4"}, headers=auth)
        self.assertTrue(any(res["canonical_name"] == "Timber wall stud (2x4)"
                            for res in r.json()["results"]), r.text)

    def test_catalog_autocorrect_dedupes_synonyms(self):
        auth = self.register("catalog4@acme.com")
        r = self.client.get("/api/catalog/autocorrect", params={"q": "tile", "limit": 25}, headers=auth)
        names = [res["canonical_name"] for res in r.json()["results"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("Floor tile (ceramic)", names)

    def test_catalog_autocorrect_matches_canonical_names_too(self):
        auth = self.register("catalog5@acme.com")
        r = self.client.get("/api/catalog/autocorrect", params={"q": "concrete block"}, headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(any(res["canonical_name"] == "Concrete blocks (100mm)"
                            for res in r.json()["results"]), r.text)

    def test_catalog_seed_covers_standard_materials(self):
        auth = self.register("catalog6@acme.com")
        for q, expected in [
            ("plasterboard", "Drywall board (12.5mm)"),
            ("2x4", "Timber wall stud (2x4)"),
            ("screws", "Drywall screws (box of 200)"),
            ("adhesive", "Tile adhesive"),
            ("grout", "Tile grout (5kg)"),
            ("skim", "Skim coat plaster"),
        ]:
            r = self.client.get("/api/catalog/autocorrect", params={"q": q}, headers=auth)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(any(res["canonical_name"] == expected
                                for res in r.json()["results"]),
                            (q, r.json()["results"]))

    # ------------------------------------------------------------------
    # /api/catalog/calculate-assembly
    # ------------------------------------------------------------------
    def test_calculate_assembly_stud_wall(self):
        auth = self.register("calcapi@acme.com")
        r = self.client.post("/api/catalog/calculate-assembly", headers=auth, json={
            "assembly_type": "stud_wall", "length": 4, "height": 2.4,
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["assembly_type"], "stud_wall")
        self.assertEqual(len(body["lines"]), 5)
        first = body["lines"][0]
        self.assertEqual(first["description"], "70mm Metal/Timber Studs (2.4m)")
        self.assertEqual(first["item_type"], "material")
        self.assertEqual(first["trade"], "Carpentry")
        self.assertEqual(first["unit"], "pcs")
        self.assertEqual(first["quantity"], 9)
        self.assertAlmostEqual(first["subtotal"], 48.60, places=2)
        self.assertGreater(body["total"], 0)

    def test_calculate_assembly_floor_tiling(self):
        auth = self.register("calcapi2@acme.com")
        r = self.client.post("/api/catalog/calculate-assembly", headers=auth, json={
            "assembly_type": "floor_tiling", "length": 4, "width": 3,
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["lines"]), 4)
        self.assertEqual([l["item_type"] for l in body["lines"]],
                         ["material", "material", "material", "labor"])

    def test_calculate_assembly_errors(self):
        auth = self.register("calcapi3@acme.com")
        r = self.client.post("/api/catalog/calculate-assembly", headers=auth, json={
            "assembly_type": "does_not_exist", "length": 4,
        })
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/catalog/calculate-assembly", headers=auth, json={
            "assembly_type": "stud_wall", "length": 4,  # height missing
        })
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()

