"""Builds a ProposalData from the rows produced by the editing workspace
(app.py) plus the claim metadata the parsing engine extracted. Kept
separate from render.py so this logic -- the part with any real risk of
mistakes -- is testable without needing wkhtmltopdf/pdfkit installed.
"""
from collections import OrderedDict

from pricing import compute_line_total
from tax import ITEMIZES_TAX, NONE as NO_TAX, TAX_RULE_LABELS, compute_sales_tax

from .models import ClaimInfo, ContractorInfo, ProposalData, ProposalLineItem, TradeGroup


def claim_info_from_metadata(fields: dict) -> ClaimInfo:
    return ClaimInfo(
        insured_name=fields.get("insured_name", ""),
        property_address=fields.get("property_address", ""),
        insurance_company=fields.get("insurance_company") or fields.get("company", ""),
        claim_number=fields.get("claim_number", ""),
        policy_number=fields.get("policy_number", ""),
        type_of_loss=fields.get("type_of_loss", ""),
        date_of_loss=fields.get("date_of_loss", ""),
    )


def _num_or_zero(value):
    """Handles None and pandas NaN without importing pandas here (build.py
    stays pure-Python/pandas-agnostic on purpose -- it's the one part of
    this pipeline testable without a DataFrame at all)."""
    if value is None:
        return 0.0
    if isinstance(value, float) and value != value:  # NaN
        return 0.0
    return float(value)


def _is_blank(value):
    """None or NaN -- used to recognize a supplement line by its
    "Insurance RCV" being blank (see build_proposal()'s docstring). A
    real carrier line always has a value there, even $0.00; only a
    hand-added row (no carrier line to reference) leaves it empty."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return False


def group_line_items(items):
    """Groups ProposalLineItems by trade, preserving first-seen order,
    with a subtotal per group."""
    groups = OrderedDict()
    for item in items:
        groups.setdefault(item.trade, []).append(item)
    return [
        TradeGroup(
            trade=trade,
            items=group_items,
            subtotal=round(sum(i.line_total for i in group_items), 2),
        )
        for trade, group_items in groups.items()
    ]


def build_proposal(rows, contractor: ContractorInfo, claim_fields: dict,
                    proposal_date: str, proposal_number: str = "",
                    tax_rule: str = NO_TAX, tax_rate_pct: float = 0.0,
                    deductible_amount: float = 0.0) -> ProposalData:
    """rows: an iterable of dicts (or a pandas DataFrame's
    .to_dict("records")) shaped like app.py's editor table -- Trade,
    Description, Qty, Unit, Unit Cost, Margin %, Include, and (for tax
    purposes) Material. Rows where Include is falsy are skipped. Rows
    missing Description or with a non-positive quantity are skipped too
    (an empty row a contractor added and hasn't filled in yet shouldn't
    show up as a blank line on the proposal).

    tax_rule/tax_rate_pct: see tax.py. Default is tax.NONE / 0%, so a
    caller that doesn't pass these gets the exact same total_price as
    before this parameter existed -- nothing changes for existing
    proposals unless a tax rule is explicitly chosen.

    deductible_amount: see ProposalData's payment-breakdown fields and
    app.py's _payment_breakdown() -- the same spec, computed here again
    from `rows` directly (rather than sharing one function) since this
    operates on plain dicts, not a pandas DataFrame. first_check_amount is
    always the REMAINDER of total minus the other three parts, so the
    four payment-breakdown fields sum exactly to total_price no matter
    what deductible_amount is passed -- confirmed with the user this beats
    computing it independently, which could drift out of sync.
    """
    items = []
    recoverable_depreciation_total = 0.0
    supplements_total = 0.0
    for r in rows:
        if not r.get("Include", True):
            continue
        description = (r.get("Description") or "").strip()
        qty = r.get("Qty") or 0
        if not description or not qty:
            continue
        unit_cost = r.get("Unit Cost") or 0
        margin = r.get("Margin %") or 0
        line_total = compute_line_total(qty, unit_cost, margin)
        unit_price = round(unit_cost * (1 + margin / 100), 2)
        items.append(ProposalLineItem(
            trade=r.get("Trade") or "Other",
            description=description,
            quantity=qty,
            unit=r.get("Unit") or "",
            unit_price=unit_price,
            line_total=line_total,
            is_material=r.get("Material", True),
        ))
        recoverable_depreciation_total += _num_or_zero(r.get("Recoverable Depreciation"))
        if _is_blank(r.get("Insurance RCV")):
            supplements_total += line_total
    grouped = group_line_items(items)
    subtotal = round(sum(g.subtotal for g in grouped), 2)
    tax_amount = compute_sales_tax(
        [{"line_total": i.line_total, "is_material": i.is_material} for i in items],
        tax_rule, tax_rate_pct,
    )
    tax_label = TAX_RULE_LABELS[tax_rule] if tax_rule in ITEMIZES_TAX else ""
    total = round(subtotal + tax_amount, 2)
    deductible_amount = round(_num_or_zero(deductible_amount), 2)
    recoverable_depreciation_total = round(recoverable_depreciation_total, 2)
    supplements_total = round(supplements_total, 2)
    first_check_amount = round(
        total - deductible_amount - recoverable_depreciation_total - supplements_total, 2
    )
    return ProposalData(
        contractor=contractor,
        claim=claim_info_from_metadata(claim_fields),
        grouped_items=grouped,
        total_price=total,
        proposal_date=proposal_date,
        proposal_number=proposal_number,
        subtotal=subtotal,
        tax_amount=tax_amount,
        tax_label=tax_label,
        deductible_amount=deductible_amount,
        first_check_amount=first_check_amount,
        recoverable_depreciation_amount=recoverable_depreciation_total,
        supplements_amount=supplements_total,
    )
