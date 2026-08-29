"""Stripe billing: subscription checkout, webhooks, and the customer portal.

Mirrors the opt-in pattern used for Google Contacts: an empty
STRIPE_SECRET_KEY (or a missing price ID) cleanly disables the feature with a
503 instead of a traceback, so local dev and tests without Stripe keys keep
working.

Config (environment variables):
  STRIPE_SECRET_KEY            -- Stripe secret key (sk_live_... / sk_test_...)
  STRIPE_WEBHOOK_SECRET        -- signing secret for the /webhook endpoint
  STRIPE_PRICE_STARTER_MONTH / STRIPE_PRICE_STARTER_YEAR
  STRIPE_PRICE_PRO_MONTH / STRIPE_PRICE_PRO_YEAR
  STRIPE_PRICE_ENTERPRISE_MONTH / STRIPE_PRICE_ENTERPRISE_YEAR
  APP_BASE_URL                 -- public origin. TLS terminates at Caddy, so
                                 the app only ever sees plain http; Stripe
                                 requires absolute https success/cancel URLs.

The 7-day free trial is applied at checkout (subscription_data.trial_period_days)
and the tier/interval ride along in both the session and subscription metadata
so the webhook can update the right Organization without extra lookups.
"""
import os
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import get_db
from app.routers.organization import _get_or_provision_org

router = APIRouter(prefix="/api/billing", tags=["billing"])

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://glennwestman.com")

TRIAL_DAYS = 7

TIERS = {"starter", "pro", "enterprise"}
INTERVALS = {"month", "year"}

# Stripe subscription.status -> our vocabulary. unpaid/incomplete count as
# past_due (billing needs attention); expired trials are the same as canceled.
_STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
}


def _price_id(tier: str, interval: str) -> str:
    """STRIPE_PRICE_STARTER_MONTH -> 'price_...'. Empty when unset."""
    return os.getenv(f"STRIPE_PRICE_{tier.upper()}_{interval.upper()}", "")


def _require_stripe() -> None:
    """Fail cleanly when Stripe isn't configured (opt-in feature)."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")
    stripe.api_key = STRIPE_SECRET_KEY


def _org_by_subscription(db: Session, subscription_id: str):
    return (
        db.query(models.Organization)
        .filter(models.Organization.stripe_subscription_id == subscription_id)
        .first()
    )


def _org_from_checkout(db: Session, session_data: dict):
    """Map a Stripe Checkout Session object back to its Organization via the
    client_reference_id (primary) or session metadata (fallback)."""
    ref = session_data.get("client_reference_id")
    if ref:
        try:
            return db.query(models.Organization).filter(
                models.Organization.id == int(ref)
            ).first()
        except (TypeError, ValueError):
            pass
    meta = session_data.get("metadata") or {}
    org_id = meta.get("organization_id")
    if org_id:
        try:
            return db.query(models.Organization).filter(
                models.Organization.id == int(org_id)
            ).first()
        except (TypeError, ValueError):
            pass
    return None


class CheckoutRequest(BaseModel):
    tier: str
    interval: str = "month"


@router.post("/create-checkout-session")
def create_checkout_session(
    payload: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Start the 7-day-free-trial subscription checkout for the user's org."""
    tier = (payload.tier or "").strip().lower()
    interval = (payload.interval or "month").strip().lower()
    if tier not in TIERS:
        raise HTTPException(status_code=422, detail="tier must be starter, pro or enterprise")
    if interval not in INTERVALS:
        raise HTTPException(status_code=422, detail="interval must be month or year")
    _require_stripe()
    price = _price_id(tier, interval)
    if not price:
        raise HTTPException(
            status_code=503,
            detail=f"No Stripe price configured for the {tier} plan ({interval})",
        )

    org = _get_or_provision_org(db, current_user)
    checkout = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=current_user.email,
        line_items=[{"price": price, "quantity": 1}],
        success_url=f"{APP_BASE_URL}/dashboard?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_BASE_URL}/pricing?canceled=1",
        client_reference_id=str(org.id),
        metadata={
            "organization_id": str(org.id),
            "tier": tier,
            "interval": interval,
        },
        subscription_data={
            "trial_period_days": TRIAL_DAYS,
            "metadata": {
                "organization_id": str(org.id),
                "tier": tier,
                "interval": interval,
            },
        },
    )
    if getattr(checkout, "customer", None) and not org.stripe_customer_id:
        org.stripe_customer_id = checkout.customer
        db.add(org)
        db.commit()
    return {"url": checkout.url}


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Stripe webhook endpoint. Verifies the signature against the raw body,
    then keeps the organization's subscription fields in sync.

    Handled events:
      checkout.session.completed        -- attach customer + subscription to org
      customer.subscription.updated     -- tier/status/trial_end changes
      customer.subscription.deleted     -- mark the org canceled
    Anything else (invoice.*, payment_intent.*, ...) needs no action.
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Stripe billing is not configured")
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.get("type")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        org = _org_from_checkout(db, data)
        if org is None:
            return {"received": True, "ignored": "no_organization"}
        org.stripe_customer_id = data.get("customer") or org.stripe_customer_id
        org.stripe_subscription_id = data.get("subscription") or org.stripe_subscription_id
        meta = data.get("metadata") or {}
        if meta.get("tier"):
            org.subscription_tier = meta["tier"]
        # A fresh subscription always starts on its 7-day trial.
        org.subscription_status = "trialing"
        db.add(org)
        db.commit()
        return {"received": True}

    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        subscription_id = data.get("id")
        org = _org_by_subscription(db, subscription_id) if subscription_id else None
        if org is None:
            return {"received": True, "ignored": "no_subscription"}
        meta = data.get("metadata") or {}
        if meta.get("tier"):
            org.subscription_tier = meta["tier"]
        org.subscription_status = _STATUS_MAP.get(
            data.get("status", "active"), "active"
        )
        trial_end = data.get("trial_end")
        org.trial_ends_at = (
            datetime.fromtimestamp(trial_end, tz=timezone.utc) if trial_end else None
        )
        db.add(org)
        db.commit()
        return {"received": True}

    return {"received": True}


@router.post("/customer-portal")
def customer_portal(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """A Stripe Customer Portal link so contractors can manage payment
    methods, update billing details, or upgrade/downgrade their plan."""
    _require_stripe()
    org = current_user.organization
    if org is None or not org.stripe_customer_id:
        raise HTTPException(
            status_code=400,
            detail="No subscription yet — choose a plan before managing billing",
        )
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{APP_BASE_URL}/settings",
    )
    return {"url": session.url}

