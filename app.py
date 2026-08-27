"""BuildUpQuote -- the interactive scope editing workspace + proposal export.

Run with:  streamlit run app.py

Flow: fill in your business info (sidebar) -> upload a carrier estimate
PDF -> the parsing engine (scope_parser/) reads it -> review the claim
info and any warnings -> edit trade classification, unit cost, and
margin per line item -> choose how this contract is taxed (tax.py) ->
see your total contractor price (subtotal + tax, if any) update live ->
download a branded proposal PDF (proposal/) or a plain CSV.

This screen deliberately does NOT show the insurance-only columns
(depreciation, ACV, deductible) as anything other than a clearly-labeled
reference number -- per the project's legal notes, what the contractor
charges is never "RCV minus depreciation" and this screen should not
imply otherwise. The exported proposal PDF is stricter still: its data
model (proposal/models.py) has no field for those numbers at all, so
they can't end up on a homeowner-facing document even by accident.

Sales tax (added after researching how this actually works -- see the
"Claim Ledger" reference doc and the project's "Known parsing problems"
doc, problem #4) is a SEPARATE calculation from anything the carrier
printed: it's based on the price the contractor is charging, and on
which of Texas's contract types they pick, never on the carrier's own
`tax` field (which stays insurance-only, same as depreciation/ACV). No
tax is added unless a rule is explicitly chosen -- see tax.py.

Claim context (scope_parser/claim_flags.py): the parsing engine also
recognizes, by pattern/synonym matching against the document's own text --
never a guess -- the claim-PROCESS realities described in the Claim
Ledger: whether the dwelling deductible works out to a percentage of the
policy limit, a mortgagee/lienholder mention, Ordinance-or-Law coverage,
a cosmetic damage exclusion, and whether the document is itself an
appraisal or public-adjuster estimate rather than the carrier's own. When
any of that is found, it shows up in a "Claim context" panel right below
the claim info, and which specific line items cite a building code (the
number that matters for Ordinance-or-Law coverage) is visible per-row in
the "Code Cite" column.

Payment breakdown (2026-08-24, spec from a 15-year contractor): the total
contract price isn't paid in one lump sum -- it splits into up to four
real payment stages, in the order they typically arrive: (1) the
deductible, owed directly to the contractor by the homeowner in full, by
Texas law; (2) the initial insurance check, everything left over once the
other three parts are set aside; (3) recoverable depreciation, the
carrier's own fixed figure, paid on the second check once repairs are
complete; (4) supplements, items the contractor added beyond the
carrier's original estimate, typically paid on completion. See
_payment_breakdown()'s docstring for the exact math and why part 2 is
calculated as a remainder rather than independently.

Source program (scope_parser/metadata.py's fields_from_pdf_info): every
PDF carries hidden file-level metadata -- what macOS "Get Info" or
Adobe's Document Properties would show -- separate from anything printed
on a page. Xactimate's own export stamps its exact name and version in
there (confirmed on Glenn's real PDFs: "Xactimate 24.4.1001.1"), which
this screen surfaces as a caption under the claim info. Useful on its
own, and the most reliable signal available for telling a future
non-Xactimate format apart later -- see the "Beyond Xactimate"
reference doc.
"""
import datetime
import io
import os
import tempfile

import pandas as pd
import streamlit as st

import tax
import ui
from code_checklist import (
    CATEGORY_ORDER,
    CODE_ITEMS,
    SECTION_LABEL as CODE_SECTION_LABEL,
    STATUTORY_CONTEXT,
    check_coverage,
    labor_line,
    material_description,
)
from pricing import compute_line_total
from proposal import ContractorInfo, build_proposal, payment_breakdown, render_proposal_pdf
from scope_parser import parse_pdf
from trades import TRADE_OPTIONS, guess_trade

st.set_page_config(page_title="BuildUpQuote", page_icon="\U0001F4CB", layout="wide")

# Where an uploaded logo gets saved so it can be embedded in the PDF.
# Session-scoped, not tied to any one claim, since the same contractor
# reuses the same logo across proposals.
_LOGO_DIR = os.path.join(tempfile.gettempdir(), "buildupquote_logos")


_TABLE_COLUMNS = [
    # The carrier's own printed line number, first and leftmost, so this
    # table can be read side by side with the PDF and a figure checked in
    # a couple of seconds. Rows the contractor adds get "A1", "A2"... --
    # see ui.row_label().
    "#",
    "Include", "Trade", "Section", "Description", "Qty", "Unit",
    "Unit Cost", "Margin %", "Material", "Insurance RCV", "Insurance O&P",
    "Code Cite", "Needs Review", "Review Note",
    # Hidden from the visible Scope table (see _VISIBLE_COLUMNS below) --
    # feeds the "Payment breakdown" section instead of being another
    # column to scroll past. See _payment_breakdown()'s docstring.
    "Recoverable Depreciation",
]

# What actually shows in the editable Scope grid -- everything in
# _TABLE_COLUMNS except "Recoverable Depreciation". A hidden column still
# round-trips through st.data_editor untouched (it's just not rendered),
# so it survives edits/added/deleted rows exactly like the visible ones
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


