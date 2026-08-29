"""Transactional email dispatch for BuildUpQuote (SMTP, stdlib only).

Config (environment variables):
  SMTP_HOST            SMTP server hostname (empty DISABLES all sending)
  SMTP_PORT            port (default 587)
  SMTP_USER            login username
  SMTP_PASSWORD        login password / API token
  SMTP_USE_TLS         "true" to STARTTLS on connect (default)
  SMTP_USE_SSL         "true" for implicit SSL, e.g. port 465
  EMAILS_FROM_EMAIL    From address shown to recipients
  APP_BASE_URL         public origin used to build absolute links

When SMTP_HOST is not set the service is "disabled": every send is a no-op
that logs a warning and returns False, so local dev and the test suite stay
green without a mail server.

Public API:
  is_configured() -> bool
  send_email(to, subject, html, attachments=()) -> bool
  send_quote_to_client(quote, client, org, message="") -> bool
  send_quote_accepted_notification(quote, client, org) -> dict
  queue_send_quote_to_client(quote_id, message="")   # opens its own DB session
  queue_quote_accepted_notification(quote_id)        # opens its own DB session

The queue_* functions are for FastAPI BackgroundTasks: they reload the quote
(and its org) in a fresh session so the email never depends on the request's
DB session, which is closed by the time the background task runs.
"""
import logging
import os
import smtplib
import tempfile
import time
from email.message import EmailMessage

from sqlalchemy.orm import selectinload

logger = logging.getLogger("buildupquote.email")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() != "false"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
EMAILS_FROM = os.getenv("EMAILS_FROM_EMAIL", SMTP_USER or "no-reply@buildupquote.com")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://glennwestman.com")


def is_configured() -> bool:
    """True when SMTP is set up -- the UI can show 'email sent' vs 'queued'."""
    return bool(SMTP_HOST)


def send_email(to_email: str, subject: str, html_body: str, attachments=()) -> bool:
    """Send one HTML email, optionally with attachments.

    attachments is an iterable of (filename, bytes, maintype, subtype).
    Returns True when the SMTP server accepted the message, False when the
    service is disabled or delivery fails. Never raises -- email is a
    best-effort side effect and must not break the request that triggered it.
    """
    if not SMTP_HOST:
        logger.warning(
            "email disabled (SMTP_HOST unset) -- not sending '%s' to %s",
            subject, to_email,
        )
        return False
    try:
        msg = EmailMessage()
        msg["From"] = EMAILS_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content("This email contains an HTML message. Please view it in an HTML-capable client.")
        msg.add_alternative(html_body, subtype="html")
        for filename, data, maintype, subtype in attachments:
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

        if SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
        try:
            if not SMTP_USE_SSL and SMTP_USE_TLS:
                server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 -- best effort cleanup
                server.close()
        logger.info("sent email '%s' to %s", subject, to_email)
        return True
    except Exception as exc:  # noqa: BLE001 -- email must never break the app
        logger.error("failed to send '%s' to %s: %s", subject, to_email, exc)
        return False


# ---------------------------------------------------------------------------
# Email HTML
# ---------------------------------------------------------------------------

