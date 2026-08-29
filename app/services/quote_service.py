"""Shared quote-totals math (optional-add-on aware).

Used by the quotes router, the assemblies router, and the public accept
flow so the "grand total" is always computed the same way: required lines
plus any optional add-ons the client actually selected at signature.
"""
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import models


def recalculate_quote_totals(db: Session, quote: models.Quote) -> None:
    """Refresh a quote's subtotal/tax/total from its line items, its flat tax
    rate, and its contingency buffer.

    Optional add-ons (client upgrades) are EXCLUDED from the default subtotal
    unless the client selected them on the public proposal (recorded in
    quote.selected_optional_line_ids at signature), at which point they're
    folded in so the signed amount matches what the client agreed to."""
    selected = set(quote.selected_optional_line_ids or [])
    included = [
        models.QuoteLineItem.is_optional.is_(False),
        models.QuoteLineItem.is_optional.is_(None),
    ]
    if selected:
        included.append(models.QuoteLineItem.id.in_(selected))
    subtotal = (
        db.query(func.coalesce(func.sum(models.QuoteLineItem.line_total), 0))
        .filter(
            models.QuoteLineItem.quote_id == quote.id,
            or_(*included),
        )
        .scalar()
    )
    quote.subtotal = round(float(subtotal), 2)
    rate = float(quote.tax_rate_percent or 0)
    quote.tax_amount = round(quote.subtotal * rate / 100.0, 2)
    contingency = round(quote.subtotal * float(quote.contingency_percent or 0) / 100.0, 2)
    quote.total = round(quote.subtotal + quote.tax_amount + contingency, 2)
    db.add(quote)