def _rows_from_estimate(estimate, default_margin):
    rows = []
    for position, li in enumerate(estimate.line_items, start=1):
        rows.append({
            "#": ui.row_label(li.number, position),
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
            # section (see scope_parser/codes.py) -- this is the line-level
            # detail behind the "Claim context" panel's code-related RCV
            # total below, so a contractor can see exactly which lines are
            # code-driven, not just the aggregate.
            "Code Cite": li.code_related,
            "Needs Review": li.needs_review,
            "Review Note": li.review_reason or "",
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
    # recognized line items, instead of the friendly message main() now
    # shows for that case.
    return pd.DataFrame(rows, columns=_TABLE_COLUMNS)


def _trade_totals(included):
    """Per-trade subtotals of the currently-included rows' "Your Price"
    column, highest first -- the "where is this price actually coming
    from" view a single grand total can't answer. Mirrors the grouping
    proposal/build.py's group_line_items already does for the exported
    PDF, so this previews the sections that document will have; kept as
    a plain pandas function (no Streamlit calls) so it's unit-testable
    the same way _rows_from_estimate is.
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
        "#": ui.row_label(None, position, added=True),
        "Include": True, "Trade": trade, "Section": "Added by you",
        "Description": description.strip(), "Qty": qty, "Unit": unit,
        "Unit Cost": unit_cost, "Margin %": margin, "Material": is_material,
        "Insurance RCV": None, "Insurance O&P": None, "Code Cite": False,
        "Needs Review": False, "Review Note": "",
        "Recoverable Depreciation": 0.0,
    }


def _payment_breakdown(included, deductible, total):
    """Splits the total contract price into the real payment stages a
    restoration job actually gets paid in -- not everything is due at
    once, and treating it as one lump total hides that from a homeowner.
    Spec is a 15-year contractor's own framing (2026-08-24), confirmed
    against the parser's already-captured per-line depreciation data:

      1. Deductible -- owed directly to the contractor by the homeowner,
         in full, by Texas law (Bus. & Com. Code Sec. 27.02 and Ins. Code
         Ch. 707 -- see TX_DEDUCTIBLE_NOTICE in proposal/models.py).
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


def _carrier_summary_panel(estimate):
    """The document's own bottom-line ladder (Line Item Total / Overhead
    / Profit / Replacement Cost Value / Net Claim) -- read but never
    shown anywhere before this existed, which meant a document like the
    Doyle contractor export understated its own total by exactly the
    Overhead + Profit it prints on its own summary page. See
    scope_parser/carrier_summary.py.

    Strictly reference: nothing here writes to "Your Price" on its own.
    The one exception a contractor can trigger themselves is the "Match
    this markup" button, which just pre-fills the SAME margin control
    already on this tab -- same as clicking "Apply to every row" with a
    number copied off the PDF by hand, just without the copying.
    """
    cs = estimate.carrier_summary
    if cs is None or not cs.has_content:
        return

    ui.section(
        st, "The carrier's own bottom line", "info",
        "Straight off the document's own summary page -- for comparison only. Nothing here "
        "changes your price below.",
    )
    if cs.coverage_label:
        st.caption(
            f"This ladder covers **{cs.coverage_label}** only. If this claim has other "
            "coverages (Other Structures, Personal Property...), each prints its own."
        )

    cols = st.columns(3)
    if cs.line_item_total is not None:
        label = "Carrier's line item total"
        help_text = "The carrier's own raw scope total, before tax, overhead, or profit."
        if cs.reconciles_with_parsed_items is True:
            label += " ✓"
            help_text += " Matches what we parsed."
        elif cs.reconciles_with_parsed_items is False:
            label += " ⚠️"
            help_text = (
                f"We parsed ${cs.parsed_items_sum:,.2f} in line items -- worth a check "
                "against the PDF."
            )
        cols[0].metric(label, f"${cs.line_item_total:,.2f}", help=help_text)
    if cs.combined_markup_pct is not None:
        cols[1].metric(
            "Carrier's overhead + profit", f"{cs.combined_markup_pct:g}%",
            help=f"Overhead {cs.overhead_pct:g}% + Profit {cs.profit_pct:g}% -- already baked "
                 "into their Replacement Cost Value, the same way your margin gets baked "
                 "into your price.",
        )
    if cs.replacement_cost_value is not None:
        cols[2].metric(
            "Carrier's replacement cost", f"${cs.replacement_cost_value:,.2f}",
            help="Line item total plus tax, overhead, and profit -- the carrier's own full "
                 "cost of the repair.",
        )

    if cs.combined_markup_pct is not None:
        matched = round(cs.combined_markup_pct)
        if st.button(
            f"Match the carrier's {cs.combined_markup_pct:g}% markup",
            key="match_carrier_markup",
            help="Sets the margin slider below to this number, on every row. You can still "
                 "adjust it afterward -- this is just a starting point.",
        ):
            st.session_state["rows"]["Margin %"] = matched
            st.session_state["default_margin"] = matched
            st.success(f"Set your margin to {matched}% on every row.")
            rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
            if rerun:
                rerun()

    if cs.net_claim is not None and cs.deductible:
        st.caption(
            f"After their ${cs.deductible:,.2f} deductible, the carrier's own \"{cs.source_label}\" "
            f"line reads ${cs.net_claim:,.2f} -- that's what insurance actually pays out, not "
            "the cost of the job. Your price below should still reflect the full repair, not this "
            "smaller number."
        )


def _code_row(description, trade, qty, unit, unit_cost, margin, is_material, position, review_note):
    """Same shape as _manual_row (so it prices, totals, and exports
    exactly like any other line), but labeled apart from both a carrier
    line and an ordinary contractor addition: Section is
    code_checklist.SECTION_LABEL and the "#" gets an "L" prefix instead
    of "A", for "legally required" -- see _code_required_mask() and
    ui.row_label()'s prefix argument. review_note carries the code
    item's own plain-English requirement text, which _editable_table
    shows next to the line the same way a flagged parser row's Review
    Note does -- Glenn, 2026-08-25: "give a reason for the code
    violation and what it means underneath the line item."
    """
    row = _manual_row(description, trade, qty, unit, unit_cost, margin, is_material, position=position)
    row["#"] = ui.row_label(None, position, added=True, prefix="L")
    row["Section"] = CODE_SECTION_LABEL
    row["Review Note"] = review_note
    return row


def _code_item_form_key(item):
    return f"code_form_{item.id}"


def _open_code_item_form(item_id):
    st.session_state["_open_code_form"] = item_id
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun:
        rerun()


def _close_code_item_form():
    st.session_state["_open_code_form"] = None
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun:
        rerun()


def _save_code_item_addition(item, workers, hours, rate,
                              material_qty, material_unit, material_cost, material_margin,
                              add_material):
    """One "Add" click on a code item's form. At most one material line
    per item -- skipped if that exact line is already in the scope, so
    reopening the form and clicking Add again doesn't duplicate it --
    plus one labor line per click, so clicking Add again with new
    numbers (a second crew, a second day) is how the labor "repeats"
    (Glenn, 2026-08-25). Either half can be left blank/zero; nothing is
    added for a half that is (see code_checklist.labor_line)."""
    rows = st.session_state["rows"]
    new_rows = []
    next_label = _next_added_label(rows, prefix="L")

    already_has_material = add_material and not rows.empty and (
        (rows["Section"] == CODE_SECTION_LABEL)
        & (rows["Description"] == material_description(item))
    ).any()
    if add_material and not already_has_material:
        new_rows.append(_code_row(
            material_description(item), item.default_trade,
            material_qty, material_unit, material_cost, material_margin, True,
            position=next_label, review_note=item.requirement,
        ))
        next_label += 1

    labor_description, total_hours = labor_line(item, workers, hours, rate)
    if labor_description is not None:
        new_rows.append(_code_row(
            labor_description, item.default_trade,
            total_hours, "HR", rate, material_margin, False,
            position=next_label, review_note=item.requirement,
        ))

    if not new_rows:
        st.warning("Nothing to add yet -- fill in a quantity and cost, or a labor crew, first.")
        return

    st.session_state["rows"] = pd.concat(
        [rows, pd.DataFrame(new_rows, columns=_TABLE_COLUMNS)], ignore_index=True,
    )
    st.success(f"Added {len(new_rows)} line(s) for “{item.citation} — {item.title}.”")
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun:
        rerun()


def _render_code_item_form(item):
    """The inline form under a checklist item once its Add control is
    clicked. Two independent halves -- material (qty/unit/cost/margin,
    same shape as the "Add a line item the carrier missed" form) and
    labor (workers/hours/rate, folded into one priced line by
    code_checklist.labor_line) -- since a code item might need one, the
    other, or both. Stays open after Add so a second labor line is just
    another click, per Glenn's "the labor... should be on a separate
    line item, that the contractor can repeat" (2026-08-25). No
    st.dialog -- this app has no Streamlit version floor high enough to
    rely on it, so the form opens inline instead of in a popup."""
    default_margin = int(st.session_state["default_margin"])
    with st.form(_code_item_form_key(item), clear_on_submit=False):
        add_material = st.checkbox(
            "Add a priced line for this item", value=True, key=f"code_material_on_{item.id}",
        )
        m1, m2, m3, m4 = st.columns(4)
        material_qty = m1.number_input("Qty", min_value=0.0, value=1.0, step=1.0, key=f"code_qty_{item.id}")
        material_unit = m2.text_input("Unit", value="EA", key=f"code_unit_{item.id}")
        material_cost = m3.number_input(
            "Unit cost ($)", min_value=0.0, value=0.0, step=1.0, key=f"code_cost_{item.id}",
        )
        material_margin = m4.number_input(
            "Margin %", min_value=0, max_value=100, value=default_margin, key=f"code_margin_{item.id}",
        )
        st.caption(
            "Labor for this item, if any -- leave at 0 to skip. Submit again for a second "
            "crew or a second day; each click adds its own line."
        )
        w1, w2, w3 = st.columns(3)
        workers = w1.number_input("Workers", min_value=0.0, value=0.0, step=1.0, key=f"code_workers_{item.id}")
        hours = w2.number_input("Hours", min_value=0.0, value=0.0, step=1.0, key=f"code_hours_{item.id}")
        rate = w3.number_input("Rate ($/hr)", min_value=0.0, value=0.0, step=1.0, key=f"code_rate_{item.id}")
        add_col, close_col = st.columns(2)
        add_clicked = add_col.form_submit_button("Add", use_container_width=True)
        close_clicked = close_col.form_submit_button("Done -- close this form", use_container_width=True)
    if add_clicked:
        _save_code_item_addition(
            item, workers, hours, rate,
            material_qty, material_unit, material_cost, material_margin, add_material,
        )
    elif close_clicked:
        _close_code_item_form()


def _code_item_checklist_row(item, found):
    """One line inside a code category expander: the citation/title and
    requirement, a found/not-found mark, and an Add control that opens
    _render_code_item_form inline underneath."""
    is_found = found.get(item.id, False)
    open_id = st.session_state.get("_open_code_form")
    row_cols = st.columns([5, 2, 2])
    row_cols[0].markdown(f"{'✅' if is_found else '⬜'} **{item.citation}** — {item.title}")
    row_cols[0].caption(item.requirement)
    if is_found:
        row_cols[1].caption("in scope")
    if open_id == item.id:
        if row_cols[2].button("Close", key=f"close_code_{item.id}"):
            _close_code_item_form()
    else:
        label = "Add another line" if is_found else "Add"
        if row_cols[2].button(label, key=f"add_code_{item.id}"):
            _open_code_item_form(item.id)
    if open_id == item.id:
        _render_code_item_form(item)


def _code_additions_list(rows):
    """The priced result of everything added from the checklist above --
    same editable grid as every other tab, filtered to just the
    code-required rows (see _code_required_mask). Kept apart from the
    Scope and Review tabs, per Glenn's "it shouldn't be added into the
    scope but additions that the contractor has to add by law"
    (2026-08-25) -- but it still flows through the same pricing/export
    machinery as any other line."""
    code_rows = rows[_code_required_mask(rows)]
    ui.section(st, f"Added by law ({len(code_rows)})", "added")
    if code_rows.empty:
        st.info("Nothing added from the checklist above yet.")
        return
    query = _search_box("Search code additions", "code_search")
    shown = ui.filter_rows(code_rows, query)
    if query:
        st.caption(f"Showing {len(shown)} of {len(code_rows)} lines.")
    _editable_table(shown, _SIMPLE_COLUMNS + ["Review Note"], key="code_editor")


def _code_additions_tab(rows):
    """Texas building/electrical/mechanical/plumbing code items, plus
    OSHA worksite-safety items (Glenn, 2026-08-25) -- checked against the
    CONTRACTOR's current scope, not the carrier's. See code_checklist.py.

    Purely a reminder, never a finding: a category showing "not found"
    means no line in THIS proposal's wording matches yet, never that the
    requirement isn't being met on the actual job -- and plenty of these
    items won't apply to every claim at all (no garage on this job, no
    dryer work in scope...). The contractor's own judgement decides which
    apply; the Add control next to each opens a form to price it as its
    own line, separate from the carrier's scope and from anything added
    on the Review tab, since this is something the contractor has to add
    BY LAW, not a line they chose to add themselves.
    """
    ui.section(
        st, "Texas code checklist", "info",
        "A reference list, not a finding -- checked against your current scope by simple "
        "keyword matching. “Not found yet” doesn't mean it's missing from the actual "
        "job, only that nothing in this proposal's wording matches it -- and plenty of these "
        "won't apply to every claim. Use your own judgement on which do, then Add to price "
        "it as its own line here, separate from the carrier's scope.",
    )
    with st.expander("Which codes apply in Texas (background, not a checklist)"):
        for entry in STATUTORY_CONTEXT:
            st.caption(f"**{entry['citation']}** — {entry['title']}: {entry['detail']}")

    descriptions = rows["Description"].tolist() if not rows.empty else []
    found = check_coverage(descriptions)
    present_trades = set(rows["Trade"].dropna()) if not rows.empty else set()

    for category in CATEGORY_ORDER:
        items = [i for i in CODE_ITEMS if i.category == category]
        matched_count = sum(1 for i in items if found.get(i.id))
        # Open by default only when a trade already in this scope
        # suggests the category is relevant -- keeps ~29 items from
        # dumping onto the screen on every single claim.
        relevant = any(item.default_trade in present_trades for item in items)
        with st.expander(
            f"{category} ({matched_count}/{len(items)} found in your scope)",
            expanded=relevant,
        ):
            for item in items:
                _code_item_checklist_row(item, found)

    st.divider()
    _code_additions_list(rows)


def _slugify(value):
    """Turns a piece of a file name into something every OS is happy
    with: strips anything that isn't a letter, digit, space, or hyphen,
    then collapses whitespace into underscores. Keeps a company name or
    claim number readable ("State_Farm", "0761262757") instead of
    disappearing into percent-encoded junk over one stray "&" or "/".

    Strictness on purpose, and it's the one deliberate difference from
    ui.sanitize_filename: this builds a name out of several separate
    pieces (business, carrier, claim number) joined into one, so
    punctuation that's fine typed by hand ("Doyle's kitchen") would
    turn into an ambiguous run here ("Doyle_s_kitchen") -- this strips
    it. The cleaning MECHANICS themselves are the same single
    implementation (ui.sanitize_filename), not a second copy.
    """
    cleaned = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in " -")
    return ui.sanitize_filename(cleaned, "", "", separator="_")


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


def _contractor_sidebar():
    """Business info shown on every proposal. Kept in session_state so it's
    filled in once and stays put while you work through a claim -- it's
    not saved to disk yet, so it resets if the app restarts (accounts/
    saved profiles are a later step)."""
    st.sidebar.header("Your business info")
    st.sidebar.caption("This appears on every proposal you export.")
    name = st.sidebar.text_input("Business name", key="contractor_name")
    address = st.sidebar.text_input("Address", key="contractor_address")
    phone = st.sidebar.text_input("Phone", key="contractor_phone")
    email = st.sidebar.text_input("Email", key="contractor_email")
    license_number = st.sidebar.text_input("License #", key="contractor_license")
    logo_file = st.sidebar.file_uploader("Logo (optional)", type=["png", "jpg", "jpeg"], key="contractor_logo")

    logo_path = st.session_state.get("_logo_path")
    if logo_file is not None:
        os.makedirs(_LOGO_DIR, exist_ok=True)
        logo_path = os.path.join(_LOGO_DIR, logo_file.name)
        with open(logo_path, "wb") as f:
            f.write(logo_file.getvalue())
        st.session_state["_logo_path"] = logo_path

    return ContractorInfo(
        name=name, address=address, phone=phone, email=email,
        license_number=license_number, logo_path=logo_path,
    )


def _render_read_banner(estimate):
    """How this file was read, and how much to trust it.

    Every upload gets exactly one of these -- there is no silent parse.
    The three states come from scope_parser/confidence.py; the evidence
    behind them is the document's OWN printed subtotals, which is why the
    same judgement works for a format nobody has ever taught us.
    """
    conf = getattr(estimate, "confidence", None)
    fp = getattr(estimate, "fingerprint", None)
    kind = getattr(estimate, "document_type", None)
    if conf is None:
        return

    show = {
        "recognised": st.success,
        "generic_ok": st.info,
        "low": st.warning,
        "not_a_scope": st.warning,
    }.get(conf.state, st.info)
    show(f"**{conf.headline}.** {conf.detail}")

    # What kind of document this is, when it isn't the ordinary case.
    if kind is not None and kind.advice:
        st.warning(f"**{kind.label}.** {kind.advice}")

    if fp is not None and fp.signals:
        with st.expander("How we worked out what this file is", expanded=False):
            st.caption(
                "Nothing here is a guess -- each line is a specific thing found in the "
                "file or printed on the page."
            )
            for signal in fp.signals:
                st.write(f"- {signal}")
            if fp.jurisdiction_state:
                st.write(
                    f"- the price list on this estimate is **{fp.price_list_code}**, "
                    f"which prices work in **{fp.jurisdiction_state}**"
                )

    # The app's tax rules and the deductible notice on the exported
    # proposal are Texas law. On a claim priced somewhere else they are
    # the wrong law, and that is worth saying out loud rather than
    # printing quietly onto a contract.
    if fp is not None and fp.jurisdiction_state and fp.jurisdiction_state != "TX":
        st.warning(
            f"**This estimate is priced in {fp.jurisdiction_state}, not Texas.** The "
            "contract-type tax rules below and the deductible notice on the exported "
            "proposal are both written to Texas law. Check what your state requires "
            "before you send this out."
        )

    flagged = [li for li in estimate.line_items if li.needs_review]
    if flagged:
        with st.expander(
            f"🔍 {len(flagged)} line(s) to check first", expanded=conf.needs_attention
        ):
            st.caption(
                "These are the rows the parser was not sure about. It kept what the "
                "document actually said rather than guessing at a number. They are still "
                "in the Scope table below, in document order, so you can compare them "
                "against the PDF side by side."
            )
            for li in flagged:
                label = f"**#{li.number}** {li.description}" if li.number else f"**{li.description}**"
                st.write(f"- {label} — {li.review_reason or 'needs review'}")



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
    total, included line count). Rebuilt every rerun, so the sticky totals
    bar at the bottom of the page always matches whatever the contractor
    has edited -- same formula as the Pricing tab's metrics."""
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
    """Rows added from the Code Additions tab -- a SUBSET of
    _added_mask() (no carrier line behind them either), told apart by
    Section. Kept out of the Scope tab and the Review tab's "Added by
    you" list the same way _added_mask() rows are kept out of Scope --
    see code_checklist.SECTION_LABEL's docstring."""
    if frame.empty:
        return frame.index == frame.index
    return frame["Section"] == CODE_SECTION_LABEL


