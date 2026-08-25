"""Pure pricing math, kept separate from app.py so it can be unit tested
without needing Streamlit installed (this sandbox couldn't reach PyPI to
install Streamlit -- see README's "Testing note" section for the full
story). No dependencies here beyond the standard library.
"""


def compute_line_total(qty, unit_cost, margin_pct):
    """What the contractor charges for one line: quantity x unit cost,
    with the margin % applied on top.

    Missing/blank values (a brand-new row the contractor just added and
    hasn't filled in yet) are treated as 0 rather than raising, since the
    editing screen needs to keep working while a row is half-filled-in.
    """
    qty = qty or 0
    unit_cost = unit_cost or 0
    margin_pct = margin_pct or 0
    return round(qty * unit_cost * (1 + margin_pct / 100), 2)
