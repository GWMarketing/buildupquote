"""Integration tests for the platform admin section (/api/admin/* and the
/admin page), against a throwaway SQLite database.

Same setup discipline as test_crm_api.py / test_billing.py: DATABASE_URL is
set before importing the app, and admin access is granted directly in the DB
(the ADMIN_EMAILS bootstrap only runs in the app's lifespan, which tests
don't exercise). Every test asserts the admin-only gate: normal users get 403
on every endpoint, admins get through.
"""
import csv
import io
import os
import tempfile
import unittest
from unittest import mock

_DB = os.path.join(tempfile.gettempdir(), "test_admin.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


class AdminApiTestCase(unittest.TestCase):
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
        return r.json()

    def auth(self, body):
        return {"Authorization": "Bearer " + body["access_token"]}

    def promote(self, email):
        db = SessionLocal()
        try:
            user = db.query(models.User).filter(models.User.email == email).first()
            user.is_admin = True
            db.add(user)
            db.commit()
        finally:
            db.close()

    def org_id(self, auth):
        r = self.client.get("/api/organization/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def user_id(self, auth):
        r = self.client.get("/api/users/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    # ------------------------------------------------------------------
    # The admin gate: every endpoint 403s for a normal user
    # ------------------------------------------------------------------
    def test_all_admin_endpoints_blocked_for_normal_users(self):
        auth = self.auth(self.register("adm-normal@acme.com"))
        checks = [
            ("GET", "/api/admin/stats", None),
            ("GET", "/api/admin/organizations", None),
            ("GET", "/api/admin/users", None),
            ("GET", "/api/admin/clients", None),
            ("GET", "/api/admin/clients/export", None),
            ("PATCH", "/api/admin/users/999999/admin", {"is_admin": True}),
            ("PATCH", "/api/admin/users/999999/active", {"is_active": False}),
            ("PATCH", "/api/admin/organizations/999999/subscription", {"tier": "pro"}),
        ]
        for method, path, body in checks:
            r = self.client.request(method, path, headers=auth, json=body)
            self.assertEqual(r.status_code, 403, f"{method} {path} -> {r.status_code}")
            self.assertIn("Admin access required", r.json()["detail"])

    def test_users_me_exposes_is_admin(self):
        normal = self.register("adm-notadmin@acme.com")
        r = self.client.get("/api/users/me", headers=self.auth(normal))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_admin"])

        self.promote("adm-notadmin@acme.com")
        r = self.client.get("/api/users/me", headers=self.auth(normal))
        self.assertTrue(r.json()["is_admin"])

    def test_admin_emails_env_flags_new_registrations(self):
        # A user who registers with an email listed in ADMIN_EMAILS is made an
        # admin at registration time -- no server restart needed.
        import app.routers.auth as auth_router

        with mock.patch.object(auth_router, "ADMIN_EMAILS", {"glenn.boss@acme.com"}):
            body = self.register("glenn.boss@acme.com", org="Boss Co")
        r = self.client.get("/api/users/me", headers=self.auth(body))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_admin"])

    # ------------------------------------------------------------------
    # Stats + organizations
    # ------------------------------------------------------------------
    def test_admin_stats(self):
        boss = self.register("adm-boss@acme.com")
        self.promote("adm-boss@acme.com")
        auth = self.auth(boss)
        self.register("adm-tenant@acme.com", org="Tenant Co")
        r = self.client.get("/api/admin/stats", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertGreaterEqual(body["counts"]["organizations"], 2)
        self.assertGreaterEqual(body["counts"]["users"], 2)
        self.assertGreaterEqual(body["counts"]["admins"], 1)
        self.assertIn("trialing", body["subscription_breakdown"])
        self.assertIn("recent_signups", body)
        self.assertIn("recent_quotes", body)
        self.assertIn("mrr", body["revenue"])

    def test_organizations_list_and_search(self):
        boss = self.register("adm-lister@acme.com")
        self.promote("adm-lister@acme.com")
        auth = self.auth(boss)
        self.register("adm-search@acme.com", org="UniqueSearch Co")
        r = self.client.get("/api/admin/organizations", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(any("UniqueSearch" in o["name"] for o in r.json()))
        r = self.client.get("/api/admin/organizations?search=uniquesearch", headers=auth)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["name"], "UniqueSearch Co")

    def test_override_subscription_tier(self):
        boss = self.register("adm-tier@acme.com")
        self.promote("adm-tier@acme.com")
        auth = self.auth(boss)
        tenant = self.register("adm-tenant2@acme.com", org="Tier Target")
        target_org = self.org_id(self.auth(tenant))

        r = self.client.patch(
            f"/api/admin/organizations/{target_org}/subscription",
            headers=auth, json={"tier": "enterprise", "status": "active"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["subscription_tier"], "enterprise")
        self.assertEqual(r.json()["subscription_status"], "active")

        r = self.client.patch(
            f"/api/admin/organizations/{target_org}/subscription",
            headers=auth, json={"tier": "platinum"},
        )
        self.assertEqual(r.status_code, 422, r.text)

    # ------------------------------------------------------------------
    # Admin management + account control
    # ------------------------------------------------------------------
    def test_promote_and_demote_another_user(self):
        boss = self.register("adm-owner@acme.com")
        self.promote("adm-owner@acme.com")
        auth = self.auth(boss)
        other = self.register("adm-other@acme.com", org="Other Co")
        other_id = self.user_id(self.auth(other))

        r = self.client.patch(f"/api/admin/users/{other_id}/admin", headers=auth, json={"is_admin": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_admin"])
        # The promoted user now passes the admin gate themselves.
        r = self.client.get("/api/admin/stats", headers=self.auth(other))
        self.assertEqual(r.status_code, 200, r.text)

        r = self.client.patch(f"/api/admin/users/{other_id}/admin", headers=auth, json={"is_admin": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_admin"])
        r = self.client.get("/api/admin/stats", headers=self.auth(other))
        self.assertEqual(r.status_code, 403, r.text)

    def test_admin_cannot_demote_self(self):
        boss = self.register("adm-self@acme.com")
        self.promote("adm-self@acme.com")
        auth = self.auth(boss)
        boss_id = self.user_id(auth)
        r = self.client.patch(f"/api/admin/users/{boss_id}/admin", headers=auth, json={"is_admin": False})
        self.assertEqual(r.status_code, 400, r.text)

    def test_deactivate_blocks_login_and_reactivate_restores(self):
        boss = self.register("adm-hr@acme.com")
        self.promote("adm-hr@acme.com")
        auth = self.auth(boss)
        victim = self.register("adm-victim@acme.com", org="Victim Co")
        victim_auth = self.auth(victim)
        victim_id = self.user_id(victim_auth)

        # Still works before deactivation.
        r = self.client.get("/api/auth/me", headers=victim_auth)
        self.assertEqual(r.status_code, 200, r.text)

        r = self.client.patch(f"/api/admin/users/{victim_id}/active", headers=auth, json={"is_active": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["is_active"])
        # Their existing JWT is dead now.
        r = self.client.get("/api/auth/me", headers=victim_auth)
        self.assertEqual(r.status_code, 401, r.text)

        r = self.client.patch(f"/api/admin/users/{victim_id}/active", headers=auth, json={"is_active": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["is_active"])
        r = self.client.get("/api/auth/me", headers=victim_auth)
        self.assertEqual(r.status_code, 200, r.text)

    def test_admin_cannot_deactivate_self(self):
        boss = self.register("adm-selfoff@acme.com")
        self.promote("adm-selfoff@acme.com")
        auth = self.auth(boss)
        boss_id = self.user_id(auth)
        r = self.client.patch(f"/api/admin/users/{boss_id}/active", headers=auth, json={"is_active": False})
        self.assertEqual(r.status_code, 400, r.text)

    # ------------------------------------------------------------------
    # Client list export
    # ------------------------------------------------------------------
    def _add_client(self, auth, name, email):
        r = self.client.post("/api/clients", headers=auth, json={
            "name": name, "email": email, "phone": "(555) 010-1000",
        })
        self.assertEqual(r.status_code, 201, r.text)

    def test_export_clients_csv_global_and_per_org(self):
        boss = self.register("adm-exporter@acme.com")
        self.promote("adm-exporter@acme.com")
        auth = self.auth(boss)

        org_a = self.register("adm-clienta@acme.com", org="Alpha Co")
        org_b = self.register("adm-clientb@acme.com", org="Beta Co")
        self._add_client(self.auth(org_a), "Alice Alpha", "alice@alpha.com")
        self._add_client(self.auth(org_b), "Bob Beta", "bob@beta.com")

        r = self.client.get("/api/admin/clients/export", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "text/csv; charset=utf-8")
        rows = list(csv.reader(io.StringIO(r.text)))
        self.assertEqual(rows[0], ["organization", "client_name", "site_address", "phone", "email", "created_at", "quote_count", "total_quoted"])
        names = {row[1] for row in rows[1:]}
        orgs_in_csv = {row[0] for row in rows[1:]}
        # The suite shares one DB, so other tests' clients may be present too --
        # assert ours are there, not that the list is exactly ours.
        self.assertIn("Alice Alpha", names)
        self.assertIn("Bob Beta", names)
        self.assertIn("Alpha Co", orgs_in_csv)
        self.assertIn("Beta Co", orgs_in_csv)

        # Per-org export only includes that org's clients.
        org_a_id = self.org_id(self.auth(org_a))
        r = self.client.get(f"/api/admin/clients/export?organization_id={org_a_id}", headers=auth)
        rows = list(csv.reader(io.StringIO(r.text)))
        self.assertEqual({row[1] for row in rows[1:]}, {"Alice Alpha"})

    def test_admin_clients_json_list(self):
        boss = self.register("adm-clients-json@acme.com")
        self.promote("adm-clients-json@acme.com")
        auth = self.auth(boss)
        tenant = self.register("adm-clientjson@acme.com", org="JSON Co")
        self._add_client(self.auth(tenant), "Carla JSON", "carla@json.com")
        r = self.client.get("/api/admin/clients", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["organization_name"], "JSON Co")
        self.assertEqual(r.json()[0]["name"], "Carla JSON")

    # ------------------------------------------------------------------
    # Page render
    # ------------------------------------------------------------------
    def test_admin_page_renders_shell(self):
        self.register("adm-pager@acme.com")
        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Platform control center", r.text)
        self.assertIn("/api/admin/stats", r.text)


if __name__ == "__main__":
    unittest.main()