def _column_config():
    """One definition of how every column behaves, shared by every table
    on every tab -- so a figure looks and edits the same wherever it is
    seen. Widths are left to the editor (columns autosize to their
    content) rather than pinned, so the same data never needs hand-tuning
    per table."""
    return {
        "#": st.column_config.TextColumn(
            "#", disabled=True,
            help="The carrier's own line number, so you can check this row against "
                 "the PDF. Lines you added are numbered A1, A2...",
        ),
        "Include": st.column_config.CheckboxColumn(help="Uncheck to drop this line from your price."),
        "Trade": st.column_config.SelectboxColumn(options=TRADE_OPTIONS),
        "Qty": st.column_config.NumberColumn(format="%.2f"),
        "Unit": st.column_config.TextColumn(),
        "Unit Cost": st.column_config.NumberColumn(format="$%.2f"),
        "Margin %": st.column_config.NumberColumn(min_value=0, max_value=100, format="%d%%"),
        "Material": st.column_config.CheckboxColumn(
            help="Counts toward sales tax under a Separated contract (materials only). "
                 "Uncheck for pure-labor lines.",
        ),
        "Insurance RCV": st.column_config.NumberColumn(format="$%.2f", disabled=True),
        "Insurance O&P": st.column_config.NumberColumn(
            format="$%.2f", disabled=True,
            help="Overhead & profit the carrier already priced into this line, if any -- "
                 "reference only, it never affects your price.",
        ),
        "Code Cite": st.column_config.CheckboxColumn(
            disabled=True,
            help="This line cites a specific building-code section -- see \"Claim context\" "
                 "for what that means for coverage.",
        ),
        "Needs Review": st.column_config.CheckboxColumn(
            help="Check a line that needs another look, or uncheck one you've verified "
                 "against the PDF -- this drives the Review tab's 'Needs review' list.",
        ),
        "Review Note": st.column_config.TextColumn(
            help="Why this line was flagged -- the parser's reason, or a note you add "
                 "while reviewing.",
        ),
        # Computed live by _priced() -- shown as money, never typed over.
        # Also the one column the Pricing tab's editable table DISPLAYS
        # but must NOT write back (see _writable_columns).
        "Your Price": st.column_config.NumberColumn(
            format="$%.2f", disabled=True,
            help="Qty x Unit Cost x (1 + margin) -- computed, not typed.",
        ),
    }


