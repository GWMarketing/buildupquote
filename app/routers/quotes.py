"""Quote CRUD, line-item persistence, and PDF export (tenant-scoped).

This is the API behind the quote builder page: create/list/detail quotes,
patch header fields, replace the line grid wholesale, and export a branded
PDF. The assembly math (apply-assembly) lives in app/routers/assemblies.py.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services import email_service, quote_pdf, quote_service

router = APIRouter(prefix="/api/quotes", tags=["quotes"])

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates"
)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

_EXPORT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "exports", "pdf",
)


def _get_owned_quote(db: Session, user: models.User, quote_id: int) -> models.Quote:
    quote = (
        db.query(models.Quote)
        .options(selectinload(models.Quote.items), selectinload(models.Quote.client))
        .filter(models.Quote.id == quote_id)
        .first()
    )
    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found")
    if quote.organization_id is not None and quote.organization_id != user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your organization's quote",
        )
    return quote


def _quote_out(quote: models.Quote) -> dict:
    return {
        "id": quote.id,
        "organization_id": quote.organization_id,
        "client_id": quote.client_id,
        "client_name": quote.client.name if quote.client is not None else None,
        "client_phone": quote.client.phone if quote.client is not None else None,
        "public_uuid": quote.public_uuid,
        "title": quote.title,
        "site_address": quote.site_address,
        "status": quote.status,
        "subtotal": float(quote.subtotal or 0),
        "tax_amount": float(quote.tax_amount or 0),
        "tax_rate_percent": float(quote.tax_rate_percent) if quote.tax_rate_percent is not None else None,
        "total": float(quote.total or 0),
        "include_contract": quote.include_contract if quote.include_contract is not None else True,
        "custom_contract_override": quote.custom_contract_override,
        "payment_schedule": quote.payment_schedule,
        "exclusions": quote.exclusions,
        "payment_instructions": quote.payment_instructions,
        "selected_optional_line_ids": quote.selected_optional_line_ids,
        "parent_quote_id": quote.parent_quote_id,
        "change_order_code": quote.change_order_code,
        "contingency_percent": float(quote.contingency_percent or 0),
        "contingency_visible": bool(quote.contingency_visible),
        "expiration_days": quote.expiration_days,
        "scope_dimensions": quote.scope_dimensions,
        "created_at": quote.created_at,
        "line_count": len(quote.items or []),
    }


def _quote_detail_out(quote: models.Quote, db: Session) -> dict:
    out = {
        **_quote_out(quote),
        "lines": [
            schemas.QuoteLineOut.model_validate(item)
            for item in sorted(quote.items, key=lambda i: i.position or 0)
        ],
    }
    # Change-order summary: base contract amount (parent total), this CO's
    # own total, and the revised project total.
    if quote.parent_quote_id:
        parent = db.query(models.Quote).filter(models.Quote.id == quote.parent_quote_id).first()
        base = float(parent.total or 0) if parent else 0.0
        co = float(quote.total or 0)
        out["base_amount"] = round(base, 2)
        out["co_total"] = round(co, 2)
        out["revised_total"] = round(base + co, 2)
    return out


def _recalculate_quote_totals(db: Session, quote: models.Quote) -> None:
    """Refresh a quote's subtotal/tax/total from its stored line items, its
    persisted flat tax rate, and its contingency buffer.

    Subtotal is the priced line-item total; tax applies to the subtotal; the
    contingency reserve (0-20%) is applied to the subtotal and added on top,
    so the grand total = subtotal + tax + contingency. Optional add-ons the
    client hasn't selected are excluded (see quote_service)."""
    quote_service.recalculate_quote_totals(db, quote)
    db.add(quote)


def _ensure_editable(quote: models.Quote) -> None:
    """Accepted quotes are a signed, locked record: the audit trail is only
    defensible if the quoted scope can't be rewritten after the client signs.
    Every mutation endpoint calls this before changing anything."""
    if quote.status == "accepted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This quote has been accepted and signed — it can no longer be edited",
        )

