"""Public, no-login quote approval: the shareable link a client opens to view
a proposal and digitally sign it (1-click acceptance), plus the signed PDF.

These routes deliberately have NO auth dependency -- the unguessable
public_uuid is the capability. Nothing about the contractor's workspace is
exposed: only the single quote's proposal view, its accept action, and its
own PDF download.
"""
import os
import tempfile
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.services import quote_pdf

router = APIRouter(tags=["public"])

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


def _get_quote_by_uuid(db: Session, public_uuid: str) -> models.Quote:
    quote = (
        db.query(models.Quote)
        .filter(models.Quote.public_uuid == public_uuid)
        .first()
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


def _organization_for(db: Session, quote: models.Quote):
    if not quote.organization_id:
        return None
    return (
        db.query(models.Organization)
        .filter(models.Organization.id == quote.organization_id)
        .first()
    )


@router.get("/view/quote/{public_uuid}")
def public_quote_view(
    public_uuid: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """The client-facing proposal page (no login). Unknown links render a
    friendly not-found state instead of a bare 404."""
    quote = (
        db.query(models.Quote)
        .filter(models.Quote.public_uuid == public_uuid)
        .first()
    )
    if quote is None:
        return templates.TemplateResponse(
            request, "public_quote_view.html", {"not_found": True}
        )
    organization = _organization_for(db, quote)
    return templates.TemplateResponse(request, "public_quote_view.html", {
        "quote": quote,
        "organization": organization,
        "client": quote.client,
        "lines": sorted(quote.items, key=lambda i: i.position or 0),
        "public_uuid": quote.public_uuid,
        "signature_uri": quote_pdf.signature_uri(quote.client_signature),
        "currency": (organization.currency_symbol
                     if organization and organization.currency_symbol else "£"),
    })


@router.post("/api/public/quotes/{public_uuid}/accept")
def public_quote_accept(
    public_uuid: str,
    payload: schemas.PublicQuoteAcceptRequest,
    db: Session = Depends(get_db),
):
    """The client's digital sign-off: persists the signature image, the signer
    name, and the acceptance timestamp; flips the quote to accepted."""
    quote = _get_quote_by_uuid(db, public_uuid)
    if quote.status == "accepted":
        return {"accepted": True, "already": True, "status": quote.status}
    if not (payload.signature_data or "").strip():
        raise HTTPException(status_code=400, detail="A signature is required")
    quote.status = "accepted"
    quote.client_signature = payload.signature_data.strip()
    quote.signed_by = (payload.client_name or "").strip() or None
    quote.accepted_at = func.now()
    db.commit()
    return {"accepted": True, "status": quote.status, "signed_by": quote.signed_by}


@router.get("/view/quote/{public_uuid}/download-pdf")
def public_quote_download_pdf(
    public_uuid: str,
    db: Session = Depends(get_db),
):
    """Render the branded PDF including the client's signature, if accepted."""
    quote = _get_quote_by_uuid(db, public_uuid)
    if not quote.items:
        raise HTTPException(status_code=400, detail="No line items to export")
    organization = _organization_for(db, quote)
    fd, out_path = tempfile.mkstemp(prefix=f"public-quote-{quote.id}-", suffix=".pdf")
    os.close(fd)
    context = {
        "quote": quote,
        "client": quote.client,
        "organization": organization,
        "estimator": None,
        "lines": sorted(quote.items, key=lambda i: i.position or 0),
        "today": time.strftime("%B %d, %Y"),
        "signature_uri": quote_pdf.signature_uri(quote.client_signature),
        "signed_by": quote.signed_by,
        "accepted_at": quote.accepted_at,
    }
    quote_pdf.render_quote_pdf(context, out_path)
    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename=f"quote-{quote.id}-signed.pdf",
    )
