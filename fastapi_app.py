"""BuildUpQuote -- FastAPI deployment for the Hostinger VPS.

Serves the parsing / pricing / proposal engine as a REST API plus a
self-contained web page (web/index.html). The web page is the only app
interface; no other app framework is involved.

Run with:  uvicorn fastapi_app:app --host 0.0.0.0 --port 8000

The pure row/totals logic is imported from workspace.py -- plain pandas
and Python, no framework, so it imports anywhere.
"""
import base64
import datetime
import io
import os
import tempfile
from contextlib import asynccontextmanager
from typing import Optional

import pandas as pd

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

import tax
import workspace  # noqa: E402 -- pure logic, no framework
import app.models  # noqa: E402 -- registers the User table with Base.metadata
from app.database import Base, engine, ensure_legacy_columns, get_db
from app.routers import auth as auth_router
from app.routers import organization as organization_router
from proposal import ContractorInfo, build_proposal, render_proposal_pdf
from scope_parser import parse_pdf
from trades import TRADE_OPTIONS


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create tables on startup (idempotent). If postgres isn't reachable
    yet -- local dev without a database, or the db container still doing
    its first-time initialization on deploy -- log and continue: the
    app's core (parse/totals/proposal) doesn't need the DB, and
    /api/db-check reports the real state."""
    try:
        Base.metadata.create_all(bind=engine)
        ensure_legacy_columns(engine)
    except Exception as exc:  # noqa: BLE001 -- surfaced via /api/db-check
        print(f"[startup] database not reachable, skipping table creation: {exc}")
    yield


app = FastAPI(title="BUILDUPQUOTE", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router.router)
app.include_router(organization_router.router)

_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


# ---------------------------------------------------------------------------
# request payloads
# ---------------------------------------------------------------------------
class BusinessInfo(BaseModel):
    name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    license_number: str = ""
    logo_data_url: str = ""  # optional "data:image/png;base64,..."


class TotalsRequest(BaseModel):
    rows: list
    tax_rule: str = tax.NONE
    tax_rate_pct: float = 0.0


class ProposalRequest(BaseModel):
    rows: list
    business: BusinessInfo = BusinessInfo()
    claim_fields: dict = {}
    tax_rule: str = tax.NONE
    tax_rate_pct: float = 0.0
    deductible: Optional[float] = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _frame(records):
    """JSON rows -> the canonical workspace DataFrame (full column set)."""
    if not records:
        return pd.DataFrame(columns=workspace._TABLE_COLUMNS)
    frame = pd.DataFrame(records)
    for col in workspace._TABLE_COLUMNS:
        if col not in frame.columns:
            frame[col] = None
    return frame[workspace._TABLE_COLUMNS]


def _json_safe(frame):
    """DataFrame -> list of dicts with NaN turned into None (valid JSON)."""
    out = frame.to_dict("records")
    for rec in out:
        for key, value in list(rec.items()):
            if isinstance(value, float) and value != value:  # NaN
                rec[key] = None
    return out


def _save_logo(data_url):
    """Decode an optional logo data-URL into a temp file for the PDF."""
    if not data_url or "," not in data_url:
        return None
    header, _, b64 = data_url.partition(",")
    try:
        raw = base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return None
    lower = header.lower()
    ext = "jpg" if ("jpeg" in lower or "jpg" in lower) else "png"
    fd, path = tempfile.mkstemp(suffix=f".{ext}")
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return path


def _carrier_summary_dict(estimate):
    cs = getattr(estimate, "carrier_summary", None)
    if cs is None or not getattr(cs, "has_content", False):
        return None
    return {
        "line_item_total": cs.line_item_total,
        "overhead_pct": cs.overhead_pct,
        "profit_pct": cs.profit_pct,
        "replacement_cost_value": cs.replacement_cost_value,
        "deductible": cs.deductible,
        "net_claim": cs.net_claim,
        "coverage_label": cs.coverage_label,
    }


# ---------------------------------------------------------------------------
# pages & endpoints
# ---------------------------------------------------------------------------
@app.get("/", response_class=FileResponse)
async def index():
    return os.path.join(_WEB_DIR, "index.html")


@app.get("/api/meta")
async def meta():
    """Tax rules, trade options, and the default Texas rate -- so the page
    can build its selects without hard-coding them."""
    return {
        "tax_rules": tax.TAX_RULE_LABELS,
        "tax_rule_options": tax.TAX_RULE_OPTIONS,
        "itemizes_tax": sorted(tax.ITEMIZES_TAX),
        "trade_options": TRADE_OPTIONS,
        "default_tax_rate_pct": tax.DEFAULT_TEXAS_RATE_PCT,
    }


@app.get("/api/db-check")
async def db_check(db: Session = Depends(get_db)):
    """Postgres reachability: SELECT 1 -> {"database": "connected"}.

    503 with {"database": "disconnected"} when the database is down -- the
    rest of the API keeps working either way."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 -- the check itself is the feature
        raise HTTPException(status_code=503, detail={"database": "disconnected"})
    return {"database": "connected"}


