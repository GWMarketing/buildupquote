"""BuildUpQuote -- the framework-free core of the scope editing workspace.

All the pure logic of the editing workspace. This module has NO framework
dependencies: it is pure pandas + plain Python, so the FastAPI deployment
(fastapi_app.py) and the test suite import it on any box.

Everything here is a decision -- row shaping, pricing math, totals,
payment breakdown, file-name building, search matching. Rendering lives
elsewhere: fastapi_app.py + web/index.html for the REST deployment.

Flow: upload a carrier estimate PDF -> the parsing engine (scope_parser/)
reads it -> workspace shapes the rows (_rows_from_estimate) -> the API
serves edits, totals (_quote_totals), the payment breakdown
(_payment_breakdown), and the proposal export (proposal/).

The insurance-only columns (depreciation, ACV, deductible) are deliberately
reference figures, never part of what the contractor charges -- per the
project's legal notes, what the contractor charges is never "RCV minus
depreciation". The exported proposal PDF is stricter still: its data model
(proposal/models.py) has no field for those numbers at all.

Sales tax (see tax.py) is a SEPARATE calculation from anything the carrier
printed: it's based on the price the contractor is charging and on which
of Texas's contract types they pick, never on the carrier's own `tax`
field. No tax is added unless a rule is explicitly chosen.

Payment breakdown (2026-08-24, spec from a 15-year contractor): the total
contract price splits into up to four real payment stages -- (1) the
deductible, owed directly to the contractor by the homeowner in full, by
Texas law; (2) the initial insurance check, everything left once the other
three parts are set aside; (3) recoverable depreciation, the carrier's own
fixed figure, paid on the second check once repairs are complete;
(4) supplements, items the contractor added beyond the carrier's original
estimate, typically paid on completion. See _payment_breakdown()'s
docstring for the exact math and why part 2 is a remainder.

Rows the contractor adds vs. the carrier's own lines are told apart by the
"Insurance RCV" column: every parsed carrier row has a value there (even
$0.00 is a real value, not a blank), while a hand-added row leaves it
blank -- which is also what identifies a supplement in the payment
breakdown.
"""
import pandas as pd

from code_checklist import SECTION_LABEL as CODE_SECTION_LABEL
from pricing import compute_line_total
from proposal import payment_breakdown
from trades import TRADE_OPTIONS, guess_trade
import tax

# ---------------------------------------------------------------------
# The canonical row/column contract
# ---------------------------------------------------------------------

_TABLE_COLUMNS = [
    # The carrier's own printed line number, first and leftmost, so this
    # table can be read side by side with the PDF and a figure checked in
    # a couple of seconds. Rows the contractor adds get "A1", "A2"... --
    # see row_label().
    "#",
    "Include", "Trade", "Section", "Description", "Qty", "Unit",
    "Unit Cost", "Margin %", "Material", "Insurance RCV", "Insurance O&P",
    "Code Cite", "Needs Review", "Review Note",
    "Notes",
    # Hidden from the visible Scope table (see _VISIBLE_COLUMNS below) --
    # feeds the "Payment breakdown" section instead of being another
    # column to scroll past. See _payment_breakdown()'s docstring.
    "Recoverable Depreciation",
]

# What actually shows in the editable Scope grid -- everything in
# _TABLE_COLUMNS except "Recoverable Depreciation". A hidden column still
# round-trips untouched through any edit/merge (it's just not rendered),
# so it survives edited/added/deleted rows exactly like the visible ones
# do.
_VISIBLE_COLUMNS = [c for c in _TABLE_COLUMNS if c != "Recoverable Depreciation"]

# Two views of the same table. "Simple" is the default because fourteen
# columns on a horizontally-scrolling grid is how a contractor loses their
# place -- the ones below are what you actually touch while pricing a job.
_SIMPLE_COLUMNS = [
    "#", "Include", "Description", "Qty", "Unit", "Unit Cost", "Margin %", "Trade",
    # The review checkbox is part of the working table now -- checking a
    # line (or unchecking one you've verified against the PDF) is how the
    # Review tab's "Needs review" list is driven.
    "Needs Review",
]
# Everything else: reference figures from the carrier, the sales-tax
# material flag, and the parser's own review notes. One toggle away.
_DETAIL_COLUMNS = [c for c in _VISIBLE_COLUMNS if c not in _SIMPLE_COLUMNS]
_FULL_COLUMNS = _SIMPLE_COLUMNS + _DETAIL_COLUMNS