def _writable_columns(edited_columns, master_columns):
    """Which columns from an edited table may be written back into the
    master row set.

    Every table on screen is a filtered view of the same master rows, and
    one column -- "Your Price" -- is computed by _priced() and exists ONLY
    in the view. Writing it back would add a stray column to
    st.session_state["rows"] and break the _TABLE_COLUMNS contract. So the
    write-back writes the intersection of what the editor returned and
    what the master table actually owns, computed per-edit so a future
    pure-computed column can't be forgotten either way.

    Plain Python (no Streamlit, no pandas) so it's unit-testable, same as
    every other decision in this file.
    """
    return [c for c in edited_columns if c in master_columns]


def _toggle(container, label, **kwargs):
    """st.toggle where it exists, st.checkbox where it doesn't.

    Purely defensive: st.toggle arrived in Streamlit 1.26 and this app
    otherwise has no version floor. A nicer-looking switch is not worth a
    crash on someone's older install.
    """
    widget = getattr(container, "toggle", None) or container.checkbox
    return widget(label, **kwargs)


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


def _editable_table(frame, columns, key, caption=None, empty_message="Nothing here."):
    """Draw one editable table and write any edits straight back into the
    master row set.

    The table is also where new lines get added now: the native "+" row at
    its bottom (_merge_table_edits turns what you type there into a proper
    counter-offer/supplement row). Edits, additions, and deletions are
    merged by row identity against what this view was showing -- never a
    wholesale replace, so a search filter or the Review tab can never wipe
    the rows it isn't displaying.
    """
    if caption:
        st.caption(caption)
    if frame.empty:
        # The editor stays on screen so the "+" row is available even
        # before the table has any lines in it.
        st.info(empty_message)
    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_order=[c for c in columns if c in frame.columns],
        column_config=_column_config(),
        key=key,
    )
    st.session_state["rows"], added_count = _merge_table_edits(
        st.session_state["rows"],
        frame,
        edited,
        int(st.session_state.get("default_margin", 20)),
    )
    if added_count:
        st.success(
            f"Added {added_count} line{'s' if added_count != 1 else ''} to your scope -- "
            "no carrier line behind it, so it exports as a supplement."
        )