@app.post("/api/parse")
async def parse_pdf_upload(file: UploadFile = File(...)):
    """Upload a carrier estimate PDF -> parsed line items + claim info."""
    raw = await file.read()
    try:
        estimate = parse_pdf(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001 -- surfaced to the page
        raise HTTPException(status_code=422, detail=f"Could not read this PDF: {exc}")
    rows = workspace._rows_from_estimate(estimate, default_margin=20)
    return {
        "rows": _json_safe(rows),
        "claim_fields": dict(estimate.metadata.fields),
        "warnings": estimate.warnings,
        "needs_review_count": len(estimate.needs_review_items),
        "document_type": getattr(estimate.document_type, "label", ""),
        "deductible": workspace._effective_deductible(estimate),
        "carrier_summary": _carrier_summary_dict(estimate),
    }


@app.post("/api/totals")
async def totals(req: TotalsRequest):
    """Live quote totals for whatever rows the page currently holds -- the
    same math as the dashboard's sticky totals bar."""
    rows = _frame(req.rows)
    subtotal, tax_amount, total, count = workspace._quote_totals(
        rows, req.tax_rule, req.tax_rate_pct
    )
    included = workspace._priced(rows[rows["Include"].fillna(False)])
    return {
        "subtotal": round(subtotal, 2),
        "tax": round(tax_amount, 2),
        "total": round(total, 2),
        "count": count,
        "trades": _json_safe(workspace._trade_totals(included)),
    }


@app.post("/api/csv")
async def csv_export(req: ProposalRequest):
    """The currently-included lines, as a downloadable CSV."""
    rows = _frame(req.rows)
    included = rows[rows["Include"].fillna(False)]
    csv_bytes = included.drop(columns=["Needs Review", "Review Note"], errors="ignore").to_csv(index=False)
    filename = workspace._export_basename(req.business.name, req.claim_fields) + ".csv"
    return Response(
        csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/proposal")
async def proposal_export(req: ProposalRequest):
    """The branded proposal PDF, built from the rows exactly like the
    dashboard's "Branded proposal PDF" button."""
    rows = _frame(req.rows)
    logo_path = _save_logo(req.business.logo_data_url)
    try:
        contractor = ContractorInfo(
            name=req.business.name,
            address=req.business.address,
            phone=req.business.phone,
            email=req.business.email,
            license_number=req.business.license_number,
            logo_path=logo_path,
        )
        deductible = req.deductible if req.deductible is not None else 0.0
        data = build_proposal(
            rows.to_dict("records"),
            contractor,
            req.claim_fields,
            datetime.date.today().strftime("%m/%d/%Y"),
            tax_rule=req.tax_rule,
            tax_rate_pct=req.tax_rate_pct,
            deductible_amount=deductible,
        )
        out_path = os.path.join(tempfile.gettempdir(), "buildupquote_proposal.pdf")
        render_proposal_pdf(data, out_path)
        with open(out_path, "rb") as fh:
            pdf_bytes = fh.read()
    finally:
        if logo_path:
            try:
                os.remove(logo_path)
            except OSError:
                pass
    filename = workspace._export_basename(req.business.name, req.claim_fields) + ".pdf"
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

