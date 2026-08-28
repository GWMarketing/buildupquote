"""Client CRUD -- the "who is this quote for" records, tenant-scoped.

Also hosts the contact importer: .vcf / .csv uploads and a free-form
quick-paste box both funnel through the same dedupe + persist helper.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, get_current_user
from app.database import get_db
from app.services import contact_service, google_contacts

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
    phone = contact_service.normalize_phone(get("phone"))
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
        contacts = contact_service.parse_vcard_data(raw)
    elif ext == ".csv":
        contacts = contact_service.parse_csv_contacts(raw)
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
    contacts = contact_service.parse_quick_text(payload.text)
    return _persist_contacts(db, current_user, contacts)


@router.post("/quick-parse-lead", response_model=schemas.ClientOut)
def quick_parse_lead(
    payload: schemas.LeadParseRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Single-lead upsert: parse pasted text and create the client, or return
    the existing one if the same contact is already on file (idempotent)."""
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before importing clients",
        )
    parsed = contact_service.parse_lead_text(payload.raw_text)
    # The parser falls back to a generic name; reject junk that has no real
    # contact signal (email/phone/labelled name).
    if not contact_service.has_contact_signal(payload.raw_text):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Couldn't find any contact details in that text",
        )
    result = _persist_contacts(db, current_user, [parsed])
    if result["created"]:
        return result["clients"][0]
    # Duplicate: hand back the matching existing client.
    key = _contact_key(parsed)
    existing = (
        db.query(models.Client)
        .filter(models.Client.organization_id == current_user.organization_id)
        .all()
    )
    for client in existing:
        if _contact_key(client) == key:
            return client
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Couldn't resolve that lead to a client",
    )


@router.post("/import-google-contacts", response_model=schemas.ClientImportResult)
def import_google_contacts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Pull the user's Google Contacts (People API) and persist them through
    the same dedupe as every other importer. Refreshes the OAuth access token
    once when the People API reports it stale."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Contacts sync is not configured",
        )
    if not (current_user.google_access_token and current_user.google_refresh_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect Google Contacts first — click Sync Google Contacts",
        )
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You need an organization before importing clients",
        )

    access_token = current_user.google_access_token
    data = None
    for attempt in range(2):
        try:
            data = google_contacts.fetch_contacts(access_token)
            break
        except google_contacts.GoogleContactsError as exc:
            if exc.status == 401 and attempt == 0:
                refreshed = google_contacts.refresh_access_token(
                    current_user.google_refresh_token,
                    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
                )
                access_token = refreshed.get("access_token")
                if not access_token:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Google Contacts session expired — reconnect from the Clients page",
                    )
                current_user.google_access_token = access_token
                db.commit()
                continue
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    contacts = google_contacts.map_people_to_contacts(data.get("connections", []))
    if not contacts:
        return {"created": 0, "skipped": 0, "clients": []}
    return _persist_contacts(db, current_user, contacts)
