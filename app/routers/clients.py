"""Client CRUD -- the "who is this quote for" records, tenant-scoped."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db

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
