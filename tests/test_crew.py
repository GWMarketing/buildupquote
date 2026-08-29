"""Tests for the crew roster + availability calendar:

  1. The builder adds a crew member -> a role='crew' login is created with a
     temporary password (email mocked -- SMTP_HOST unset in tests).
  2. The crew member logs in, marks their own availability on a month
     calendar, and can read it back.
  3. Role guards: crew accounts cannot reach office APIs (middleware 403) or
     manage the roster (router 403). The builder can override availability
     and deactivate a member (login then 401s).
"""
import os
import tempfile
import unittest
from datetime import datetime

_DB = os.path.join(tempfile.gettempdir(), "test_crew.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402

_EMAIL_SEQ = [0]


def _unique_email(prefix="joe"):
    _EMAIL_SEQ[0] += 1
    return f"{prefix}{_EMAIL_SEQ[0]}@example.com"


class CrewApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(fastapi_app.app)
        cls.client.__enter__()
        cls.month = datetime.now().strftime("%Y-%m")
        cls.day1 = f"{cls.month}-01"
        cls.day2 = f"{cls.month}-02"
        cls.day3 = f"{cls.month}-03"

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def register(self, email, org="Acme Roofing"):
        r = self.client.post("/api/auth/register", json={
            "email": email, "password": "pw12345678", "organization_name": org,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    def add_crew(self, auth, name="Joe Romero", email=None, trade="Framing"):
        email = email or _unique_email()
        r = self.client.post("/api/crew", headers=auth, json={
            "full_name": name, "email": email, "trade": trade, "phone": "555-0100",
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def login(self, email, password):
        r = self.client.post("/api/auth/token", data={
            "username": email, "password": password,
        })
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    # ------------------------------------------------------------------
    # Builder adds crew -> role='crew' login with temporary credentials
    # ------------------------------------------------------------------
    def test_builder_adds_crew_member_with_temporary_password(self):
        auth = self.register("crew-bc@acme.com")
        created = self.add_crew(auth)
        self.assertTrue(created["temporary_password"])
        self.assertEqual(created["role"], "crew")
        self.assertEqual(created["trade"], "Framing")

        # The crew account logs in with the temporary password.
        crew = self.login(created["email"], created["temporary_password"])
        me = self.client.get("/api/crew/me", headers=crew)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["full_name"], "Joe Romero")
        self.assertEqual(me.json()["role"], "crew")

    def test_duplicate_email_rejected(self):
        auth = self.register("crew-bc2@acme.com")
        self.add_crew(auth, email="dup@example.com")
        r = self.client.post("/api/crew", headers=auth, json={
            "full_name": "Copy", "email": "dup@example.com",
        })
        self.assertEqual(r.status_code, 400, r.text)

    # ------------------------------------------------------------------
    # Crew self-service availability
    # ------------------------------------------------------------------
    def test_crew_marks_and_reads_own_availability(self):
        auth = self.register("crew-bc3@acme.com")
        created = self.add_crew(auth)
        crew = self.login(created["email"], created["temporary_password"])

        r = self.client.put("/api/crew/me/availability", headers=crew, json={
            "month": self.month,
            "days": {self.day1: "available", self.day2: "unavailable"},
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["days"][self.day1], "available")
        self.assertEqual(r.json()["days"][self.day2], "unavailable")

        # Read back.
        r = self.client.get(
            f"/api/crew/me/availability?month={self.month}", headers=crew
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["days"], {self.day1: "available", self.day2: "unavailable"})

        # Unset removes the mark.
        r = self.client.put("/api/crew/me/availability", headers=crew, json={
            "month": self.month,
            "days": {self.day1: "unset"},
        })
        self.assertEqual(r.json()["days"], {self.day2: "unavailable"})

    def test_availability_rejects_out_of_month_dates(self):
        auth = self.register("crew-bc4@acme.com")
        created = self.add_crew(auth)
        crew = self.login(created["email"], created["temporary_password"])
        r = self.client.put("/api/crew/me/availability", headers=crew, json={
            "month": self.month,
            "days": {"2099-01-01": "available"},  # not in this month
        })
        self.assertEqual(r.status_code, 400, r.text)

    # ------------------------------------------------------------------
    # Role guards: crew is a field hand, not an estimator
    # ------------------------------------------------------------------
    def test_crew_blocked_from_office_apis(self):
        auth = self.register("crew-bc5@acme.com")
        created = self.add_crew(auth)
        crew = self.login(created["email"], created["temporary_password"])

        for path in ("/api/quotes", "/api/clients", "/api/catalog", "/api/dashboard/summary"):
            r = self.client.get(path, headers=crew)
            self.assertEqual(r.status_code, 403, f"{path}: {r.text}")

    def test_crew_cannot_manage_roster(self):
        auth = self.register("crew-bc6@acme.com")
        created = self.add_crew(auth)
        crew = self.login(created["email"], created["temporary_password"])
        r = self.client.post("/api/crew", headers=crew, json={
            "full_name": "Sneaky", "email": "sneaky@example.com",
        })
        self.assertEqual(r.status_code, 403, r.text)

    # ------------------------------------------------------------------
    # Builder views the combined picture and can override/deactivate
    # ------------------------------------------------------------------
    def test_builder_sees_roster_and_overrides_availability(self):
        auth = self.register("crew-bc7@acme.com")
        self.add_crew(auth, name="Joe Romero")
        self.add_crew(auth, name="Ann Kim", email="ann@example.com", trade="Drywall")

        roster = self.client.get("/api/crew", headers=auth)
        self.assertEqual(roster.status_code, 200, roster.text)
        names = [m["full_name"] for m in roster.json()]
        self.assertIn("Joe Romero", names)
        self.assertIn("Ann Kim", names)

        # Builder marks Ann's days for her (override).
        ann = next(m for m in roster.json() if m["full_name"] == "Ann Kim")
        r = self.client.put(f"/api/crew/{ann['id']}/availability", headers=auth, json={
            "month": self.month, "days": {self.day3: "available"},
        })
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(
            f"/api/crew/{ann['id']}/availability?month={self.month}", headers=auth
        )
        self.assertEqual(r.json()["days"], {self.day3: "available"})

    def test_builder_deactivates_crew_member_and_login_is_blocked(self):
        auth = self.register("crew-bc8@acme.com")
        created = self.add_crew(auth)
        crew_id = created["id"]
        password = created["temporary_password"]

        r = self.client.delete(f"/api/crew/{crew_id}", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)

        # The login endpoint still issues a token (pre-existing behavior), but
        # every protected call now 401s -- get_current_user rejects the
        # deactivated account.
        r = self.client.post("/api/auth/token", data={
            "username": created["email"], "password": password,
        })
        self.assertEqual(r.status_code, 200, r.text)
        dead_crew = {"Authorization": "Bearer " + r.json()["access_token"]}
        r = self.client.get("/api/crew/me", headers=dead_crew)
        self.assertEqual(r.status_code, 401, r.text)
        self.assertIn("disabled", r.text.lower())

    def test_crew_page_renders(self):
        auth = self.register("crew-bc9@acme.com")
        r = self.client.get("/crew")
        self.assertIn("Crew Availability", r.text)
        self.assertIn("crewApp", r.text)
        self.assertIn("/api/crew/me/availability", r.text)


if __name__ == "__main__":
    unittest.main()