def _email_layout(org_name: str, headline: str, body_html: str,
                  cta_url: str = "", cta_label: str = "") -> str:
    """A clean, inline-styled brand email shell (no external CSS -- mail
    clients ignore <style> and link tags)."""
    cta = ""
    if cta_url and cta_label:
        cta = (
            '<p style="margin:26px 0 0;">'
            f'<a href="{cta_url}" '
            'style="background:#f59e0b;color:#0f172a;font-weight:700;'
            'text-decoration:none;padding:12px 26px;border-radius:10px;'
            f'display:inline-block;">{cta_label}</a>'
            "</p>"
        )
    return (
        "<!doctype html><html><body style=\"margin:0;padding:0;background:#f1f5f9;"
        'font-family:Arial,Helvetica,sans-serif;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f1f5f9;padding:24px 0;"><tr><td align="center">'
        '<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;width:100%;background:#ffffff;border-radius:14px;'
        'overflow:hidden;border:1px solid #e2e8f0;">'
        '<tr><td style="background:#0f172a;padding:18px 28px;color:#ffffff;">'
        f'<span style="font-size:18px;font-weight:800;letter-spacing:.5px;">{org_name}</span></td></tr>'
        '<tr><td style="padding:28px;">'
        f'<h1 style="margin:0 0 14px;font-size:20px;color:#0f172a;">{headline}</h1>'
        f'<div style="font-size:15px;line-height:1.55;color:#334155;">{body_html}</div>'
        f"{cta}"
        "</td></tr>"
        '<tr><td style="padding:16px 28px;background:#f8fafc;color:#94a3b8;'
        'font-size:12px;border-top:1px solid #e2e8f0;">'
        "This is an automated message from BuildUpQuote. Please do not reply to this email."
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def _currency_for(org) -> str:
    return (org.currency_symbol if org and org.currency_symbol else "$")


# ---------------------------------------------------------------------------
# Quote emails
# ---------------------------------------------------------------------------

def send_quote_to_client(quote, client, org, message: str = "") -> bool:
    """The contractor's branded proposal email: project title, the grand
    total, an optional personal message, and a CTA straight into the public
    approval page (/view/quote/<public_uuid>)."""
    if not client or not (client.email or "").strip():
        logger.warning("no client email -- skipping quote email for quote %s", getattr(quote, "id", None))
        return False

    org_name = org.name if org else "BuildUpQuote"
    currency = _currency_for(org)
    total = float(quote.total or 0)
    link = f"{APP_BASE_URL}/view/quote/{quote.public_uuid}"

    subject = f"Your proposal for {quote.title} — {org_name}"
    body = (
        f"<p>Hello {client.name or 'there'},</p>"
        f"<p>{org_name} has prepared a proposal for "
        f"<strong>{quote.title}</strong> totaling "
        f"<strong>{currency}{total:,.2f}</strong>.</p>"
        "<p>You can review the details and approve the job online — "
        "it takes about a minute and is legally binding:</p>"
    )
    if (message or "").strip():
        body += (
            '<p style="background:#f8fafc;border-left:4px solid #f59e0b;'
            'padding:10px 14px;color:#334155;">'
            f"{message.strip()}</p>"
        )
    html = _email_layout(org_name, f"Proposal ready: {quote.title}", body,
                         link, "Review &amp; Sign Proposal")
    return send_email(client.email, subject, html)


def _quote_pdf_bytes(quote, org) -> bytes:
    """Render the signed (audit-stamped) branded PDF to bytes for attachment."""
    from app.services import quote_pdf  # local import: heavy (WeasyPrint)

    currency = _currency_for(org)
    _include, _contract = quote_pdf.contract_for_quote(
        quote, org, currency, time.strftime("%d %b %Y"),
    )
    context = {
        "quote": quote,
        "client": quote.client,
        "organization": org,
        "estimator": None,
        "lines": sorted(quote.items, key=lambda i: i.position or 0),
        "today": time.strftime("%B %d, %Y"),
        "signature_uri": quote_pdf.signature_uri(quote.client_signature),
        "signed_by": quote.signed_by,
        "signer_ip": quote.signer_ip,
        "accepted_at": quote.accepted_at,
        "include_contract": _include,
        "contract_text": _contract,
    }
    fd, out_path = tempfile.mkstemp(prefix=f"quote-{quote.id}-", suffix=".pdf")
    os.close(fd)
    try:
        quote_pdf.render_quote_pdf(context, out_path)
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def send_quote_accepted_notification(quote, client, org) -> dict:
    """The two emails that fire the moment a client signs:

    1. Contractor alert -- 'accepted & signed', with who/when/audit details.
    2. Client confirmation -- thank-you with the signed PDF attached.

    Returns a {recipient: bool} map of delivery results. Skipped recipients
    (no address) are simply absent from the map."""
    results: dict = {}

    # --- Contractor alert (org email + every user in the org) ---
    contractor_emails = set()
    if org and org.email and (org.email or "").strip():
        contractor_emails.add(org.email.strip())
    if org and org.users:
        for user in org.users:
            if user.email and user.email.strip():
                contractor_emails.add(user.email.strip())
    if contractor_emails:
        currency = _currency_for(org)
        total = float(quote.total or 0)
        signed_when = (
            quote.accepted_at.strftime("%B %d, %Y at %H:%M UTC")
            if quote.accepted_at else "just now"
        )
        audit_lines = "".join(
            f"<li><strong>{label}:</strong> {value}</li>"
            for label, value in (
                ("Signed by", quote.signed_by or "—"),
                ("Signer email", quote.signer_email or "—"),
                ("IP address", quote.signer_ip or "—"),
                ("User agent", quote.signer_user_agent or "—"),
            )
        )
        subject = f"Proposal accepted & signed: {quote.title}"
        body = (
            f"<p>Great news — <strong>{quote.client.name if quote.client else 'your client'}</strong> "
            f"has signed the proposal for <strong>{quote.title}</strong> "
            f"({currency}{total:,.2f}) on {signed_when}.</p>"
            "<p>Audit record captured at signing:</p>"
            f"<ul style=\"padding-left:20px;margin:0;\">{audit_lines}</ul>"
            "<p>The client has been sent a final copy with the signed PDF attached.</p>"
        )
        for email in sorted(contractor_emails):
            results[f"contractor:{email}"] = send_email(
                email, subject,
                _email_layout(org.name if org else "BuildUpQuote", "Proposal accepted ✔", body),
            )

    # --- Client confirmation with the signed PDF attached ---
    if client and (client.email or "").strip():
        pdf_bytes = None
        try:
            pdf_bytes = _quote_pdf_bytes(quote, org)
        except Exception as exc:  # noqa: BLE001 -- attach what we can, still confirm
            logger.error("could not render signed PDF for quote %s: %s", quote.id, exc)
        attachments = ()
        if pdf_bytes:
            attachments = ((f"{quote.title or 'proposal'}.pdf", pdf_bytes, "application", "pdf"),)
        subject = f"Proposal confirmed: {quote.title}"
        body = (
            f"<p>Thank you {client.name or ''} — your signed proposal for "
            f"<strong>{quote.title}</strong> is confirmed and locked in.</p>"
            "<p>Your final, signed copy is attached to this email for your records.</p>"
        )
        results[f"client:{client.email}"] = send_email(
            client.email, subject,
            _email_layout(org.name if org else "BuildUpQuote", "Proposal confirmed", body),
            attachments,
        )

    return results


# ---------------------------------------------------------------------------
# Background-task wrappers (for FastAPI BackgroundTasks)
# ---------------------------------------------------------------------------

def _load_quote(db, quote_id: int):
    from app import models  # local import: avoids a heavy import at module load

    return (
        db.query(models.Quote)
        .options(
            selectinload(models.Quote.items),
            selectinload(models.Quote.client),
        )
        .filter(models.Quote.id == quote_id)
        .first()
    )


def _load_org(db, quote):
    from app import models  # local import

    if not quote.organization_id:
        return None
    return (
        db.query(models.Organization)
        .options(selectinload(models.Organization.users))
        .filter(models.Organization.id == quote.organization_id)
        .first()
    )


def queue_send_quote_to_client(quote_id: int, message: str = "") -> None:
    """Background-task entry point: reload the quote in its own session (the
    request's session is closed by the time BackgroundTasks run) and send."""
    from app.database import SessionLocal  # local import

    db = SessionLocal()
    try:
        quote = _load_quote(db, quote_id)
        if quote is None:
            return
        send_quote_to_client(quote, quote.client, _load_org(db, quote), message)
    finally:
        db.close()


def queue_quote_accepted_notification(quote_id: int) -> None:
    """Background-task entry point for the post-signing emails."""
    from app.database import SessionLocal  # local import

    db = SessionLocal()
    try:
        quote = _load_quote(db, quote_id)
        if quote is None:
            return
        send_quote_accepted_notification(quote, quote.client, _load_org(db, quote))
    finally:
        db.close()