@router.get("", response_model=list[schemas.QuoteOut])
def list_quotes(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """This organization's quotes, newest first."""
    query = (
        db.query(models.Quote)
        .options(selectinload(models.Quote.items), selectinload(models.Quote.client))
        .order_by(models.Quote.created_at.desc())
    )
    if current_user.organization_id is not None:
        query = query.filter(models.Quote.organization_id == current_user.organization_id)
    return [_quote_out(q) for q in query.all()]


@router.post("", response_model=schemas.QuoteOut, status_code=status.HTTP_201_CREATED)
def create_quote(
    payload: schemas.QuoteCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before creating quotes",
        )
    quote = models.Quote(
        organization_id=current_user.organization_id,
        title=payload.title or "Untitled quote",
        client_id=payload.client_id,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    quote.items = []
    return _quote_out(quote)

def _row_truthy(value) -> bool:
    """Parsed rows carry Include/Material as True/False bools, but tolerate
    the 'TRUE'/'1' strings a hand-built payload might send."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "x")
    return bool(value)


def _parse_to_title(claim_fields: dict) -> str:
    """'Jane Doe / claim 123 / State Farm' -> 'Estimate — Jane Doe (Claim 123)'."""
    normalized = {
        k.lower(): str(v or "").strip() for k, v in (claim_fields or {}).items()
    }

    def first(*keys):
        for key in keys:
            for col, value in normalized.items():
                if key in col and value and value != "--":
                    return value
        return None

    holder = first("policyholder", "insured", "policy holder", "name")
    claim = first("claim number", "claim", "claim #", "file")
    carrier = first("carrier", "insurance company", "company")
    if holder and claim:
        return f"Estimate — {holder} (Claim {claim})"
    if holder:
        return f"Estimate — {holder}"
    if claim:
        return f"Claim {claim}"
    if carrier:
        return f"Insurance estimate — {carrier}"
    return "Insurance estimate"


@router.post("/from-parse", response_model=schemas.QuoteDetailOut,
             status_code=status.HTTP_201_CREATED)
def create_quote_from_parse(
    payload: schemas.ParseToQuoteRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Turn the insurance parser's output into a draft Quote: one line item
    per Included row, in order, with the parsed trade/qty/unit/unit cost and
    markup. Needs-review rows keep a '⚠' marker on their description."""
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before creating quotes",
        )
    if payload.client_id is not None:
        client = (
            db.query(models.Client)
            .filter(models.Client.id == payload.client_id)
            .first()
        )
        if client is None or client.organization_id != current_user.organization_id:
            raise HTTPException(status_code=404, detail="Client not found")

    quote = models.Quote(
        organization_id=current_user.organization_id,
        client_id=payload.client_id,
        title=_parse_to_title(payload.claim_fields),
    )
    db.add(quote)
    db.flush()

    position = 1
    for row in payload.rows:
        if not _row_truthy(row.get("Include")):
            continue
        description = str(row.get("Description") or "").strip()
        if not description:
            continue
        if _row_truthy(row.get("Needs Review")):
            description = "⚠ " + description
        try:
            quantity = float(row.get("Qty") or 0)
            unit_cost = float(row.get("Unit Cost") or 0)
            markup = float(row.get("Margin %") or 0)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Row values for Qty, Unit Cost and Margin % must be numbers",
            )
        line_total = round(quantity * unit_cost * (1 + markup / 100.0), 2)
        db.add(models.QuoteLineItem(
            quote_id=quote.id,
            trade=(str(row.get("Trade") or "").strip() or None),
            description=description,
            item_type="material" if _row_truthy(row.get("Material")) else "labor",
            quantity=quantity,
            unit=str(row.get("Unit") or "item").strip() or "item",
            unit_cost=unit_cost,
            markup_percent=markup,
            line_total=line_total,
            position=position,
        ))
        position += 1

    db.flush()  # totals SUM must see the new rows (autoflush is off)
    _recalculate_quote_totals(db, quote)
    db.commit()
    return _quote_detail_out(_get_owned_quote(db, current_user, quote.id), db)




@router.get("/{quote_id}", response_model=schemas.QuoteDetailOut)
def get_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return _quote_detail_out(_get_owned_quote(db, current_user, quote_id), db)


