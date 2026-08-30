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

    def test_google_auth_not_configured(self):
        # With no GOOGLE_CLIENT_ID set the endpoint must refuse cleanly so the
        # feature stays opt-in until the operator configures Google.
        r = self.client.post("/api/auth/google", json={"credential": "not-a-real-token"})
        self.assertEqual(r.status_code, 503, r.text)

    def test_google_auth_signup_then_login(self):
        from unittest import mock

        import app.routers.auth as auth_router

        profile = {
            "sub": "112233445566778899",
            "email": "google.user@example.com",
            "email_verified": True,
            "name": "Google User",
        }
        with mock.patch.object(auth_router, "GOOGLE_CLIENT_ID", "test.apps.googleusercontent.com"), \
             mock.patch.object(auth_router, "verify_google_credential", return_value=profile):
            # First call = sign-up: self-provisions account + org, issues JWT.
            r = self.client.post("/api/auth/google", json={"credential": "jwt"})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["access_token"])
            self.assertEqual(body["user"]["email"], "google.user@example.com")
            self.assertEqual(body["user"]["role"], "owner")
            token = body["access_token"]
            # The issued JWT is a normal BuildUpQuote token.
            r = self.client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["email"], "google.user@example.com")
            # Organization self-provisioned from the Google display name.
            r = self.client.get("/api/organization/me", headers={"Authorization": "Bearer " + token})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["name"], "Google User")
            # Second call = sign-in: reuses the same account (no duplicates).
            r = self.client.post("/api/auth/google", json={"credential": "jwt"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["user"]["id"], body["user"]["id"])

    def test_google_auth_rejects_unverified_email(self):
        from unittest import mock

        import app.routers.auth as auth_router

        profile = {"sub": "1", "email": "unverified@example.com", "email_verified": False, "name": "X"}
        with mock.patch.object(auth_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(auth_router, "verify_google_credential", return_value=profile):
            r = self.client.post("/api/auth/google", json={"credential": "jwt"})
            self.assertEqual(r.status_code, 401, r.text)
            self.assertIn("not verified", r.json()["detail"])

    def test_google_auth_rejects_bad_credential(self):
        from unittest import mock

        import app.routers.auth as auth_router

        def boom(credential):
            raise ValueError("Invalid Google credential")

        with mock.patch.object(auth_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(auth_router, "verify_google_credential", side_effect=boom):
            r = self.client.post("/api/auth/google", json={"credential": "junk"})
            self.assertEqual(r.status_code, 401, r.text)
            self.assertEqual(r.json()["detail"], "Invalid Google credential")

    def test_auth_pages_google_block_is_well_formed(self):
        """Regression: the Google button block must sit OUTSIDE the form-submit
        script. An earlier version nested it inside, which closed the form
        script at the first `</script>` and turned the swallowed GIS `<script
        src>` tag into a JS SyntaxError -- killing both login and register."""
        from unittest import mock

        import app.routers.pages as pages_router

        cid = "test.apps.googleusercontent.com"
        with mock.patch.object(pages_router, "GOOGLE_CLIENT_ID", cid):
            for path, form_id in (("/login", "loginForm"), ("/register", "registerForm")):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, r.text)
                html = r.text
                # Locate the form handler script and confirm it is closed
                # before any injected Google script tag appears.
                handler_at = html.index(f"getElementById('{form_id}').addEventListener")
                block_open = html.rindex("<script>", 0, handler_at)
                block_close = html.index("</script>", handler_at)
                # Everything between the opening and closing tags of the form
                # handler script must be plain JS -- no swallowed HTML tags.
                self.assertNotIn("<script", html[block_open + len("<script>"):block_close])
                gis = '<script src="https://accounts.google.com/gsi/client"'
                self.assertIn(gis, html)
                self.assertGreater(html.index(gis), block_close)
                self.assertIn("initGoogleButton", html)
                self.assertIn(cid, html)

    def test_quick_parse_lead_creates_client(self):
        auth = self.register("qplead@acme.com")
        r = self.client.post("/api/clients/quick-parse-lead", headers=auth, json={
            "raw_text": "Name: Jane Doe\n+44 7700 900123\njane@example.com\nSite: 123 High St, Manchester",
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["name"], "Jane Doe")
        self.assertEqual(body["email"], "jane@example.com")
        self.assertEqual(body["phone"], "+44 7700 900123")
        self.assertEqual(body["site_address"], "123 High St, Manchester")

    def test_quick_parse_lead_is_idempotent(self):
        auth = self.register("qpidem@acme.com")
        payload = {"raw_text": "Bob Smith\nbob@example.com\n555-010-2222"}
        r1 = self.client.post("/api/clients/quick-parse-lead", headers=auth, json=payload)
        self.assertEqual(r1.status_code, 200, r1.text)
        r2 = self.client.post("/api/clients/quick-parse-lead", headers=auth, json=payload)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["id"], r1.json()["id"])
        r = self.client.get("/api/clients", headers=auth)
        self.assertEqual(len(r.json()), 1)

    def test_quick_parse_lead_rejects_garbage(self):
        auth = self.register("qpgarbage@acme.com")
        r = self.client.post("/api/clients/quick-parse-lead", headers=auth, json={"raw_text": "123456"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("contact details", r.json()["detail"])

    def test_quick_parse_lead_requires_org(self):
        r = self.client.post("/api/auth/register", json={
            "email": "noorg@w.com", "password": "pw12345678",
        })
        self.assertEqual(r.status_code, 201, r.text)
        token = r.json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        r = self.client.post("/api/clients/quick-parse-lead", headers=headers,
                             json={"raw_text": "Name: X\nx@y.com"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_clients_page_renders_sync_hub(self):
        """The clients page must render the 1-Click Sync Hub UI: phone picker,
        quick-paste modal, file import modal, and add-client modal."""
        r = self.client.get("/clients")
        self.assertEqual(r.status_code, 200, r.text)
        html = r.text
        for needle in ("clientManager", "pickNativeContacts", "navigator.contacts",
                       "openQuickPasteModal", "openImportFileModal", "openAddClientModal",
                       "quick-parse-lead", "import-file", "site_address"):
            self.assertIn(needle, html)

    # ------------------------------------------------------------------
    # Google Contacts sync (People API)
    # ------------------------------------------------------------------
    def test_google_contacts_unconfigured_answers_503(self):
        auth = self.register("gc503@acme.com")
        r = self.client.get("/api/auth/google/contacts/auth", headers=auth)
        self.assertEqual(r.status_code, 503, r.text)
        r = self.client.post("/api/clients/import-google-contacts", headers=auth)
        self.assertEqual(r.status_code, 503, r.text)

    def test_google_contacts_auth_url_when_configured(self):
        import urllib.parse
        from unittest import mock

        import app.routers.auth as auth_router

        auth = self.register("gcaurl@acme.com")
        with mock.patch.object(auth_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(auth_router, "GOOGLE_CLIENT_SECRET", "secret"):
            r = self.client.get("/api/auth/google/contacts/auth", headers=auth)
            self.assertEqual(r.status_code, 200, r.text)
            url = r.json()["auth_url"]
            self.assertIn("https://accounts.google.com/o/oauth2/v2/auth?", url)
            self.assertIn("access_type=offline", url)
            self.assertIn("prompt=consent", url)
            # The redirect URI must be the exact registered https URL -- never
            # http (that's what caused the redirect_uri_mismatch behind Caddy).
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertEqual(
                query["redirect_uri"],
                ["https://glennwestman.com/api/auth/google/contacts/callback"],
            )

    def test_google_contacts_import_not_connected(self):
        from unittest import mock

        import app.routers.clients as clients_router

        auth = self.register("gcnc@acme.com")
        with mock.patch.object(clients_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(clients_router, "GOOGLE_CLIENT_SECRET", "secret"):
            r = self.client.post("/api/clients/import-google-contacts", headers=auth)
            self.assertEqual(r.status_code, 400, r.text)
            self.assertIn("Connect", r.json()["detail"])

    def test_google_contacts_import_creates_clients(self):
        from unittest import mock

        from app import models
        from app.database import SessionLocal
        from app.services import google_contacts

        import app.routers.clients as clients_router

        auth = self.register("gcimport@acme.com")
        # Attach OAuth tokens directly to the user row.
        session = SessionLocal()
        user = session.query(models.User).filter(models.User.email == "gcimport@acme.com").first()
        user.google_access_token = "access-1"
        user.google_refresh_token = "refresh-1"
        session.commit()
        session.close()

        people = [{
            "names": [{"displayName": "Google Jane"}],
            "emailAddresses": [{"value": "gjane@example.com"}],
            "phoneNumbers": [{"value": "+44 7700 900111"}],
            "addresses": [{"formattedValue": "9 Google Rd"}],
        }]
        with mock.patch.object(clients_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(clients_router, "GOOGLE_CLIENT_SECRET", "secret"), \
             mock.patch.object(google_contacts, "fetch_contacts",
                               return_value={"connections": people}):
            r = self.client.post("/api/clients/import-google-contacts", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["clients"][0]["name"], "Google Jane")
        self.assertEqual(body["clients"][0]["site_address"], "9 Google Rd")

    def test_google_contacts_import_refreshes_stale_token(self):
        from unittest import mock

        from app import models
        from app.database import SessionLocal
        from app.services import google_contacts

        import app.routers.clients as clients_router

        auth = self.register("gcrefresh@acme.com")
        session = SessionLocal()
        user = session.query(models.User).filter(models.User.email == "gcrefresh@acme.com").first()
        user.google_access_token = "stale"
        user.google_refresh_token = "refresh-1"
        session.commit()
        session.close()

        responses = iter([
            google_contacts.GoogleContactsError("expired", status=401),
            {"connections": [{"emailAddresses": [{"value": "fresh@example.com"}]}]},
        ])

        def fake_fetch(token):
            value = next(responses)
            if isinstance(value, google_contacts.GoogleContactsError):
                raise value
            return value

        with mock.patch.object(clients_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(clients_router, "GOOGLE_CLIENT_SECRET", "secret"), \
             mock.patch.object(google_contacts, "fetch_contacts", side_effect=fake_fetch), \
             mock.patch.object(google_contacts, "refresh_access_token",
                               return_value={"access_token": "fresh-token"}):
            r = self.client.post("/api/clients/import-google-contacts", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["created"], 1)
        # The refreshed access token was persisted.
        session = SessionLocal()
        user = session.query(models.User).filter(models.User.email == "gcrefresh@acme.com").first()
        self.assertEqual(user.google_access_token, "fresh-token")
        session.close()

    def test_google_contacts_callback_stores_tokens(self):
        from unittest import mock

        from app import models
        from app.auth import ALGORITHM, SECRET_KEY
        from app.database import SessionLocal
        from app.services import google_contacts
        from jose import jwt

        import app.routers.auth as auth_router

        auth = self.register("gccb@acme.com")
        me = self.client.get("/api/auth/me", headers=auth).json()
        state = jwt.encode({"uid": me["id"]}, SECRET_KEY, algorithm=ALGORITHM)
        with mock.patch.object(auth_router, "GOOGLE_CLIENT_ID", "cid"), \
             mock.patch.object(auth_router, "GOOGLE_CLIENT_SECRET", "secret"), \
             mock.patch.object(google_contacts, "exchange_code",
                               return_value={"access_token": "at-1", "refresh_token": "rt-1"}):
            r = self.client.get(
                f"/api/auth/google/contacts/callback?code=abc123&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(r.status_code, 307, r.text)
        self.assertIn("/clients?google_import=1", r.headers["location"])
        session = SessionLocal()
        user = session.query(models.User).filter(models.User.email == "gccb@acme.com").first()
        self.assertEqual(user.google_access_token, "at-1")
        self.assertEqual(user.google_refresh_token, "rt-1")
        session.close()

    def test_google_contacts_callback_rejects_bad_state(self):
        r = self.client.get(
            "/api/auth/google/contacts/callback?code=x&state=garbage",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 307, r.text)
        self.assertIn("google_error=", r.headers["location"])

    # ------------------------------------------------------------------
    # Rate catalog management
    # ------------------------------------------------------------------
    def test_catalog_items_list_seeded(self):
        auth = self.register("catlist@acme.com")
        r = self.client.get("/api/catalog/items", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()
        self.assertGreater(len(items), 0)
        first = items[0]
        for field in ("id", "trade", "canonical_name", "unit",
                      "default_unit_cost", "default_trade_type"):
            self.assertIn(field, first)

    def test_catalog_item_create_and_delete(self):
        auth = self.register("catcrud@acme.com")
        r = self.client.post("/api/catalog/items", headers=auth, json={
            "trade": "Plumbing", "canonical_name": "15mm Copper Pipe 3m",
            "unit": "length", "default_unit_cost": 12.5, "default_trade_type": "Material",
        })
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["canonical_name"], "15mm Copper Pipe 3m")
        self.assertEqual(body["default_unit_cost"], 12.5)
        # Duplicate (case-insensitive) is rejected cleanly.
        r = self.client.post("/api/catalog/items", headers=auth, json={
            "trade": "Plumbing", "canonical_name": "15mm copper pipe 3m",
            "unit": "length", "default_unit_cost": 12.5,
        })
        self.assertEqual(r.status_code, 400, r.text)
        # Delete removes it.
        r = self.client.delete(f"/api/catalog/items/{body['id']}", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"], True)
        r = self.client.get("/api/catalog/items", headers=auth)
        names = [i["canonical_name"] for i in r.json()]
        self.assertNotIn("15mm Copper Pipe 3m", names)

    def test_catalog_page_renders_manager(self):
        r = self.client.get("/catalog")
        self.assertEqual(r.status_code, 200, r.text)
        html = r.text
        for needle in ("catalogManager", "/api/catalog/items", "openNewItemModal",
                       "filterCatalog", "default_trade_type", "Add Rate Item"):
            self.assertIn(needle, html)

    # ------------------------------------------------------------------
    # Dashboard analytics
    # ------------------------------------------------------------------
    def test_dashboard_stats_empty_for_new_org(self):
        auth = self.register("dashempty@acme.com")
        r = self.client.get("/api/dashboard/stats", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["stats"]["active_quotes_count"], 0)
        self.assertEqual(body["stats"]["open_quotes_count"], 0)
        self.assertEqual(body["stats"]["pipeline_total"], "0.00")
        self.assertEqual(body["stats"]["open_quotes_total"], "0.00")
        self.assertEqual(body["stats"]["pending_deposits_count"], 0)
        self.assertEqual(body["stats"]["pending_deposits_total"], "0.00")
        self.assertEqual(body["stats"]["currency"], "$")  # USD default
        self.assertEqual(body["recent_quotes"], [])

    def test_dashboard_stats_computes_analytics(self):
        auth = self.register("dashstat@acme.com")
        line = [{"description": "Shingles", "item_type": "material", "quantity": 10,
                 "unit": "m2", "unit_cost": 20, "markup_percent": 20}]
        q1 = self.make_quote(auth, "Draft roof")
        self.client.put(f"/api/quotes/{q1}/lines", headers=auth, json=line)
        q2 = self.make_quote(auth, "Sent gutter")
        self.client.patch(f"/api/quotes/{q2}", headers=auth, json={"status": "sent"})
        q3 = self.make_quote(auth, "Accepted roof")
        self.client.put(f"/api/quotes/{q3}/lines", headers=auth, json=line)
        self.client.patch(f"/api/quotes/{q3}", headers=auth, json={
            "status": "accepted",
            "payment_schedule": [
                {"label": "Deposit", "percent": 50},
                {"label": "Rough-in", "percent": 50},
            ],
        })

        r = self.client.get("/api/dashboard/stats", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        stats = body["stats"]
        self.assertEqual(stats["active_quotes_count"], 2)  # draft + sent
        self.assertEqual(stats["open_quotes_count"], 2)
        self.assertEqual(stats["accepted_quotes_count"], 1)
        self.assertEqual(stats["active_jobs_count"], 1)
        self.assertEqual(stats["win_rate"], 50.0)  # 1 accepted of 2 decided
        self.assertGreater(float(stats["pipeline_total"]), 0)
        self.assertGreater(float(stats["open_quotes_total"]), 0)
        self.assertGreater(float(stats["won_revenue"]), 0)
        self.assertGreater(stats["avg_margin"], 0)
        # Accepted job with an unreleased 50% deposit -> 1 pending deposit.
        self.assertEqual(stats["pending_deposits_count"], 1)
        self.assertAlmostEqual(float(stats["pending_deposits_total"]),
                               0.5 * float(stats["won_revenue"]), places=2)
        recent = body["recent_quotes"]
        self.assertEqual(len(recent), 3)
        self.assertEqual(recent[0]["status"], "Accepted")  # newest first
        for q in recent:
            self.assertIn("id", q)
            self.assertIn("title", q)
            self.assertIn("total_amount", q)

    def test_dashboard_stats_released_deposit_not_pending(self):
        auth = self.register("dashpaid@acme.com")
        qid = self.make_quote(auth, "Paid job")
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[{
            "description": "Paint", "item_type": "material",
            "quantity": 1, "unit": "gal", "unit_cost": 100, "markup_percent": 10,
        }])
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "status": "accepted",
            "payment_schedule": [{"label": "Deposit", "percent": 100, "released": True}],
        })
        r = self.client.get("/api/dashboard/stats", headers=auth)
        stats = r.json()["stats"]
        self.assertEqual(stats["active_jobs_count"], 1)
        self.assertEqual(stats["pending_deposits_count"], 0)
        self.assertEqual(stats["pending_deposits_total"], "0.00")

    def test_dashboard_stats_range_filter(self):
        from datetime import datetime, timedelta, timezone
        from app.database import SessionLocal
        from app import models

        auth = self.register("dashrange@acme.com")
        qid = self.make_quote(auth, "Old quote")
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"status": "sent"})

        # Backdate the quote past the month window.
        db = SessionLocal()
        try:
            row = db.query(models.Quote).filter(models.Quote.id == qid).first()
            row.created_at = datetime.now(timezone.utc) - timedelta(days=45)
            db.commit()
        finally:
            db.close()

        all_time = self.client.get("/api/dashboard/stats", headers=auth).json()
        self.assertEqual(all_time["stats"]["active_quotes_count"], 1)
        self.assertEqual(len(all_time["recent_quotes"]), 1)

        for rng in ("month", "week"):
            resp = self.client.get(f"/api/dashboard/stats?range={rng}", headers=auth)
            self.assertEqual(resp.status_code, 200, resp.text)
            stats = resp.json()["stats"]
            self.assertEqual(stats["active_quotes_count"], 0, rng)
            self.assertEqual(resp.json()["recent_quotes"], [], rng)

    def test_dashboard_page_renders_analytics(self):
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 200, r.text)
        html = r.text
        for needle in ("dashboardAnalytics", "/api/dashboard/stats", "Recent proposals",
                       "open_quotes_count", "active_jobs_count", "pending_deposits_count",
                       "Week to date", "statusPill", "statusLabel", "appShell",
                       "sidebarCollapsed", "Jobs / Projects", "Search quotes, clients", "recent_quotes",
                       "quotes?view=open", "BQ_SHELL_STATS_CHANGED"):
            self.assertIn(needle, html)

    def test_app_shell_renders_global_search_launcher(self):
        """Every authenticated page carries the Cmd+K launcher + create menu."""
        r = self.client.get("/quotes")
        self.assertEqual(r.status_code, 200, r.text)
        html = r.text
        for needle in ("/static/js/global_search.js", "bq-search-modal", "bq-search-input",
                       "bq-search-results", "New Invoice", "New Client", "createOpen",
                       "chevron-right", "⌘K"):
            self.assertIn(needle, html)
        # The launcher script ships the fuzzy engine, keyboard handling, and
        # the cross-tab sync channel.
        js = self.client.get("/static/js/global_search.js")
        self.assertEqual(js.status_code, 200, js.text)
        for needle in ("window.BQSearch", "BroadcastChannel('bq_updates')",
                       "BQ_SHELL_STATS_CHANGED", "metaKey", "searchIndex"):
            self.assertIn(needle, js.text)
        # Voice normalizer loads before the parser it feeds.
        self.assertLess(html.index("/static/js/voice_normalizer.js"),
                        html.index("/static/js/smart_voice_parser.js"))
        # The parser ships the spec-alias + parametric assembly extraction.
        parser = self.client.get("/static/js/smart_voice_parser.js").text
        self.assertIn("parseVoiceInput: processConversationalVoice", parser)
        self.assertIn("ASSEMBLY_RE", parser)
        self.assertIn("insertAssembly", parser)

    def test_login_page_has_no_shell_api_loop(self):
        """appShell runs on /login and /register too, so its badge refresh must
        use a plain fetch — BQ.api() redirects to /login on a 401, which would
        reload the auth pages forever."""
        for path in ("/login", "/register"):
            html = self.client.get(path).text
            self.assertIn("appShell()", html)
            self.assertIn("fetch('/api/dashboard/stats'", html)
            self.assertNotIn("BQ.api('/api/dashboard/stats')", html)

    # ------------------------------------------------------------------
    # Production hardening: password toggle, security/caching headers
    # ------------------------------------------------------------------
    def test_auth_pages_have_password_visibility_toggle(self):
        for path in ("/login", "/register"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, r.text)
            html = r.text
            self.assertIn('x-data="{ showPassword: false }"', html)
            self.assertIn("showPassword ? 'text' : 'password'", html)
            self.assertIn('@click="showPassword = !showPassword"', html)
            self.assertIn('data-lucide="eye"', html)
            self.assertIn('data-lucide="eye-off"', html)
            self.assertIn('type="button"', html)  # toggle never submits the form

    def test_security_and_caching_headers(self):
        r = self.client.get("/")
        self.assertEqual(r.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(r.headers.get("x-frame-options"), "SAMEORIGIN")
        self.assertIn("strict-origin-when-cross-origin", r.headers.get("referrer-policy", ""))
        r = self.client.get("/static/js/app.js")
        self.assertIn("max-age=", r.headers.get("cache-control", ""))

    # ------------------------------------------------------------------
    # Public 1-click quote approval
    # ------------------------------------------------------------------
    def _public_quote_fixture(self):
        from app import models
        from app.database import SessionLocal

        auth = self.register(f"pub{id(self)}@acme.com")
        qid = self.make_quote(auth, "Roof and Gutter Proposal")
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Shingles", "item_type": "material", "quantity": 10,
             "unit": "m2", "unit_cost": 20, "markup_percent": 20},
        ])
        session = SessionLocal()
        quote = session.query(models.Quote).filter(models.Quote.id == qid).first()
        public_uuid = quote.public_uuid
        session.close()
        self.assertTrue(public_uuid, "new quotes must get a public_uuid")
        return public_uuid

    def test_public_quote_view_resolves(self):
        public_uuid = self._public_quote_fixture()
        r = self.client.get(f"/view/quote/{public_uuid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Roof and Gutter Proposal", r.text)
        self.assertIn("Accept Proposal", r.text)
        self.assertIn("sigPad", r.text)

    def test_public_quote_view_unknown_link(self):
        r = self.client.get("/view/quote/no-such-uuid")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("isn't valid", r.text)

    def test_public_quote_accept_flow(self):
        from app import models
        from app.database import SessionLocal

        public_uuid = self._public_quote_fixture()
        # Missing signature -> 400.
        r = self.client.post(f"/api/public/quotes/{public_uuid}/accept",
                             json={"signature_data": "", "client_name": "Jane"})
        self.assertEqual(r.status_code, 400, r.text)
        # Accept with signature + name + email and audit headers.
        r = self.client.post(
            f"/api/public/quotes/{public_uuid}/accept",
            json={"signature_data": "data:image/png;base64,AAAA",
                  "client_name": "Jane Doe", "signer_email": "jane@example.com"},
            headers={"X-Forwarded-For": "203.0.113.7", "User-Agent": "AuditTest/1.0"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "accepted")
        self.assertEqual(r.json()["signer_ip"], "203.0.113.7")
        # Persisted (full audit trail).
        session = SessionLocal()
        quote = session.query(models.Quote).filter(
            models.Quote.public_uuid == public_uuid).first()
        self.assertEqual(quote.status, "accepted")
        self.assertEqual(quote.signed_by, "Jane Doe")
        self.assertEqual(quote.signer_email, "jane@example.com")
        self.assertEqual(quote.signer_ip, "203.0.113.7")
        self.assertEqual(quote.signer_user_agent, "AuditTest/1.0")
        self.assertEqual(quote.client_signature, "data:image/png;base64,AAAA")
        self.assertIsNotNone(quote.accepted_at)
        session.close()
        # The public page now shows the accepted state.
        r = self.client.get(f"/view/quote/{public_uuid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Proposal Accepted", r.text)
        self.assertIn("Download Signed Proposal", r.text)
        # Re-accepting is idempotent.
        r = self.client.post(f"/api/public/quotes/{public_uuid}/accept",
                             json={"signature_data": "x", "client_name": "y"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["already"])

    def test_public_quote_accept_locks_edits(self):
        from app import models
        from app.database import SessionLocal

        auth = self.register(f"lock{id(self)}@acme.com")
        qid = self.make_quote(auth, "Locked proposal")
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Shingles", "item_type": "material", "quantity": 10,
             "unit": "m2", "unit_cost": 20, "markup_percent": 20},
        ])
        session = SessionLocal()
        pub = session.query(models.Quote).filter(models.Quote.id == qid).first().public_uuid
        line_id = session.query(models.QuoteLineItem).filter(
            models.QuoteLineItem.quote_id == qid).first().id
        session.close()

        r = self.client.post(f"/api/public/quotes/{pub}/accept",
                             json={"signature_data": "data:image/png;base64,AAAA",
                                   "client_name": "Jane Doe"})
        self.assertEqual(r.status_code, 200, r.text)

        # Every mutation is rejected once accepted.
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"title": "Changed"})
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "New", "item_type": "labor", "quantity": 1, "unit": "hr",
             "unit_cost": 1, "markup_percent": 0}])
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.delete(f"/api/quotes/{qid}/lines/{line_id}", headers=auth)
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.delete(f"/api/quotes/{qid}", headers=auth)
        self.assertEqual(r.status_code, 400, r.text)
        r = self.client.post(f"/api/quotes/{qid}/apply-assembly", headers=auth, json={
            "code": "WALL_STUD_PARTITION", "dimensions": {"length": 10, "height": 3}})
        self.assertEqual(r.status_code, 400, r.text)

        # The signed scope is untouched.
        r = self.client.get(f"/api/quotes/{qid}", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        lines = r.json()["lines"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["description"], "Shingles")
        self.assertEqual(r.json()["title"], "Locked proposal")

    def test_public_quote_download_pdf(self):
        import io

        import pdfplumber

        public_uuid = self._public_quote_fixture()
        self.client.post(
            f"/api/public/quotes/{public_uuid}/accept",
            json={"signature_data": "data:image/png;base64,AAAA",
                  "client_name": "Jane Doe"},
            headers={"X-Forwarded-For": "203.0.113.7", "User-Agent": "AuditTest/1.0"},
        )
        r = self.client.get(f"/view/quote/{public_uuid}/download-pdf")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        # The audit stamp is rendered in the signed PDF.
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        self.assertIn("Digitally Accepted", text)
        self.assertIn("Jane Doe", text)
        self.assertIn("203.0.113.7", text)

    # ------------------------------------------------------------------
    # Master contract management
    # ------------------------------------------------------------------
    def test_organization_master_contract_save_and_upload(self):
        auth = self.register("contract@acme.com")
        # Save the master contract text via the profile endpoint.
        r = self.client.put("/api/organization/me", headers=auth, json={
            "name": "Contract Co",
            "master_contract_text": "1. Scope\n2. Payment {{project_total}}\n3. Warranty\n4. Cancellation",
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("1. Scope", body["master_contract_text"])
        # Upload a master contract file.
        r = self.client.post("/api/organization/contract-file", headers=auth,
                             files={"file": ("terms.pdf", b"%PDF-1.4 fake contract", "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        url = r.json()["master_contract_pdf_url"]
        self.assertTrue(url.startswith("/static/uploads/contracts/"), url)
        # A DOCX is accepted too; a .txt is not.
        r = self.client.post("/api/organization/contract-file", headers=auth,
                             files={"file": ("terms.txt", b"no", "text/plain")})
        self.assertEqual(r.status_code, 400, r.text)

    def test_quote_contract_fields_persist(self):
        auth = self.register("qcontract@acme.com")
        qid = self.make_quote(auth)
        r = self.client.patch(f"/api/quotes/{qid}", headers=auth, json={
            "include_contract": False,
            "custom_contract_override": "Project-specific clauses for this job.",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["include_contract"])
        self.assertEqual(r.json()["custom_contract_override"],
                         "Project-specific clauses for this job.")
        detail = self.client.get(f"/api/quotes/{qid}", headers=auth).json()
        self.assertFalse(detail["include_contract"])
        self.assertEqual(detail["custom_contract_override"],
                         "Project-specific clauses for this job.")

    def test_quote_pdf_contains_contract_page(self):
        import io

        import pdfplumber

        auth = self.register("pdfcontract@acme.com")
        # Create a client and attach them so {{client_name}} substitutes.
        client = self.client.post("/api/clients", headers=auth, json={
            "name": "Contract Client", "site_address": "7 Contract Ave",
        }).json()
        self.client.put("/api/organization/me", headers=auth, json={
            "name": "Pdf Contract Co",
            "master_contract_text": (
                "Standard terms. Client: {{client_name}} | Total: {{project_total}} "
                "| Site: {{site_address}} | Date: {{date}}"
            ),
        })
        qid = self.make_quote(auth, "Contract proposal")
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"client_id": client["id"]})
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Shingles", "item_type": "material", "quantity": 10,
             "unit": "m2", "unit_cost": 20, "markup_percent": 20},
        ])
        r = self.client.get(f"/api/quotes/{qid}/export-pdf", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        self.assertIn("Contract Agreement & Terms of Service", text)
        self.assertIn("Contract Client", text)
        self.assertIn("$240.00", text)          # {{project_total}} substituted
        self.assertIn("7 Contract Ave", text)   # {{site_address}} substituted
        # Toggling include_contract off removes the contract page.
        self.client.patch(f"/api/quotes/{qid}", headers=auth, json={"include_contract": False})
        r = self.client.get(f"/api/quotes/{qid}/export-pdf", headers=auth)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text2 = "\n".join((p.extract_text() or "") for p in pdf.pages)
        self.assertNotIn("Contract Agreement & Terms of Service", text2)

    def test_public_view_contract_accordion(self):
        from app import models
        from app.database import SessionLocal

        auth = self.register("pubcontract@acme.com")
        self.client.put("/api/organization/me", headers=auth, json={
            "name": "Pub Contract Co",
            "master_contract_text": "Binding terms for {{client_name}} — total {{project_total}}.",
        })
        qid = self.make_quote(auth)
        self.client.put(f"/api/quotes/{qid}/lines", headers=auth, json=[
            {"description": "Shingles", "item_type": "material", "quantity": 10,
             "unit": "m2", "unit_cost": 20, "markup_percent": 20},
        ])
        session = SessionLocal()
        pub = session.query(models.Quote).filter(models.Quote.id == qid).first().public_uuid
        session.close()
        r = self.client.get(f"/view/quote/{pub}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Contract Agreement &amp; Terms of Service", r.text)
        self.assertIn("Binding terms for", r.text)
        self.assertIn("$240.00", r.text)  # public view defaults to USD ($)

    def test_settings_and_builder_contract_ui(self):
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200, r.text)
        for needle in ("Master Contract", "contractTokens", "insertToken",
                       "contract-file", "master_contract_text"):
            self.assertIn(needle, r.text)
        r = self.client.get("/quotes/new")
        self.assertEqual(r.status_code, 200, r.text)
        for needle in ("Attach Master Contract", "toggleContract", "include_contract",
                       "custom_contract_override", "saveContract"):
            self.assertIn(needle, r.text)

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
            "currency_symbol": "$",
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
        self.assertEqual(body["currency_symbol"], "$")
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
            "currency_symbol": "$",
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
        # (10*20*1.2) + (4*50*1.0) = 240 + 200 = 440 subtotal. Material tax is
        # material-only: 240 x 8.25% = 19.80 (labor stays tax-exempt).
        self.assertAlmostEqual(detail["subtotal"], 440.0, places=2)
        self.assertAlmostEqual(detail["tax_amount"], 19.80, places=2)
        self.assertAlmostEqual(detail["total"], 459.80, places=2)

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