# ---------------------------------------------------------------------
# Search / naming -- plain Python, no framework (previously in ui.py)
# ---------------------------------------------------------------------

def filter_rows(frame, query, columns=None):
    """Rows where `query` appears anywhere, case-insensitively.

    Deliberately dumb: one box, no syntax to learn, matches any column
    including numbers ("172.5" finds a quantity, "R&R" finds a
    description, "Roof" finds a trade). Multiple words must ALL appear
    somewhere in the row, which is how people expect search to behave --
    "remove carpet" finds a line that says "Remove Carpet" and also one
    that says "Carpet - remove and haul".

    Returns the frame UNCHANGED for an empty query, and always keeps the
    original index, because the caller writes edits back to the master
    table by index.
    """
    if frame is None or len(frame) == 0:
        return frame
    terms = [t for t in str(query or "").lower().split() if t]
    if not terms:
        return frame
    searchable = frame if columns is None else frame[[c for c in columns if c in frame.columns]]
    # Built row by row rather than column by column on purpose: the table
    # mixes text, numbers and booleans, and pandas re-infers dtypes on a
    # column-wise pass, which puts floats back into what should be an
    # all-string frame and breaks the join.
    haystack = searchable.apply(
        lambda row: " ".join(str(value) for value in row).lower(), axis=1
    )
    keep = haystack.apply(lambda text: all(term in text for term in terms))
    return frame[keep]


# Characters no operating system will accept in a file name. Everything
# else the contractor typed is kept, spaces included -- this is their file
# and "Doyle kitchen rebuild v2" is a perfectly good name for it.
_ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name, extension, fallback, separator=" "):
    """Turn whatever someone typed into a safe file name.

    Strips the characters Windows and macOS refuse, collapses runs of
    whitespace (to `separator` -- a space for a name the contractor typed
    themselves, "_" for an auto-built name joining several pieces, see
    _slugify), removes a duplicate extension if they typed one, and trims
    to a sane length. An empty or all-punctuation name falls back rather
    than producing a file called ".pdf". An empty `extension` returns the
    cleaned name itself (no trailing dot) -- how the auto-built default
    download name is produced without inventing one.
    """
    cleaned = "".join(ch for ch in str(name or "") if ch not in _ILLEGAL_FILENAME_CHARS)
    cleaned = separator.join(cleaned.split()).strip(" .")
    if cleaned.lower().endswith("." + extension.lower()):
        cleaned = cleaned[: -(len(extension) + 1)].strip(" .")
    if not cleaned:
        return fallback
    if not extension:
        return cleaned
    return f"{cleaned[:120]}.{extension}"


def row_label(number, position, added=False, prefix="A"):
    """The "#" shown at the left of every table row.

    A carrier's own printed line number is the useful one -- it is what
    lets a contractor put this table beside the PDF and check a figure in
    two seconds. Rows the contractor added have no carrier number, so
    they get "A1", "A2"... which can never be mistaken for one. A
    code-required addition uses `prefix="L"` (for "legally required")
    instead, so it reads apart from a line the contractor chose to add
    on their own -- see `_next_added_label`.
    """
    if added or not str(number or "").strip():
        return f"{prefix}{position}"
    return str(number).strip()


# ---------------------------------------------------------------------
# Row shaping -- parsed estimate -> the canonical DataFrame
# ---------------------------------------------------------------------

