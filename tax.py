"""Sales tax on the CONTRACTOR's own price.

This is a different question from the `tax` field kept on
scope_parser.models.LineItem -- that one is the carrier's own
estimate-internal tax figure, parsed only so the totals-consistency check
can use it, and never touched by this module. What this module answers
is: given the price the *contractor* is quoting, what (if anything) do
they need to charge the client in sales tax, on THIS job, in Texas --
which depends entirely on how the contract itself is written, not on
anything the carrier printed.

Rules come from the Texas Comptroller's own guidance (Publication 94-116)
on real property repair and remodeling, and are explained at length in
the project's "Known parsing problems / legal issues" doc, problem #4,
and in the "Claim Ledger" reference artifact:

  - Separated contract, residential: materials and labor are billed as
    separate line items. Tax is owed on materials only; labor on a
    residential job is never taxed, under either contract type.
  - Lump-sum contract, residential: one price for the whole job. The
    contractor pays tax when buying materials and does NOT separately
    itemize tax back to the client -- there is no client-facing tax line
    at all, even though tax was effectively paid.
  - Commercial / nonresidential: the entire charge -- materials AND
    labor -- is taxable, regardless of how the contract is written.
  - No sales tax: for anything outside Texas, or a tax-exempt situation
    (a contractor should confirm this with their own state's rules --
    this module only encodes the Texas residential/commercial split).

This is deliberately NOT a default-on behavior anywhere it's called from
(see app.py and proposal/build.py) -- a contractor has to pick a rule
before any tax gets added, so nothing changes silently for existing
proposals.
"""

SEPARATED_RESIDENTIAL = "separated_residential"
LUMP_SUM_RESIDENTIAL = "lump_sum_residential"
COMMERCIAL = "commercial"
NONE = "none"

TAX_RULE_LABELS = {
    SEPARATED_RESIDENTIAL: "Separated contract, residential (tax on materials only)",
    LUMP_SUM_RESIDENTIAL: "Lump-sum contract, residential (tax not billed separately)",
    COMMERCIAL: "Commercial / nonresidential (tax on materials and labor)",
    NONE: "No sales tax",
}

TAX_RULE_OPTIONS = [NONE, SEPARATED_RESIDENTIAL, LUMP_SUM_RESIDENTIAL, COMMERCIAL]

# Rules under which the client actually sees a separate, itemized tax
# line. A lump-sum residential contract IS taxed in effect -- the
# contractor already paid it when buying materials -- but the whole point
# of that contract type is that it is not itemized back to the client, so
# nothing shows on their copy.
ITEMIZES_TAX = {SEPARATED_RESIDENTIAL, COMMERCIAL}

DEFAULT_TEXAS_RATE_PCT = 8.25  # the commonly-quoted TX combined state+local ceiling; local rate varies 6.25-8.25%


def compute_sales_tax(rows, tax_rule, tax_rate_pct):
    """rows: an iterable of dicts, each with a "line_total" (float) key
    and, when the rule needs it, an "is_material" (bool) key (missing or
    True counts as material -- the safer default, since under-collecting
    tax is the costlier mistake for a contractor to make).

    Returns the tax amount to charge the client, rounded to the cent.
    Always 0.0 for a rule that doesn't itemize tax back to the client.
    """
    if tax_rule not in ITEMIZES_TAX:
        return 0.0
    rate = (tax_rate_pct or 0) / 100
    if tax_rule == COMMERCIAL:
        taxable = sum(r.get("line_total") or 0 for r in rows)
    else:  # SEPARATED_RESIDENTIAL
        taxable = sum(r.get("line_total") or 0 for r in rows if r.get("is_material", True))
    return round(taxable * rate, 2)
