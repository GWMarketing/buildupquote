"""Trade lexicon endpoints -- auto-tag descriptions with a trade."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services.lexicon_service import match_trade_from_description

router = APIRouter(prefix="/api/lexicon", tags=["lexicon"])


@router.post("/match")
def match_description(
    payload: schemas.LexiconMatchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """{"description": "12.5mm plasterboard install"} -> {"trade": "Drywall"}."""
    return {"trade": match_trade_from_description(payload.description, db)}