def _search_box(label, key):
    """One box, no syntax to learn. Matches any column: a description, a
    trade, or a number read straight off the PDF."""
    return st.text_input(
        label, key=key,
        placeholder="Type to filter -- description, trade, a number, anything",
    )


def _export_basename(contractor_name, fields):
    """The default download name: business, carrier, claim number."""
    return _export_filename(
        contractor_name,
        _best(fields, "insurance_company", "company", default=""),
        _best(fields, "claim_number", default=""),
        "", "buildupquote.",
    ).rstrip(".")


def main():
    ui.inject_css(st)
    ui.masthead(
        st, "BuildUpQuote",
        "Upload a carrier estimate, adjust the scope, and export a proposal in your own name.",
    )

    contractor = _contractor_sidebar()

    st.session_state.setdefault("estimate", None)
    st.session_state.setdefault("rows", None)
    st.session_state.setdefault("default_margin", 20)
    st.session_state.setdefault("_uploaded_name", None)
    st.session_state.setdefault("tax_rule", tax.NONE)
    st.session_state.setdefault("tax_rate", tax.DEFAULT_TEXAS_RATE_PCT)

    uploaded = st.file_uploader(
        "Carrier estimate PDF", type=["pdf"],
        help="Files up to 200MB are fine -- no need to compress or shrink one first.",
    )

    if uploaded is not None and uploaded.name != st.session_state["_uploaded_name"]:
        size_mb = len(uploaded.getvalue()) / (1024 * 1024)
        # Reading a PDF's text is genuinely slow on a long, sketch-heavy
        # claim -- measured at ~25s on a 167-page synthetic test file,
        # nearly all of it inside pdfplumber. Without this spinner a big
        # file just leaves the page looking frozen, which reads as "it
        # never loads" even though it is still working.
        spinner_msg = "Reading your PDF..."
        if size_mb > 15:
            spinner_msg = (
                f"Reading your PDF ({size_mb:.0f}MB) -- a long claim with a lot of pages or "
                "roof/room sketch diagrams can take a few minutes here. This is normal; "
                "please don't refresh or re-upload while it's working."
            )
        with st.spinner(spinner_msg):
            try:
                estimate = parse_pdf(io.BytesIO(uploaded.getvalue()))
            except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
                st.error(
                    f"Couldn't read that PDF: {exc}. If this is a scanned or photographed "
                    "estimate rather than one exported straight from the carrier's "
                    "software, this version of the parser won't be able to read it."
                )
                return
        st.session_state["estimate"] = estimate
        st.session_state["_uploaded_name"] = uploaded.name
        # A new claim gets a new default file name. Without this, the
        # Export tab would still be offering the previous claim's name,
        # because a keyed text box keeps whatever was last typed into it.
        st.session_state.pop("export_name", None)
        # A document that already carries a contractor's markup starts at
        # 0% margin, not at the saved default -- otherwise the first
        # render has the markup applied twice before anyone has touched
        # anything. See scope_parser/doc_type.py.
        starting_margin = 0 if estimate.margin_locked_at_zero else st.session_state["default_margin"]
        st.session_state["rows"] = _rows_from_estimate(estimate, starting_margin)

    estimate = st.session_state["estimate"]
    if estimate is None:
        st.info("Upload a carrier estimate PDF to get started.")
        return

    # ---------------- claim summary ----------------
    fields = estimate.metadata.fields
    ui.cards(st, [
        ("Insured", _best(fields, "insured_name"), "info"),
        ("Claim number", _best(fields, "claim_number"), "info"),
        ("Type of loss", _best(fields, "type_of_loss"), "info"),
        ("Insurance company", _best(fields, "insurance_company", "company"), "info"),
        ("Property", _best(fields, "property_address"), "good"),
    ])

    source_program = fields.get("source_program")
    if source_program:
        version = fields.get("source_program_version")
        created = fields.get("pdf_created_at")
        bits = [f"Written in **{source_program}{f' {version}' if version else ''}**"]
        if created:
            bits.append(f"file created {created}")
        st.caption(" · ".join(bits))

    _render_read_banner(estimate)

    if estimate.warnings:
        with st.expander(
            f"⚠️ {len(estimate.warnings)} thing(s) worth double-checking before you send this out",
            expanded=False,
        ):
            for w in estimate.warnings:
                st.write(f"- {w}")

    claim_flag_notes = estimate.claim_flags.notes
    if claim_flag_notes:
        with st.expander(
            f"🧾 {len(claim_flag_notes)} claim-process thing(s) this document tells us",
            expanded=False,
        ):
            st.caption(
                "Recognized automatically from the document's own text -- not guesses. See "
                "the \"Claim Ledger\" reference doc for what each one means."
            )
            for note in claim_flag_notes:
                st.write(f"- {note}")

    if not estimate.line_items:
        kind = getattr(estimate, "document_type", None)
        if kind is not None and not kind.line_items_expected:
            # Not a failed parse. This document never had line items --
            # saying "we couldn't read it" would be both wrong and
            # useless. Naming what it is, and what to ask for instead, is
            # the answer the contractor needs.
            st.info(f"**{kind.label}.** {kind.advice}")
        else:
            st.error(
                "I could read the claim info above, but couldn't recognize any line items "
                "in this PDF -- it's likely laid out differently than the formats this "
                "version has been taught to read. Send it over so support can be added; "
                "there's nothing to edit until then."
            )
        return

    rows = st.session_state["rows"]
    code_required_count = int(_code_required_mask(rows).sum())
    added_count = int((_added_mask(rows) & ~_code_required_mask(rows)).sum())
    flagged_count = int(_flagged_mask(rows).sum())
    review_count = added_count + flagged_count
    non_carrier_count = int(_added_mask(rows).sum())  # added-by-you + code-required, for Scope's count

    tab_scope, tab_review, tab_code, tab_price, tab_export = st.tabs([
        f"📋 Scope ({len(rows) - non_carrier_count})",
        f"🔎 Review ({review_count})" if review_count else "🔎 Review",
        f"⚖️ Code Additions ({code_required_count})" if code_required_count else "⚖️ Code Additions",
        "💵 Pricing",
        "📤 Export",
    ])

    # ================= SCOPE =================
    with tab_scope:
        ui.section(
            st, "The carrier's scope", "info",
            "Every line the carrier priced. Uncheck one to drop it from your price. "
            "Anything you add yourself lives on the Review tab.",
        )

        if estimate.margin_locked_at_zero:
            # Prices that already contain somebody's markup must not get
            # another one stacked on top -- that charges it twice, and the
            # parse looks perfectly clean while it happens. See
            # scope_parser/doc_type.py, "the money bug".
            st.warning(
                "**Margin is locked at 0% for this file.** Its prices already include a "
                "contractor's markup, so adding more would charge it twice. If you meant to "
                "start from the carrier's own estimate, upload that instead."
            )
            default_margin = 0
            show_detail = _toggle(st, "Show reference columns", key="detail_locked")
        else:
            margin_col, apply_col, view_col = st.columns([3, 1, 2])
            default_margin = margin_col.slider(
                "Default margin for all items (%)", 0, 100, st.session_state["default_margin"]
            )
            apply_col.write("")
            if apply_col.button("Apply to every row", use_container_width=True):
                st.session_state["rows"]["Margin %"] = default_margin
                st.session_state["default_margin"] = default_margin
            show_detail = _toggle(
                view_col, "Show reference columns",
                help="Section, the sales-tax material flag, and the carrier's own RCV and "
                     "O&P figures. Hidden by default so the table stays readable.",
            )

        carrier_rows = rows[~_added_mask(rows)]
        query = _search_box("Search this scope", "scope_search")
        shown = ui.filter_rows(carrier_rows, query)
        if query:
            st.caption(f"Showing {len(shown)} of {len(carrier_rows)} lines.")
        _editable_table(
            shown,
            _FULL_COLUMNS if show_detail else _SIMPLE_COLUMNS,
            key="scope_editor",
            empty_message=(
                f"No line matches “{query}”." if query
                else "This document had no carrier lines."
            ),
        )

        # Roof/room measurements (surface area, squares, perimeters) are
        # read off the plan pages but aren't priced lines -- a collapsed
        # reference block keeps them available without cluttering the grid.
        # See scope_parser/measurements.py and ParsedEstimate.measurements.
        if estimate.measurements:
            with st.expander(
                f"📐 {len(estimate.measurements)} measurement block(s) from this document",
                expanded=False,
            ):
                st.caption(
                    "Roof/room figures read off the plan pages -- for reference when "
                    "comparing against the PDF, not priced lines."
                )
                for m in estimate.measurements[:20]:
                    st.write(f"- **{m.section or 'Document'}** — {m.label}: {m.value:,.2f} {m.unit}".rstrip())
                if len(estimate.measurements) > 20:
                    st.caption(f"…and {len(estimate.measurements) - 20} more")

    # ================= REVIEW =================
    with tab_review:
        ui.section(
            st, "Lines worth a second look", "warn",
            "Two kinds of line end up here: ones you added yourself, and ones the parser "
            "wasn't sure about. Everything on this tab is priced and exported exactly "
            "like a carrier line. To add your own line -- your counter-offer beyond what "
            "the carrier priced -- type it into the '+' row of the table below.",
        )

        st.markdown(
            ui.pill(f"{added_count} added by you", "added")
            + ui.pill(f"{flagged_count} need review", "warn" if flagged_count else "good"),
            unsafe_allow_html=True,
        )
        st.write("")

        added_rows = rows[_added_mask(rows) & ~_code_required_mask(rows)]
        ui.section(st, f"Added by you ({len(added_rows)})", "added")
        added_query = _search_box("Search your additions", "added_search") if not added_rows.empty else ""
        added_shown = ui.filter_rows(added_rows, added_query) if added_query else added_rows
        if added_query:
            st.caption(f"Showing {len(added_shown)} of {len(added_rows)} lines.")
        _editable_table(
            added_shown, _SIMPLE_COLUMNS, key="added_editor",
            empty_message=(
                "You haven't added anything to this scope yet -- the '+' row at the "
                "bottom of the table is where your first line goes."
            ),
        )

        flagged_rows = rows[_flagged_mask(rows)]
        ui.section(st, f"Needs review ({len(flagged_rows)})", "warn")
        if flagged_rows.empty:
            st.success(
                "Nothing needs review -- every row read cleanly. Check the 'Needs Review' "
                "box on any Scope line to send it here."
            )
        else:
            st.caption(
                "Lines the parser wasn't sure about, or lines you've checked yourself. Each "
                "shows its note; check each against the PDF using the # on the left, fix the "
                "figures here if you need to, then uncheck the 'Needs Review' box once you're "
                "satisfied and it leaves this list."
            )
            flagged_query = _search_box("Search flagged lines", "flagged_search")
            flagged_shown = ui.filter_rows(flagged_rows, flagged_query)
            if flagged_query:
                st.caption(f"Showing {len(flagged_shown)} of {len(flagged_rows)} lines.")
            _editable_table(
                flagged_shown,
                _SIMPLE_COLUMNS + ["Review Note"],
                key="flagged_editor",
            )

    # ================= CODE ADDITIONS =================
    with tab_code:
        _code_additions_tab(rows)

    # ---------------- pricing maths, once, off the master table ----------------
    rows = st.session_state["rows"]
    included = _priced(rows[rows["Include"].fillna(False)])

    # ================= PRICING =================
    with tab_price:
        _carrier_summary_panel(estimate)

        ui.section(
            st, "How this job is taxed", "info",
            "This decides whether -- and how -- sales tax gets added to your price. See the "
            "Claim Ledger reference doc if you're not sure which one applies.",
        )
        tax_col1, tax_col2 = st.columns([2, 1])
        tax_label = tax_col1.selectbox(
            "Contract type",
            options=[tax.TAX_RULE_LABELS[k] for k in tax.TAX_RULE_OPTIONS],
            index=tax.TAX_RULE_OPTIONS.index(st.session_state.get("tax_rule", tax.NONE)),
            key="tax_rule_label",
        )
        tax_rule = next(k for k in tax.TAX_RULE_OPTIONS if tax.TAX_RULE_LABELS[k] == tax_label)
        st.session_state["tax_rule"] = tax_rule
        if tax_rule in tax.ITEMIZES_TAX:
            tax_rate = tax_col2.number_input(
                "Tax rate (%)", min_value=0.0, max_value=15.0,
                value=st.session_state.get("tax_rate", tax.DEFAULT_TEXAS_RATE_PCT), step=0.05,
                key="tax_rate_input",
            )
            st.session_state["tax_rate"] = tax_rate
            st.caption(
                "Under a separated contract only material lines are taxed. Turn on "
                "\"Show reference columns\" on the Scope tab to change which lines count."
            )
        else:
            tax_rate = 0.0

        tax_amount = tax.compute_sales_tax(
            [{"line_total": r["Your Price"], "is_material": r["Material"]}
             for _, r in included.iterrows()],
            tax_rule, tax_rate,
        )
        subtotal = included["Your Price"].sum() if not included.empty else 0.0
        total = subtotal + tax_amount

        if tax_rule in tax.ITEMIZES_TAX:
            m1, m2, m3 = st.columns(3)
            m1.metric("Subtotal", f"${subtotal:,.2f}")
            m2.metric("Sales tax", f"${tax_amount:,.2f}")
            m3.metric("Total contract price", f"${total:,.2f}")
        else:
            st.metric("Total contract price", f"${total:,.2f}")

        ui.section(
            st, "Your price, line by line", "good",
            "Only the lines you've kept. Edit Qty, Unit Cost, or Margin here and the "
            "total updates below. Search it the same way as the scope.",
        )
        display = included[["#", "Trade", "Description", "Qty", "Unit", "Unit Cost", "Margin %", "Your Price"]].copy()
        price_query = _search_box("Search your price list", "price_search")
        display = ui.filter_rows(display, price_query)
        if price_query:
            st.caption(f"Showing {len(display)} of {len(included)} lines.")
        _editable_table(
            display,
            ["#", "Trade", "Description", "Qty", "Unit", "Unit Cost", "Margin %", "Your Price"],
            key="price_editor",
            empty_message="No lines match this filter.",
        )

        ui.section(
            st, "Totals by trade", "info",
            "Where this price is coming from, grouped the same way the proposal PDF will "
            "group it -- highest first.",
        )
        trade_totals = _trade_totals(included)
        trade_totals_display = trade_totals.copy()
        trade_totals_display["Subtotal"] = trade_totals_display["Subtotal"].map(lambda v: f"${v:,.2f}")
        st.dataframe(trade_totals_display, use_container_width=True, hide_index=True)

        ui.section(
            st, "When each part gets paid", "good",
            "Not all of this is due at once -- here's the order it typically arrives in.",
        )
        deductible = _effective_deductible(estimate)
        if deductible is None:
            st.warning(
                "No deductible amount was found anywhere in this document -- not in the "
                "coverage table, not in the summary ladder. Enter it manually."
            )
            deductible = st.number_input(
                "Enter the deductible manually ($)", min_value=0.0, value=0.0, step=100.0,
                key="manual_deductible",
            )
        breakdown = _payment_breakdown(included, deductible, total)
        if breakdown["first_check"] < 0:
            st.error(
                f"The deductible, recoverable depreciation, and supplements below add up to "
                f"${-breakdown['first_check']:,.2f} more than the total contract price -- "
                "double-check these numbers before sending this out."
            )
        p1, p2, p3, p4 = st.columns(4)
        p1.metric(
            "1. Deductible", f"${breakdown['deductible']:,.2f}",
            help="Owed directly to you by the homeowner, in full -- by Texas law, never paid "
                 "by insurance and never absorbed into your price (Bus. & Com. Code Sec. 27.02, "
                 "Ins. Code Ch. 707).",
        )
        p2.metric(
            "2. Due on 1st check", f"${breakdown['first_check']:,.2f}",
            help="The initial ACV-based payment from insurance -- everything left over once "
                 "the deductible, recoverable depreciation, and supplements are set aside.",
        )
        p3.metric(
            "3. Recoverable depreciation", f"${breakdown['recoverable_depreciation']:,.2f}",
            help="Paid by insurance on the second check, once repairs are complete and proof "
                 "is submitted. The carrier's own figure -- fixed, not affected by your margin.",
        )
        p4.metric(
            "4. Supplements", f"${breakdown['supplements']:,.2f}",
            help="Items you've added beyond the carrier's original estimate, at your price. "
                 "Typically paid on completion, once approved.",
        )

    # ================= EXPORT =================
    with tab_export:
        ui.section(
            st, "Name your files", "info",
            "Both downloads use this name. It starts from your business, the carrier and the "
            "claim number -- change it to whatever you'd rather find in your downloads folder.",
        )
        default_name = _export_basename(contractor.name, fields)
        chosen_name = st.text_input(
            "File name (no extension needed)",
            value=default_name,
            key="export_name",
            help="Slashes and other characters your computer won't accept are removed "
                 "automatically. Leave it empty to fall back to the standard name.",
        )
        csv_filename = ui.sanitize_filename(chosen_name, "csv", "scope.csv")
        pdf_filename = ui.sanitize_filename(chosen_name, "pdf", "proposal.pdf")
        st.caption(f"Will save as **{csv_filename}** and **{pdf_filename}**.")

        ui.section(st, "Download", "good")
        dl_col1, dl_col2 = st.columns(2)
        dl_col1.download_button(
            "⬇️ Scope as CSV",
            data=included.drop(columns=["Needs Review", "Review Note"]).to_csv(index=False),
            file_name=csv_filename,
            mime="text/csv",
            use_container_width=True,
        )

        if not contractor.name:
            dl_col2.info("Add your business name in the sidebar to enable the branded proposal PDF.")
        else:
            proposal_data = build_proposal(
                st.session_state["rows"].to_dict("records"),
                contractor,
                fields,
                proposal_date=datetime.date.today().strftime("%m/%d/%Y"),
                tax_rule=st.session_state["tax_rule"],
                tax_rate_pct=st.session_state.get("tax_rate", 0.0)
                if st.session_state["tax_rule"] in tax.ITEMIZES_TAX else 0.0,
                deductible_amount=_effective_deductible(estimate)
                or st.session_state.get("manual_deductible", 0.0),
            )
            try:
                pdf_path = os.path.join(tempfile.gettempdir(), "buildupquote_proposal.pdf")
                render_proposal_pdf(proposal_data, pdf_path)
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                dl_col2.download_button(
                    "⬇️ Branded proposal PDF",
                    data=pdf_bytes,
                    file_name=pdf_filename,
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                )
            except Exception as exc:  # noqa: BLE001 -- surfaced, not swallowed
                dl_col2.error(
                    f"Couldn't build the proposal PDF: {exc}. This usually means "
                    "WeasyPrint isn't installed in the venv this app is running "
                    "in -- run `pip install -r requirements.txt` and restart the "
                    "app (Ctrl+C, then `streamlit run app.py`)."
                )

        st.caption(
            "The proposal PDF only ever shows your price -- quantity, unit, unit price, a "
            "subtotal, sales tax if your contract type itemizes it, and the total. It never "
            "includes the carrier's depreciation, ACV, or condition figures."
        )

    # ---- sticky quote totals ----
    # Always visible at the bottom of the page, recomputed every rerun so
    # the bar tracks whatever the contractor has edited in any table.
    current_rows = st.session_state.get("rows")
    if current_rows is not None:
        tax_rule = st.session_state.get("tax_rule", tax.NONE)
        tax_rate = st.session_state.get("tax_rate", tax.DEFAULT_TEXAS_RATE_PCT)
        if tax_rule not in tax.ITEMIZES_TAX:
            tax_rate = 0.0
        subtotal, tax_amount, total, line_count = _quote_totals(current_rows, tax_rule, tax_rate)
        ui.totals_bar(st, subtotal, tax_amount, total, line_count)


if __name__ == "__main__":
    main()
