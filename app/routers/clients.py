"""Client CRUD -- the "who is this quote for" records, tenant-scoped.

Also hosts the contact importer: .vcf / .csv uploads and a free-form
quick-paste box both funnel through the same dedupe + persist helper.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services import contact_import

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.get("", response_model=list[schemas.ClientOut])
def list_clients(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """This organization's clients, alphabetical."""
    return (
        db.query(models.Client)
        .filter(models.Client.organization_id == current_user.organization_id)
        .order_by(models.Client.name)
        .all()
    )


@router.post("", response_model=schemas.ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(
    payload: schemas.ClientCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before adding clients",
        )
    client = models.Client(
        organization_id=current_user.organization_id,
        name=payload.name.strip(),
        site_address=payload.site_address,
        phone=payload.phone,
        email=payload.email,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


@router.delete("/{client_id}")
def delete_client(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Delete a client -- but only if nothing references it. A 400 with a
    clean message protects quotes that still point at this client."""
    client = (
        db.query(models.Client)
        .filter(models.Client.id == client_id)
        .first()
    )
    if client is None or client.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    quote_count = (
        db.query(func.count(models.Quote.id))
        .filter(models.Quote.client_id == client.id)
        .scalar()
    )
    if quote_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Client has {quote_count} active quote(s); delete or reassign them first.",
        )
    db.delete(client)
    db.commit()
    return {"deleted": True}


def _normalize_email(email):
    return (email or "").strip().lower()


def _contact_key(contact):
    """Dedupe identity: email wins, then phone, then lowercased name.
    Accepts either a parsed contact dict or an existing Client ORM row."""

    def get(field):
        if isinstance(contact, dict):
            return contact.get(field)
        return getattr(contact, field, None)

    email = _normalize_email(get("email"))
    if email:
        return "e:" + email
    phone = contact_import.normalize_phone(get("phone"))
    if phone:
        return "p:" + phone
    name = (get("name") or "").strip().lower()
    if name:
        return "n:" + name
    return None


def _persist_contacts(db: Session, user: models.User, contacts: list[dict]) -> dict:
    """Create new Client rows for contacts that don't already exist in this
    organization. Returns {created, skipped, clients}."""
    existing = (
        db.query(models.Client)
        .filter(models.Client.organization_id == user.organization_id)
        .all()
    )
    known = {_contact_key(c) for c in existing}
    known.discard(None)

    created = []
    skipped = 0
    for contact in contacts:
        if not (contact.get("name") or contact.get("email") or contact.get("phone")):
            skipped += 1
            continue
        key = _contact_key(contact)
        if key and key in known:
            skipped += 1
            continue
        if key:
            known.add(key)
        client = models.Client(
            organization_id=user.organization_id,
            name=(contact.get("name") or contact.get("email")
                  or contact.get("phone") or "Unnamed").strip(),
            site_address=contact.get("site_address"),
            phone=contact.get("phone"),
            email=contact.get("email"),
        )
        db.add(client)
        created.append(client)
    db.commit()
    for client in created:
        db.refresh(client)
    return {"created": len(created), "skipped": skipped, "clients": created}


@router.post("/import-file", response_model=schemas.ClientImportResult)
async def import_clients_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Upload a .vcf or .csv of contacts and create a Client per row.
    Duplicates (same email/phone/name) are skipped, not erroring out."""
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before importing clients",
        )
    raw = (await file.read()).decode("utf-8", errors="replace")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext == ".vcf":
        contacts = contact_import.parse_vcard(raw)
    elif ext == ".csv":
        contacts = contact_import.parse_csv(raw)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type -- upload a .vcf or .csv file",
        )
    return _persist_contacts(db, current_user, contacts)


@router.post("/quick-parse-text", response_model=schemas.ClientImportResult)
def quick_parse_clients(
    payload: schemas.QuickParseRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Free-form pasted lead text (one contact per blank line) -> clients."""
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before importing clients",
        )
    contacts = contact_import.parse_quick_text(payload.text)
    return _persist_contacts(db, current_user, contacts)
