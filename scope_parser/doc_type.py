"""What KIND of document is this?

Separate question from "which program wrote it", and the more dangerous of
the two. The program decides how to READ the numbers; the document type
decides what the app is ALLOWED TO DO with them.

Six types show up in this business (see the research summarised in
`claude/multi-format-architecture.md`):

  carrier scope          the normal path -- everything else was built for this
  appraisal report       normal path, but multi-tier tax/O&P shift the schema
  supplement package     TWO scopes side by side plus a delta column
  supplement reinspection  a later-pass estimate that says so in its own text
  contractor proposal    already marked up -- this app's own output shape
  settlement statement   no line items at all, by design

Two of those are genuinely hazardous, and neither announces itself:

THE MONEY BUG -- a contractor proposal. Someone will upload last year's
proposal, a competitor's, or their own. The generic reader's arithmetic
succeeds perfectly on it, because qty x price = total holds. It just holds
on a price that already contains the contractor's markup. The parse looks
clean and the number is quietly wrong, with margin charged twice. No
carrier prints a "Markup %" column, so that column is the tell -- and a
document carrying it must import with margin locked at zero.

THE TRUST BUG -- a settlement statement. It has no line items because it
was never a scope. Left alone, it lands on the app's "couldn't recognise
this layout" message, which reads as a broken app when nothing is broken.
The useful answer names what the document actually is and says what to ask
the adjuster for instead.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

CARRIER_SCOPE = "carrier_scope"
APPRAISAL_REPORT = "appraisal_report"
SUPPLEMENT_PACKAGE = "supplement_package"
SUPPLEMENT_REINSPECTION = "supplement_reinspection"
CONTRACTOR_PROPOSAL = "contractor_proposal"
SETTLEMENT_STATEMENT = "settlement_statement"

# "Markup %" / "Mark-up %" / "Markup Pct" -- a contractor's own column.
_MARKUP_RE = re.compile(r"\bMARK[\s-]?UP\b\s*(%|PCT|PERCENT)?", re.IGNORECASE)
_CONTRACTOR_PRICE_RE = re.compile(r"\bCONTRACTOR\s+(UNIT\s+)?PRICE\b", re.IGNORECASE)
_SUPPLEMENT_DELTA_RE = re.compile(r"\bSUPPLEMENT\s+DELTA\b", re.IGNORECASE)
_CARRIER_SCOPED_RE = re.compile(r"\bCARRIER\s+SCOPED\b", re.IGNORECASE)
_REQUESTED_SCOPE_RE = re.compile(r"\bREQUESTED\s+SCOPE\b", re.IGNORECASE)
_SETTLEMENT_RE = re.compile(
    r"\b(NET\s+CLAIM\s+PAYABLE|STATEMENT\s+OF\s+LOSS|NET\s+CLAIM\b|CLAIM\s+SETTLEMENT\s+SUMMARY)\b",
    re.IGNORECASE,
)


@dataclass
class DocumentType:
    kind: str = CARRIER_SCOPE
    label: str = "Carrier adjuster scope"
    # The one sentence the workspace should show about this document.
    advice: str = ""
    # True when the prices in this document already include a contractor's
    # markup, so applying a margin on top would charge it twice.
    prices_already_marked_up: bool = False
    # True when this kind of document is not expected to contain any
    # priced scope rows -- so finding none is the correct result, not a
    # parsing failure.
    line_items_expected: bool = True
    signals: list = field(default_factory=list)

    @property
    def is_usable_as_carrier_scope(self) -> bool:
        return self.kind in (CARRIER_SCOPE, APPRAISAL_REPORT)


def detect(lines, item_count=0, claim_flags=None, has_anchors=True) -> DocumentType:
    """`lines` is the noise-filtered document text. `item_count` is how
    many line items were parsed. `claim_flags` is the existing ClaimFlags
    (used for the appraisal/public-adjuster signals it already computes).
    `has_anchors` says whether any quantity/unit pairs exist anywhere.
    """
    text = "\n".join(lines)

    markup = _MARKUP_RE.search(text)
    contractor_price = _CONTRACTOR_PRICE_RE.search(text)
    if markup and (contractor_price or item_count):
        return DocumentType(
            kind=CONTRACTOR_PROPOSAL,
            label="Contractor proposal (already marked up)",
            advice=(
                "This looks like a contractor's own proposal, not a carrier estimate -- "
                "it has a markup column, which no carrier prints. Its prices already "
                "include someone's margin, so margin is locked at 0% here. Adding more "
                "would charge the markup twice."
            ),
            prices_already_marked_up=True,
            signals=[
                "found a markup column",
                "found a contractor unit-price column" if contractor_price else "priced rows present",
            ],
        )

    if _SUPPLEMENT_DELTA_RE.search(text) or (
        _CARRIER_SCOPED_RE.search(text) and _REQUESTED_SCOPE_RE.search(text)
    ):
        return DocumentType(
            kind=SUPPLEMENT_PACKAGE,
            label="Supplement / counter-quote package",
            advice=(
                "This document puts two scopes side by side -- what the carrier allowed "
                "and what's being requested -- plus the difference between them. Tell us "
                "which scope you want priced before relying on these numbers."
            ),
            signals=["found side-by-side carrier/requested scope columns"],
        )

    if claim_flags is not None and getattr(claim_flags, "is_supplement_document", False):
        return DocumentType(
            kind=SUPPLEMENT_REINSPECTION,
            label="Supplement / reinspection claim",
            advice=(
                "This document describes itself as a supplement or reinspection rather "
                "than an original estimate -- treat these totals as an addition to a "
                "prior claim, not the whole claim."
            ),
            signals=["document describes itself as a supplement or reinspection"],
        )

    if _SETTLEMENT_RE.search(text) and (item_count == 0 or not has_anchors):
        return DocumentType(
            kind=SETTLEMENT_STATEMENT,
            label="Settlement statement of loss",
            advice=(
                "This is a settlement statement, not a scope. It shows what the carrier "
                "is paying -- replacement cost, depreciation, the deductible and the net "
                "cheque -- but not what work is in the job. You'll want the adjuster's "
                "scope document as well; that's the one with the line items on it."
            ),
            line_items_expected=False,
            signals=["found settlement summary wording and no priced scope rows"],
        )

    if claim_flags is not None and (
        getattr(claim_flags, "is_appraisal_document", False)
        or getattr(claim_flags, "is_public_adjuster_document", False)
    ):
        return DocumentType(
            kind=APPRAISAL_REPORT,
            label="Independent appraisal / public-adjuster estimate",
            advice=(
                "This is an appraisal or public-adjuster estimate rather than the "
                "carrier's own. It's priced the same way, but tax and overhead can be "
                "broken out in more tiers than a carrier estimate uses -- worth checking "
                "those columns before you set your margin."
            ),
            signals=["document identifies itself as an appraisal or public-adjuster estimate"],
        )

    return DocumentType(
        kind=CARRIER_SCOPE,
        label="Carrier adjuster scope",
        advice="",
        signals=["priced scope rows with carrier-style columns"],
    )