def _rows_from_estimate(estimate, default_margin):
    rows = []
    for position, li in enumerate(estimate.line_items, start=1):
        rows.append({
            "#": row_label(li.number, position),
            "Include": True,
            "Trade": guess_trade(li.description),
            "Section": li.section,
            "Description": li.description,
            "Qty": li.quantity,
            "Unit": li.unit,
            "Unit Cost": li.unit_price,
            "Margin %": default_margin,
            # Whether this line counts as "material" for sales-tax
            # purposes under a separated residential contract (see
            # tax.py) -- defaults to material-taxable, the safer default.
            "Material": True,
            "Insurance RCV": li.rcv,
            # Reference only, like Insurance RCV -- shows whether the
            # carrier already priced overhead & profit into this specific
            # line, so a contractor can see it while setting their own
            # margin instead of it silently vanishing after parsing.
            "Insurance O&P": li.overhead_profit,
            # True when the description/notes cite a specific IRC/IBC
            # section (see scope_parser/codes.py) -- the line-level
            # detail behind the claim-context panel's code-related RCV
            # total, so a contractor can see exactly which lines are
            # code-driven, not just the aggregate.
            "Code Cite": li.code_related,
            "Needs Review": li.needs_review,
            "Review Note": li.review_reason or "",
            # The adjuster own written remarks attached to this line --
            # what the contractor needs to see (and may export) verbatim.
            "Notes": "\n".join(li.notes),

            # Insurance's own recoverable-depreciation figure for this
            # line -- 0 for non-recoverable depreciation (settled, never
            # paid to anyone) or when the carrier didn't print one. Feeds
            # "Payment breakdown" part 3, fixed regardless of margin --
            # this is what the carrier actually pays out, not a cut of
            # the contractor's own price. See _payment_breakdown().
            "Recoverable Depreciation": li.depreciation if li.depreciation_recoverable else 0.0,
        })
    # Explicit columns matter even when rows is empty: pd.DataFrame([]) has
    # NO columns at all (not even "Include"), which used to crash the rest
    # of the app with a raw KeyError the moment a PDF produced zero
    # recognized line items, instead of the friendly message the caller
    # shows for that case.
    return pd.DataFrame(rows, columns=_TABLE_COLUMNS)


