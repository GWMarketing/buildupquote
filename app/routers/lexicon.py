"""Trade lexicon endpoints -- auto-tag descriptions with a trade and search
the multi-trade voice/autocomplete lexicon."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import get_current_user
from app.database import get_db
from app.services.lexicon_service import match_trade_from_description
from app.services.trade_lexicon_service import search_trade_lexicon

router = APIRouter(prefix="/api/lexicon", tags=["lexicon"])


@router.post("/match")
def match_description(
    payload: schemas.LexiconMatchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """{"description": "1/2\" plasterboard install"} -> {"trade": "Drywall"}."""
    return {"trade": match_trade_from_description(payload.description, db)}


@router.get("/search")
def search_lexicon(
    q: str = Query("", max_length=80),
    trade: str | None = Query(None, max_length=40),
    limit: int = Query(25, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Full-text / fuzzy search over the multi-trade lexicon. Returns
    spec-shaped rows: {id, uuid, trade_category, canonical_term,
    spoken_aliases, phonetic_respelling, ipa_pronunciation,
    common_misspellings_typos, unit_of_measure, definition_and_use}.
    Optional `trade` filter ("Plumbing") narrows the results."""
    return {
        "query": q.strip(),
        "trade": trade,
        "count": None,
        "results": search_trade_lexicon(db, q, trade=trade, limit=limit),
    }
