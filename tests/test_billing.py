"""Integration tests for the Stripe billing layer (checkout sessions, the
signature-verified webhook, the customer portal) and the new marketing
landing page (/ and /pricing), against a throwaway SQLite database.

Same setup discipline as test_crm_api.py: DATABASE_URL/SECRET_KEY are set
*before* importing the app, and every Stripe call is mocked -- no network,
no keys required. The billing module's env-derived constants are patched per
test so the suite never depends on import order.
"""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import stripe

_DB = os.path.join(tempfile.gettempdir(), "test_billing.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
import app.routers.billing as billing  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


class BillingApiTestCase(unittest.TestCase):
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

    def org_id(self, auth):
        r = self.client.get("/api/organization/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def get_org(self, org_id):
        db = SessionLocal()
        try:
            return db.query(models.Organization).get(org_id)
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Landing page + pricing page
    # ------------------------------------------------------------------
    def test_landing_page_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn("Stop wasting evenings on bids", html)
        self.assertIn("Start 7-Day Free Trial", html)
        self.assertIn("Parametric Assembly Calculator", html)
        for tier in ("Starter", "Pro", "Enterprise"):
            self.assertIn(tier, html)
        self.assertIn("/api/billing/create-checkout-session", html)

    def test_pricing_page_renders(self):
        r = self.client.get("/pricing")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Pick a plan", r.text)
        self.assertIn("2 Months Free", r.text)

    def test_register_page_resumes_checkout(self):
        r = self.client.get("/register")
        self.assertEqual(r.status_code, 200)
        self.assertIn("create-checkout-session", r.text)

    # ------------------------------------------------------------------
    # create-checkout-session
    # ------------------------------------------------------------------
    def test_checkout_requires_auth(self):
        r = self.client.post("/api/billing/create-checkout-session", json={"tier": "pro"})
        self.assertEqual(r.status_code, 401)

    def test_checkout_rejects_bad_tier(self):
        auth = self.register("bad-tier@acme.com")
        r = self.client.post("/api/billing/create-checkout-session", headers=auth, json={"tier": "gold"})
        self.assertEqual(r.status_code, 422, r.text)

    def test_checkout_503_when_stripe_unconfigured(self):
        auth = self.register("no-stripe@acme.com")
        with mock.patch.object(billing, "STRIPE_SECRET_KEY", ""):
            r = self.client.post("/api/billing/create-checkout-session", headers=auth, json={"tier": "pro"})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("not configured", r.json()["detail"])

    def test_checkout_503_when_price_missing(self):
        auth = self.register("no-price@acme.com")
        with mock.patch.object(billing, "STRIPE_SECRET_KEY", "sk_test_x"):
            r = self.client.post("/api/billing/create-checkout-session", headers=auth, json={"tier": "pro", "interval": "month"})
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("No Stripe price configured", r.json()["detail"])

    def test_checkout_success_creates_7_day_trial_subscription(self):
        auth = self.register("checkout@acme.com")
        org_id = self.org_id(auth)
        fake = SimpleNamespace(url="https://checkout.stripe.com/c/pay_cs_test_123", customer="cus_test_123")
        with mock.patch.object(billing, "STRIPE_SECRET_KEY", "sk_test_x"), \
             mock.patch.dict(os.environ, {"STRIPE_PRICE_PRO_MONTH": "price_pro_month"}), \
             mock.patch.object(stripe.checkout.Session, "create", return_value=fake) as create:
            r = self.client.post(
                "/api/billing/create-checkout-session", headers=auth,
                json={"tier": "pro", "interval": "month"},
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["url"], fake.url)

        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["mode"], "subscription")
        self.assertEqual(kwargs["customer_email"], "checkout@acme.com")
        self.assertEqual(kwargs["client_reference_id"], str(org_id))
        self.assertEqual(kwargs["line_items"], [{"price": "price_pro_month", "quantity": 1}])
        self.assertEqual(kwargs["metadata"], {
            "organization_id": str(org_id), "tier": "pro", "interval": "month",
        })
        self.assertEqual(kwargs["subscription_data"]["trial_period_days"], 7)
        self.assertEqual(kwargs["subscription_data"]["metadata"]["tier"], "pro")
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", kwargs["success_url"])
        self.assertIn("/dashboard?", kwargs["success_url"])
        self.assertIn("/pricing?canceled=1", kwargs["cancel_url"])

        # The customer id from the created session is persisted immediately.
        self.assertEqual(self.get_org(org_id).stripe_customer_id, "cus_test_123")

    # ------------------------------------------------------------------
    # webhook
    # ------------------------------------------------------------------
    def _post_webhook(self, event, sig="t=1,v1=abc"):
        body = json.dumps(event)
        return self.client.post(
            "/api/billing/webhook",
            content=body,
            headers={"stripe-signature": sig},
        )

    def test_webhook_requires_secret(self):
        with mock.patch.object(billing, "STRIPE_WEBHOOK_SECRET", ""):
            r = self.client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "x"})
        self.assertEqual(r.status_code, 503)

    def test_webhook_rejects_bad_signature(self):
        with mock.patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             mock.patch.object(
                 stripe.Webhook, "construct_event",
                 side_effect=stripe.error.SignatureVerificationError("bad", sig_header="x"),
             ):
            r = self.client.post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "x"})
        self.assertEqual(r.status_code, 400, r.text)

    def test_webhook_checkout_completed_attaches_subscription(self):
        auth = self.register("hook-checkout@acme.com")
        org_id = self.org_id(auth)
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": "cs_test_1",
                "client_reference_id": str(org_id),
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {"organization_id": str(org_id), "tier": "enterprise", "interval": "year"},
            }},
        }
        with mock.patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             mock.patch.object(stripe.Webhook, "construct_event", return_value=event):
            r = self._post_webhook(event)
        self.assertEqual(r.status_code, 200, r.text)

        org = self.get_org(org_id)
        self.assertEqual(org.stripe_customer_id, "cus_123")
        self.assertEqual(org.stripe_subscription_id, "sub_123")
        self.assertEqual(org.subscription_tier, "enterprise")
        self.assertEqual(org.subscription_status, "trialing")

    def test_webhook_subscription_updated_maps_status_and_trial(self):
        auth = self.register("hook-upd@acme.com")
        org_id = self.org_id(auth)
        db = SessionLocal()
        try:
            org = db.query(models.Organization).get(org_id)
            org.stripe_subscription_id = "sub_abc"
            db.commit()
        finally:
            db.close()
        event = {
            "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_abc",
                "status": "past_due",
                "trial_end": 1750000000,
                "metadata": {"tier": "pro"},
            }},
        }
        with mock.patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             mock.patch.object(stripe.Webhook, "construct_event", return_value=event):
            r = self._post_webhook(event)
        self.assertEqual(r.status_code, 200, r.text)

        org = self.get_org(org_id)
        self.assertEqual(org.subscription_tier, "pro")
        self.assertEqual(org.subscription_status, "past_due")
        # SQLite stores timestamps without a tz offset, so the exact instant
        # can drift by the local offset; the calendar date is stable.
        self.assertEqual(org.trial_ends_at.strftime("%Y-%m-%d"), "2025-06-15")

    def test_webhook_subscription_deleted_cancels_org(self):
        auth = self.register("hook-del@acme.com")
        org_id = self.org_id(auth)
        db = SessionLocal()
        try:
            org = db.query(models.Organization).get(org_id)
            org.stripe_subscription_id = "sub_xyz"
            db.commit()
        finally:
            db.close()
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_xyz", "status": "canceled", "metadata": {}}},
        }
        with mock.patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             mock.patch.object(stripe.Webhook, "construct_event", return_value=event):
            r = self._post_webhook(event)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.get_org(org_id).subscription_status, "canceled")

    def test_webhook_ignores_unknown_events(self):
        event = {"type": "invoice.payment_succeeded", "data": {"object": {}}}
        with mock.patch.object(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test"), \
             mock.patch.object(stripe.Webhook, "construct_event", return_value=event):
            r = self._post_webhook(event)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"received": True})

    # ------------------------------------------------------------------
    # customer portal
    # ------------------------------------------------------------------
    def test_customer_portal_requires_auth(self):
        r = self.client.post("/api/billing/customer-portal")
        self.assertEqual(r.status_code, 401)

    def test_customer_portal_without_subscription(self):
        auth = self.register("no-cus@acme.com")
        with mock.patch.object(billing, "STRIPE_SECRET_KEY", "sk_test_x"):
            r = self.client.post("/api/billing/customer-portal", headers=auth)
        self.assertEqual(r.status_code, 400, r.text)

    def test_customer_portal_success(self):
        auth = self.register("portal@acme.com")
        org_id = self.org_id(auth)
        db = SessionLocal()
        try:
            org = db.query(models.Organization).get(org_id)
            org.stripe_customer_id = "cus_123"
            db.commit()
        finally:
            db.close()
        fake = SimpleNamespace(url="https://billing.stripe.com/session/xyz")
        with mock.patch.object(billing, "STRIPE_SECRET_KEY", "sk_test_x"), \
             mock.patch.object(stripe.billing_portal.Session, "create", return_value=fake) as create:
            r = self.client.post("/api/billing/customer-portal", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["url"], fake.url)
        self.assertEqual(create.call_args.kwargs["customer"], "cus_123")
        self.assertIn("/settings", create.call_args.kwargs["return_url"])

    def test_organization_profile_exposes_subscription_fields(self):
        auth = self.register("billingprofile@acme.com")
        r = self.client.get("/api/organization/me", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["subscription_tier"], "starter")
        self.assertEqual(body["subscription_status"], "trialing")
        self.assertIn("stripe_customer_id", body)
        self.assertIn("trial_ends_at", body)


if __name__ == "__main__":
    unittest.main()