def _trade_totals(included):
    """Per-trade subtotals of the currently-included rows' "Your Price"
    column, highest first -- the "where is this price actually coming
    from" view a single grand total can't answer. Mirrors the grouping
    proposal/build.py's group_line_items already does for the exported
    PDF, so this previews the sections that document will have; kept as
    a plain pandas function so it's unit-testable the same way
    _rows_from_estimate is.
    """
    if included.empty:
        return pd.DataFrame(columns=["Trade", "Subtotal"])
    totals = (
        included.groupby("Trade", sort=False)["Your Price"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    totals.columns = ["Trade", "Subtotal"]
    return totals


def _manual_row(description, trade, qty, unit, unit_cost, margin, is_material, position=1):
    """Builds one row dict for a line item a contractor types in by hand
    (typed into the '+' row at the bottom of a table), shaped exactly like a
    parsed row from _rows_from_estimate -- same columns, in the same
    order -- so it drops into the Scope table and flows through pricing,
    trade totals, and the proposal export identically to a carrier line.
    The three insurance-reference columns are left blank/False since
    there's no carrier line behind this one to reference.

    "Insurance RCV": None is also what the "Payment breakdown" section
    (see _payment_breakdown()) uses to recognize this as a supplement --
    per a 15-year contractor's own framing (2026-08-24), a line with no
    carrier estimate behind it is, by definition, something the
    contractor is adding beyond what the carrier already priced.
    """
    return {
        "#": row_label(None, position, added=True),
        "Include": True, "Trade": trade, "Section": "Added by you",
        "Description": description.strip(), "Qty": qty, "Unit": unit,
        "Unit Cost": unit_cost, "Margin %": margin, "Material": is_material,
        "Insurance RCV": None, "Insurance O&P": None, "Code Cite": False,
        "Needs Review": False, "Review Note": "", "Notes": "",
        "Recoverable Depreciation": 0.0,
    }

# ---------------------------------------------------------------------
# Payment breakdown & deductible
# ---------------------------------------------------------------------

def _payment_breakdown(included, deductible, total):
    """Splits the total contract price into the real payment stages a
    restoration job actually gets paid in -- not everything is due at
    once, and treating it as one lump total hides that from a homeowner.
    Spec is a 15-year contractor's own framing (2026-08-24), confirmed
    against the parser's already-captured per-line depreciation data:

      1. Deductible -- owed directly to the contractor by the homeowner,
         in full, by Texas law (Bus. and Com. Code Sec. 27.02 and Ins.
         Code Ch. 707 -- see TX_DEDUCTIBLE_NOTICE in proposal/models.py).
         Never paid by insurance.
      2. Due on the first insurance check -- everything else. Computed
         as a REMAINDER (total minus the other three parts), so the four
         parts always sum exactly to the total shown elsewhere on the
         proposal -- confirmed with the user this beats calculating it
         independently off the carrier's own ACV figure, which could
         drift out of sync with the contractor's actual price.
      3. Recoverable depreciation -- the insurance company's OWN fixed
         figure (summed off the carrier's parsed line items, restricted
         to currently-included rows), paid on the second check once the
         carrier gets proof repairs are complete. Deliberately NOT scaled
         by the contractor's margin -- confirmed with the user this
         reflects what insurance actually pays out, not a cut of the job.
      4. Supplements -- currently-included rows with no carrier line
         behind them, at the contractor's own price. Per industry norm,
         paid on completion. Identified by "Insurance RCV" being blank --
         every row _rows_from_estimate() builds from a real parsed
         carrier line has a value there (even $0.00 is a real value, not
         blank); only a hand-added row (_manual_row(), or one typed
         straight into the Scope table's own raw "+") leaves it empty,
         since there's no carrier line to reference.

    deductible may be None (not found in the document) -- the caller is
    responsible for prompting for a manual figure; this function treats
    None as 0 rather than guessing.

    The four-part split itself is ONE shared implementation --
    proposal.build.payment_breakdown (this used to contain a second copy
    of that math; only the DataFrame-specific aggregation below lives
    here now). See proposal/build.py for the spec.
    """
    deductible = deductible or 0.0
    if included.empty:
        recoverable_depreciation = 0.0
        supplements = 0.0
    else:
        recoverable_depreciation = included["Recoverable Depreciation"].fillna(0).sum()
        is_supplement = included["Insurance RCV"].isna()
        supplements = included.loc[is_supplement, "Your Price"].sum()
    return payment_breakdown(
        total,
        deductible=deductible,
        recoverable_depreciation=recoverable_depreciation,
        supplements=supplements,
    )


def _effective_deductible(estimate):
    """The deductible to use on screen and on the proposal.

    The same deductible can be printed in two places on a document: the
    Coverage/Deductible/Policy-Limit table (claim_flags.dwelling_deductible)
    and the summary ladder's "Less Deductible" line
    (carrier_summary.deductible). claim_flags is the primary source (it's
    the one the payment rules were built around); carrier_summary is the
    fallback for a document that only prints it in the summary (e.g.
    Symbility, which never prints the coverage table). None means neither
    printed one -- the caller asks the contractor for it manually.

    Uses getattr defensively so this is unit-testable against a plain
    stub object.
    """
    flags = getattr(estimate, "claim_flags", None)
    primary = getattr(flags, "dwelling_deductible", None) if flags is not None else None
    if primary:
        return primary
    summary = getattr(estimate, "carrier_summary", None)
    if summary is not None and getattr(summary, "deductible", None):
        return summary.deductible
    return None

# ---------------------------------------------------------------------
# File-name building
# ---------------------------------------------------------------------

def _slugify(value):
    """Turns a piece of a file name into something every OS is happy
    with: strips anything that isn't a letter, digit, space, or hyphen,
    then collapses whitespace into underscores. Keeps a company name or
    claim number readable ("State_Farm", "0761262757") instead of
    disappearing into percent-encoded junk over one stray "&" or "/".

    Strictness on purpose, and it's the one deliberate difference from
    sanitize_filename: this builds a name out of several separate
    pieces (business, carrier, claim number) joined into one, so
    punctuation that's fine typed by hand ("Doyle's kitchen") would
    turn into an ambiguous run here ("Doyle_s_kitchen") -- this strips
    it. The cleaning MECHANICS themselves are the same single
    implementation (sanitize_filename), not a second copy.
    """
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in " -")
    return sanitize_filename(cleaned, "", "", separator="_")


def _export_filename(contractor_name, insurance_company, claim_number, extension, fallback):
    """Contractor name, then insurance company, then claim number -- so a
    folder full of downloaded exports is actually distinguishable at a
    glance instead of "proposal.pdf", "proposal (1).pdf", "proposal (2)
    .pdf"... Falls back gracefully: any piece that's blank, missing, or
    the "--" _best() shows for a field that wasn't found is just skipped,
    rather than baking a literal "--" into the file name or crashing when
    the claim number wasn't parsed yet. `fallback` (e.g. "proposal.pdf")
    is used only if every single piece turns out to be missing.
    """
    pieces = [contractor_name, insurance_company, claim_number]
    slugs = [_slugify(p) for p in pieces if p and str(p).strip() and str(p).strip() != "--"]
    return f"{'_'.join(slugs)}.{extension}" if slugs else fallback


def _best(fields, *keys, default="--"):
    for k in keys:
        v = fields.get(k)
        if v:
            return v
    return default


# ---------------------------------------------------------------------
# Pricing & totals
# ---------------------------------------------------------------------

def _priced(frame):
    """Adds the "Your Price" column: quantity x unit cost x (1 + margin).
    Returns a copy, so nothing writes back into the master table by
    accident."""
    out = frame.copy()
    if out.empty:
        out["Your Price"] = []
        return out
    out["Your Price"] = out.apply(
        lambda r: compute_line_total(r["Qty"], r["Unit Cost"], r["Margin %"]), axis=1
    )
    return out


def _quote_totals(rows, tax_rule, tax_rate):
    """The running quote totals from the master rows: (subtotal, sales tax,
    total, included line count). Rebuilt every request, so the sticky
    totals always match whatever the contractor has edited -- same formula
    as the Pricing tab's metrics."""
    if rows is None or rows.empty:
        return 0.0, 0.0, 0.0, 0
    included = _priced(rows[rows["Include"].fillna(False)])
    subtotal = float(included["Your Price"].sum())
    tax_amount = tax.compute_sales_tax(
        [{"line_total": r["Your Price"], "is_material": r["Material"]}
         for _, r in included.iterrows()],
        tax_rule, tax_rate,
    )
    return subtotal, tax_amount, subtotal + tax_amount, int(len(included))


def _added_mask(frame):
    """Rows the contractor added rather than the carrier: no carrier line
    behind them, so no "Insurance RCV". Every parsed carrier row has a
    value there -- even $0.00 is a value, not a blank."""
    if frame.empty:
        return frame.index == frame.index  # empty boolean mask
    return frame["Insurance RCV"].isna()


def _flagged_mask(frame):
    if frame.empty:
        return frame.index == frame.index
    return frame["Needs Review"].fillna(False).astype(bool)


def _code_required_mask(frame):
    """Rows added from the Code Additions checklist -- a SUBSET of
    _added_mask() (no carrier line behind them either), told apart by
    Section. Kept out of the Scope tab and the Review tab's "Added by
    you" list the same way _added_mask() rows are kept out of Scope --
    see code_checklist.SECTION_LABEL's docstring."""
    if frame.empty:
        return frame.index == frame.index
    return frame["Section"] == CODE_SECTION_LABEL

# ---------------------------------------------------------------------
# Edit merge -- how an edited table writes back into the master rows
# ---------------------------------------------------------------------

def _writable_columns(edited_columns, master_columns):
    """Which columns from an edited table may be written back into the
    master row set.

    Every table on screen is a filtered view of the same master rows, and
    one column -- "Your Price" -- is computed by _priced() and exists ONLY
    in the view. Writing it back would add a stray column to the master
    rows and break the _TABLE_COLUMNS contract. So the write-back writes
    the intersection of what the editor returned and what the master
    table actually owns, computed per-edit so a future pure-computed
    column can't be forgotten either way.

    Plain Python (no framework, no pandas) so it's unit-testable, same as
    every other decision in this module.
    """
    return [c for c in edited_columns if c in master_columns]


def _next_added_label(frame, prefix="A"):
    """The next number for a given prefix, worked out from the labels
    already in the table rather than from a count -- so deleting an
    added line can never make the next one collide with an existing
    label. Each prefix keeps its own count: "A" for a line the
    contractor typed in themselves, "L" for one added from the Code
    Additions checklist (see _code_row) -- deleting an L row can never
    collide with, or skip, an A number and vice versa."""
    used = []
    if frame is not None and not frame.empty and "#" in frame.columns:
        for value in frame["#"]:
            text = str(value or "").strip()
            if text.upper().startswith(prefix.upper()) and text[len(prefix):].isdigit():
                used.append(int(text[len(prefix):]))
    return max(used, default=0) + 1


def _cell(row, column, default):
    """A single cell from a data-editor row, with the editor's blanks
    (None/NaN) turned into a default."""
    value = row.get(column)
    if value is None:
        return default
    try:
        if bool(pd.isna(value)):
            return default
    except (TypeError, ValueError):
        pass
    return value


def _editor_row_to_manual(row, default_margin, master):
    """Turn one row the data editor ADDED (only the visible columns are
    filled) into a full _TABLE_COLUMNS row, shaped exactly like _manual_row()
    so it flows through pricing, the payment breakdown, and the proposal
    export as the counter-offer supplement it is -- no carrier line behind
    it, so Insurance RCV stays blank and the Payment Schedule lists it as a
    supplement. Returns None when the row is completely blank, so an
    accidental "+" click doesn't create a junk line."""
    description = str(_cell(row, "Description", "") or "").strip()
    qty = float(_cell(row, "Qty", 0.0))
    unit = str(_cell(row, "Unit", "") or "")
    unit_cost = float(_cell(row, "Unit Cost", 0.0))
    margin = int(_cell(row, "Margin %", default_margin))
    trade = _cell(row, "Trade", None)
    if not trade:
        trade = TRADE_OPTIONS[0] if TRADE_OPTIONS else "General"
    is_material = bool(_cell(row, "Material", True))
    include = bool(_cell(row, "Include", True))
    if not (description or qty or unit or unit_cost):
        return None
    out = _manual_row(
        description, trade, qty, unit, unit_cost, margin, is_material,
        position=_next_added_label(master),
    )
    out["Include"] = include
    return out


def _merge_table_edits(master, shown, edited, default_margin):
    """Merge what an editable table returned back into the master row set.

    Every table on screen is a filtered VIEW of the same master rows -- a
    search box, or the Review tab showing only what you added -- so the
    merge is by row identity against what the view was SHOWING:

      * rows the editor changed, and that the view was showing -> written
        back by index;
      * rows the editor ADDED (their index is not one the view showed) ->
        appended as proper counter-offer rows via _editor_row_to_manual();
      * rows the view showed but the editor no longer has -> deleted, and
        only those, so a filter or another tab can never wipe the rows it
        isn't showing.

    Returns (master, number_added).
    """
    known = set(shown.index)

    added_count = 0
    is_new = ~edited.index.isin(list(known))
    for _, row in edited[is_new].iterrows():
        manual = _editor_row_to_manual(row, default_margin, master)
        if manual is None:
            continue
        master = pd.concat(
            [master, pd.DataFrame([manual], columns=_TABLE_COLUMNS)],
            ignore_index=True,
        )
        added_count += 1

    deleted = known - set(edited.index)
    if deleted:
        master = master.drop(index=sorted(deleted))

    edit_mask = edited.index.isin(list(known))
    if edit_mask.any():
        writable = _writable_columns(edited.columns, master.columns)
        master = master.copy()
        edit_index = edited.index[edit_mask]
        for col in writable:
            # Edited cells can arrive in a wider dtype than the master
            # column holds (a concatenated blank "+" row upcasts object /
            # float) -- coerce back to the master column's dtype so the
            # assignment can't be rejected by pandas.
            master.loc[edit_index, col] = edited.loc[edit_index, col].astype(master[col].dtype)

    return master, added_count


def _export_basename(contractor_name, fields):
    """The default download name: business, carrier, claim number."""
    return _export_filename(
        contractor_name,
        _best(fields, "insurance_company", "company", default=""),
        _best(fields, "claim_number", default=""),
        "", "buildupquote.",
    ).rstrip(".")

