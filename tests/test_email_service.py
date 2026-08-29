"""Tests for transactional email dispatch: the SMTP service itself (disabled
gracefully without SMTP_HOST, real send with SMTP mocked), the
/api/quotes/{id}/send-email endpoint, the post-signing notification trigger,
and the quote-builder "Send to Client" UI hook.

Runs inside `unittest discover -s tests` against a throwaway SQLite DB, the
same way as test_crm_api.py -- and the email service is configured OFF (no
SMTP_HOST), so nothing ever touches a network.
"""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_DB = os.path.join(tempfile.gettempdir(), "test_email.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
import app.services.email_service as email_service  # noqa: E402
from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


class EmailServiceTestCase(unittest.TestCase):
    """Pure service tests -- SMTP is mocked, so nothing touches the network."""

    def test_disabled_when_no_smtp_host(self):
        self.assertFalse(email_service.is_configured())
        self.assertFalse(email_service.send_email("a@b.com", "hi", "<p>hi</p>"))

    def test_send_email_sends_when_configured(self):
        with mock.patch.object(email_service, "SMTP_HOST", "smtp.test"), \
             mock.patch.object(email_service, "EMAILS_FROM", "quotes@buildupquote.com"), \
             mock.patch.object(email_service, "SMTP_USER", "user"), \
             mock.patch.object(email_service, "SMTP_PASSWORD", "secret"), \
             mock.patch.object(email_service.smtplib, "SMTP", autospec=True) as smtp_cls:
            server = smtp_cls.return_value
            ok = email_service.send_email(
                "client@example.com",
                "Test subject",
                "<html><body><h1>Hello</h1></body></html>",
            )
        self.assertTrue(ok)
        smtp_cls.assert_called_once_with("smtp.test", 587, timeout=20)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("user", "secret")
        server.send_message.assert_called_once()
        msg = server.send_message.call_args.args[0]
        self.assertEqual(msg["To"], "client@example.com")
        self.assertEqual(msg["Subject"], "Test subject")
        self.assertEqual(msg["From"], "quotes@buildupquote.com")
        self.assertIn("Hello", msg.as_string())

    def test_send_quote_to_client_builds_branded_cta_email(self):
        quote = SimpleNamespace(
            id=7, title="Basement Finish", total=12850.0, public_uuid="abc-123",
        )
        client = SimpleNamespace(name="Joan Smith", email="joan@example.com")
        org = SimpleNamespace(name="Glenn's Roofing", currency_symbol="$")

        with mock.patch.object(email_service, "SMTP_HOST", "smtp.test"), \
             mock.patch.object(email_service, "APP_BASE_URL", "https://example.test"), \
             mock.patch.object(email_service.smtplib, "SMTP", autospec=True) as smtp_cls:
            ok = email_service.send_quote_to_client(quote, client, org, message="Call me!")

        self.assertTrue(ok)
        msg = smtp_cls.return_value.send_message.call_args.args[0]
        self.assertIn("Your proposal for Basement Finish", msg["Subject"])
        self.assertEqual(msg["To"], "joan@example.com")
        html = msg.as_string()
        self.assertIn("https://example.test/view/quote/abc-123", html)
        self.assertIn("Review &amp; Sign Proposal", html)
        self.assertIn("$12,850.00", html)
        self.assertIn("Call me!", html)

    def test_send_quote_to_client_skips_without_client_email(self):
        quote = SimpleNamespace(id=8, title="Roof", total=100.0, public_uuid="x")
        client = SimpleNamespace(name="No Email", email=None)
        with mock.patch.object(email_service, "SMTP_HOST", "smtp.test"):
            self.assertFalse(email_service.send_quote_to_client(quote, client, None))

    def test_send_quote_accepted_notification_contractor_and_client(self):
        quote = SimpleNamespace(
            id=9, title="Kitchen", total=5000.0, public_uuid="y",
            signed_by="Joan Smith", signer_email="joan@example.com",
            signer_ip="203.0.113.9", signer_user_agent="Mozilla/5.0",
            accepted_at=None, client_signature=None,
            client=SimpleNamespace(name="Joan Smith", email="joan@example.com"),
            items=[],
        )
        org = SimpleNamespace(
            name="Glenn's Roofing", currency_symbol="$",
            email="office@example.com",
            users=[SimpleNamespace(email="boss@example.com")],
        )

        with mock.patch.object(email_service, "SMTP_HOST", "smtp.test"), \
             mock.patch.object(email_service, "_quote_pdf_bytes",
                               return_value=b"%PDF-1.4 fake pdf") as pdf_builder, \
             mock.patch.object(email_service.smtplib, "SMTP", autospec=True) as smtp_cls:
            results = email_service.send_quote_accepted_notification(quote, quote.client, org)

        self.assertTrue(all(results.values()), results)
        recipients = {key.split(":", 1)[1] for key in results}
        self.assertEqual(recipients, {"office@example.com", "boss@example.com", "joan@example.com"})
        pdf_builder.assert_called_once()

        # The client email carries the PDF attachment (base64-encoded in MIME).
        messages = [c.args[0] for c in smtp_cls.return_value.send_message.call_args_list]
        client_msg = next(m for m in messages if m["To"] == "joan@example.com")
        raw = client_msg.as_string()
        self.assertIn("application/pdf", raw)
        self.assertIn('filename="Kitchen.pdf"', raw)
        self.assertIn("JVBERi0xLjQgZmFrZSBwZGY=", raw)  # base64 of %PDF-1.4 fake pdf
        contractor_msg = next(m for m in messages if m["To"] == "boss@example.com")
        self.assertIn("accepted & signed", contractor_msg["Subject"].lower())


class EmailEndpointsTestCase(unittest.TestCase):
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

    def make_client(self, auth, name, email=None):
        r = self.client.post("/api/clients", headers=auth, json={
            "name": name, "email": email,
        })
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def make_quote(self, auth, title="Test proposal", client_id=None):
        r = self.client.post("/api/quotes", headers=auth, json={
            "title": title, "client_id": client_id,
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
    # /api/quotes/{id}/send-email
    # ------------------------------------------------------------------
    def test_send_email_requires_auth(self):
        r = self.client.post("/api/quotes/1/send-email", json={})
        self.assertEqual(r.status_code, 401)

    def test_send_email_requires_client_with_email(self):
        auth = self.register("eml-noclient@acme.com")
        qid = self.make_quote(auth)
        self.add_line(auth, qid)
        r = self.client.post(f"/api/quotes/{qid}/send-email", headers=auth, json={})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("no client with an email", r.json()["detail"])

        cid = self.make_client(auth, "No Email Client")
        qid2 = self.make_quote(auth, client_id=cid)
        self.add_line(auth, qid2)
        r = self.client.post(f"/api/quotes/{qid2}/send-email", headers=auth, json={})
        self.assertEqual(r.status_code, 400, r.text)

    def test_send_email_success_sets_sent_and_queues(self):
        auth = self.register("eml-send@acme.com")
        cid = self.make_client(auth, "Email Client", "eml-client@example.com")
        qid = self.make_quote(auth, client_id=cid)
        self.add_line(auth, qid)

        with mock.patch.object(email_service, "queue_send_quote_to_client") as q:
            r = self.client.post(f"/api/quotes/{qid}/send-email", headers=auth,
                                 json={"message": "Excited to start!"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "sent")
        self.assertEqual(body["to"], "eml-client@example.com")
        q.assert_called_once_with(qid, "Excited to start!")

        r = self.client.get(f"/api/quotes/{qid}", headers=auth)
        self.assertEqual(r.json()["status"], "sent")

    def test_send_email_requires_line_items(self):
        auth = self.register("eml-nolines@acme.com")
        cid = self.make_client(auth, "Line Client", "line@example.com")
        qid = self.make_quote(auth, client_id=cid)
        r = self.client.post(f"/api/quotes/{qid}/send-email", headers=auth, json={})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("Add line items", r.json()["detail"])

    # ------------------------------------------------------------------
    # Post-signing notification trigger
    # ------------------------------------------------------------------
    def test_public_accept_triggers_notification(self):
        auth = self.register("eml-accept@acme.com")
        cid = self.make_client(auth, "Signer Client", "signer@example.com")
        qid = self.make_quote(auth, client_id=cid)
        self.add_line(auth, qid)

        db = SessionLocal()
        try:
            quote = db.query(models.Quote).filter(models.Quote.id == qid).first()
            public_uuid = quote.public_uuid
        finally:
            db.close()

        with mock.patch.object(email_service, "queue_quote_accepted_notification") as q:
            r = self.client.post(
                f"/api/public/quotes/{public_uuid}/accept",
                json={
                    "signature_data": "data:image/png;base64,AAAA",
                    "client_name": "Signer Client",
                    "signer_email": "signer@example.com",
                },
            )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["accepted"])
        q.assert_called_once_with(qid)

    # ------------------------------------------------------------------
    # UI hook
    # ------------------------------------------------------------------
    def test_quote_builder_renders_send_button_and_modal(self):
        r = self.client.get("/quotes/new")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Send to Client", r.text)
        self.assertIn("/send-email", r.text)
        self.assertIn("openSendModal", r.text)


if __name__ == "__main__":
    unittest.main()