@router.patch("/{quote_id}", response_model=schemas.QuoteOut)
def update_quote(
    quote_id: int,
    payload: schemas.QuoteUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    quote = _get_owned_quote(db, current_user, quote_id)
    _ensure_editable(quote)
    for field in ("title", "site_address", "status", "client_id", "tax_rate_percent",
                  "include_contract", "custom_contract_override",
                  "contingency_percent", "contingency_visible",
                  "expiration_days", "exclusions", "payment_instructions"):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(quote, field, value)
    if payload.payment_schedule is not None:
        quote.payment_schedule = [
            {"label": (m.label or "").strip() or "Stage", "percent": round(float(m.percent or 0), 2)}
            for m in payload.payment_schedule
        ]
    if (payload.tax_rate_percent is not None
            or payload.contingency_percent is not None
            or payload.contingency_visible is not None):
        _recalculate_quote_totals(db, quote)
    db.commit()
    db.refresh(quote)
    return _quote_out(quote)


@router.put("/{quote_id}/lines", response_model=schemas.QuoteDetailOut)
def save_lines(
    quote_id: int,
    payload: list[schemas.QuoteLineWrite],
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Replace the quote's line grid wholesale and refresh totals."""
    quote = _get_owned_quote(db, current_user, quote_id)
    _ensure_editable(quote)
    quote.items.clear()
    db.flush()
    for position, line in enumerate(payload, start=1):
        line_total = round(
            float(line.quantity) * float(line.unit_cost) * (1 + float(line.markup_percent) / 100.0),
            2,
        )
        db.add(models.QuoteLineItem(
            quote_id=quote.id,
            trade=line.trade,
            description=line.description,
            item_type=line.item_type,
            quantity=line.quantity,
            unit=line.unit,
            unit_cost=line.unit_cost,
            markup_percent=line.markup_percent,
            line_total=line_total,
            position=position,
            is_optional=bool(line.is_optional),
        ))
    db.flush()
    _recalculate_quote_totals(db, quote)
    db.commit()
    # Re-query so the response reflects the freshly written lines (rows
    # were added by FK, not via the relationship collection).
    return _quote_detail_out(_get_owned_quote(db, current_user, quote_id), db)


@router.delete("/{quote_id}")
def delete_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete a quote: cascade its line items (ORM + ON DELETE CASCADE)
    and clean up any exported PDFs for it. 403 for another org's quote."""
    quote = _get_owned_quote(db, current_user, quote_id)
    _ensure_editable(quote)
    # Remove exported PDFs for this quote (static/exports/pdf/quote-{id}-*.pdf).
    if os.path.isdir(_EXPORT_DIR):
        for name in os.listdir(_EXPORT_DIR):
            if name.startswith(f"quote-{quote_id}-") and name.endswith(".pdf"):
                try:
                    os.unlink(os.path.join(_EXPORT_DIR, name))
                except OSError:
                    pass
    db.delete(quote)
    db.commit()
    return {"deleted": True}


@router.delete("/{quote_id}/lines/{line_id}")
def delete_quote_line(
    quote_id: int,
    line_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Remove one line item, then recalculate the quote totals."""
    quote = _get_owned_quote(db, current_user, quote_id)
    _ensure_editable(quote)
    line = (
        db.query(models.QuoteLineItem)
        .filter(
            models.QuoteLineItem.id == line_id,
            models.QuoteLineItem.quote_id == quote.id,
        )
        .first()
    )
    if line is None:
        raise HTTPException(status_code=404, detail="Line item not found")
    # Drop it from the in-memory collection too, so the later
    # db.add(quote) cascade never touches a deleted instance.
    quote.items = [item for item in (quote.items or []) if item.id != line.id]
    db.delete(line)
    db.flush()
    _recalculate_quote_totals(db, quote)
    db.commit()
    return _quote_detail_out(_get_owned_quote(db, current_user, quote_id), db)


@router.get("/{quote_id}/pdf")
def export_quote_pdf(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Render the quote to a branded PDF under /static/exports/pdf and
    return the URL (kept for the page's download-history list)."""
    quote = _get_owned_quote(db, current_user, quote_id)
    if not quote.items:
        raise HTTPException(status_code=400, detail="Add line items before exporting")

    os.makedirs(_EXPORT_DIR, exist_ok=True)
    filename = f"quote-{quote.id}-{int(time.time())}.pdf"
    out_path = os.path.join(_EXPORT_DIR, filename)
    _currency = (current_user.organization.currency_symbol
                 if current_user.organization and current_user.organization.currency_symbol else "$")
    _include, _contract = quote_pdf.contract_for_quote(
        quote, current_user.organization, _currency, time.strftime("%d %b %Y"),
    )
    context = {
        "quote": quote,
        "client": quote.client,
        "organization": current_user.organization,
        "estimator": current_user,
        "lines": sorted(quote.items, key=lambda i: i.position or 0),
        "today": time.strftime("%B %d, %Y"),
        "signature_uri": quote_pdf.signature_uri(quote.client_signature),
        "signed_by": quote.signed_by,
        "signer_ip": quote.signer_ip,
        "accepted_at": quote.accepted_at,
        "include_contract": _include,
        "contract_text": _contract,
    }
    quote_pdf.render_quote_pdf(context, out_path)
    return {"url": f"/static/exports/pdf/{filename}", "filename": filename}


@router.post("/{quote_id}/change-orders", response_model=schemas.QuoteOut,
             status_code=status.HTTP_201_CREATED)
def create_change_order(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """One-click change order on an accepted (signed) quote.

    Creates a linked child quote (CO1, CO2...) inheriting the client and
    base project info, with its own line items, assemblies, and an
    independent digital sign-off link (its own public_uuid)."""
    parent = _get_owned_quote(db, current_user, quote_id)
    if parent.status != "accepted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Change orders can only be added to accepted (signed) quotes",
        )
    co_number = (
        db.query(func.count(models.Quote.id))
        .filter(models.Quote.parent_quote_id == parent.id)
        .scalar()
    ) + 1

    change_order = models.Quote(
        organization_id=parent.organization_id,
        client_id=parent.client_id,
        title=f"{parent.title or 'Quote'} — Change Order CO{co_number}",
        site_address=parent.site_address,
        status="draft",
        parent_quote_id=parent.id,
        change_order_code=f"CO{co_number}",
        include_contract=parent.include_contract if parent.include_contract is not None else True,
        custom_contract_override=parent.custom_contract_override,
        payment_schedule=parent.payment_schedule,
        contingency_percent=parent.contingency_percent or 0,
        contingency_visible=bool(parent.contingency_visible),
    )
    db.add(change_order)
    db.commit()
    db.refresh(change_order)
    return _quote_out(change_order)


@router.post("/{quote_id}/send-email")
def send_quote_email(
    quote_id: int,
    payload: schemas.SendQuoteEmailRequest = schemas.SendQuoteEmailRequest(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Email the proposal to the linked client.

    Requires a client with an email address and at least one line item.
    Flips the quote to 'sent' (draft -> sent; accepted quotes stay locked)
    and dispatches the branded proposal email in the background so the
    request returns instantly. `delivered` reports whether SMTP is actually
    configured (so the UI can say 'sent' vs 'queued')."""
    quote = _get_owned_quote(db, current_user, quote_id)
    if not quote.items:
        raise HTTPException(status_code=400, detail="Add line items before sending")
    client = quote.client
    if client is None or not (client.email or "").strip():
        raise HTTPException(
            status_code=400,
            detail="This quote has no client with an email address",
        )
    if quote.status == "draft":
        quote.status = "sent"
        db.add(quote)
        db.commit()

    background_tasks.add_task(
        email_service.queue_send_quote_to_client,
        quote.id,
        (payload.message or "").strip(),
    )
    return {
        "ok": True,
        "status": quote.status,
        "delivered": email_service.is_configured(),
        "to": client.email,
    }


def _takeoff_groups(lines) -> list:
    """Aggregate MATERIAL quantities into cut-list groups (description + qty +
    unit only -- no pricing), the server-side twin of the cockpit's takeoff."""

    def category(row):
        t = (((row.trade or "") + " " + (row.description or "")).lower())
        if re.search(r"(carpentry|framing|lumber|stud|joist|timber|track|runner)", t):
            return "Lumber / Framing"
        if re.search(r"(drywall|plasterboard|gypsum|board)", t):
            return "Drywall"
        if re.search(r"(tile|grout|adhesive|paint|primer|finish)", t):
            return "Finishes"
        if re.search(r"(screw|nail|fastener|fixing|anchor)", t):
            return "Fasteners"
        return "General Materials"

    map_groups = {}
    for row in lines:
        if row.item_type != "material" or not (float(row.quantity or 0) > 0):
            continue
        cat = category(row)
        key = (f"{(row.description or '').strip()}|{row.unit or ''}").lower()
        map_groups.setdefault(cat, {})
        entry = map_groups[cat].setdefault(key, {
            "description": row.description, "unit": row.unit, "quantity": 0.0,
        })
        entry["quantity"] += float(row.quantity or 0)
    return [
        {"category": cat, "rows": list(rows.values())}
        for cat, rows in map_groups.items()
    ]


@router.get("/{quote_id}/work-order", include_in_schema=False)
def quote_work_order(
    quote_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The sanitized Crew Work Order: a clean printable view for field trades
    with project info, room dimensions, the scope checklist, and the material
    cut list -- and NOTHING financial (no prices, margins, or totals)."""
    quote = _get_owned_quote(db, current_user, quote_id)
    if not quote.items:
        raise HTTPException(status_code=400, detail="Add line items before printing a work order")

    lines = sorted(quote.items, key=lambda i: i.position or 0)
    scope_by_trade = {}
    for line in lines:
        scope_by_trade.setdefault(line.trade or "General", []).append({
            "description": line.description,
            "item_type": line.item_type,
            "quantity": line.quantity,
            "unit": line.unit,
        })

    pricing_valid_through = None
    if quote.expiration_days and quote.created_at:
        created = quote.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        pricing_valid_through = (
            created + timedelta(days=int(quote.expiration_days))
        ).strftime("%d %b %Y")

    return templates.TemplateResponse(request, "work_order.html", {
        "quote": quote,
        "organization": current_user.organization,
        "client": quote.client,
        "site_address": (
            quote.site_address
            or (quote.client.site_address if quote.client else None)
            or ""
        ),
        "dimensions": quote.scope_dimensions or {},
        "scope_by_trade": scope_by_trade,
        "takeoff": _takeoff_groups(lines),
        "pricing_valid_through": pricing_valid_through,
    })


@router.get("/{quote_id}/export-pdf")
def export_quote_pdf_download(
    quote_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Render the branded PDF and serve it as a direct download attachment
    (the browser save-as path; the page records it in its download list)."""
    quote = _get_owned_quote(db, current_user, quote_id)
    if not quote.items:
        raise HTTPException(status_code=400, detail="Add line items before exporting")

    os.makedirs(_EXPORT_DIR, exist_ok=True)
    filename = f"quote-{quote.id}-{int(time.time())}.pdf"
    out_path = os.path.join(_EXPORT_DIR, filename)
    _currency = (current_user.organization.currency_symbol
                 if current_user.organization and current_user.organization.currency_symbol else "$")
    _include, _contract = quote_pdf.contract_for_quote(
        quote, current_user.organization, _currency, time.strftime("%d %b %Y"),
    )
    context = {
        "quote": quote,
        "client": quote.client,
        "organization": current_user.organization,
        "estimator": current_user,
        "lines": sorted(quote.items, key=lambda i: i.position or 0),
        "today": time.strftime("%B %d, %Y"),
        "signature_uri": quote_pdf.signature_uri(quote.client_signature),
        "signed_by": quote.signed_by,
        "signer_ip": quote.signer_ip,
        "accepted_at": quote.accepted_at,
        "include_contract": _include,
        "contract_text": _contract,
    }
    quote_pdf.render_quote_pdf(context, out_path)
    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename=filename,
    )

