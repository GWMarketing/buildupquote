"""Plain data structures produced by the parsing engine.

Kept deliberately dumb (no behavior) so the UI layer, tests, and any future
export code can all depend on this one shared shape without depending on
each other.
"""
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

# Type-only imports: carrier_summary lives in this package and is imported
# directly (it only needs `parse_number`, no cycle); the other three are
# imported under TYPE_CHECKING so the annotations below are real classes
# rather than bare `object`, without creating a circular import at runtime
# (fingerprint.py, doc_type.py and confidence.py each import .models).
if TYPE_CHECKING:
    from .carrier_summary import CarrierSummary as _CarrierSummary
    from .confidence import ParseConfidence as _ParseConfidence
    from .doc_type import DocumentType as _DocumentType
    from .fingerprint import FormatFingerprint as _FormatFingerprint
else:
    _CarrierSummary = object
    _ParseConfidence = object
    _DocumentType = object
    _FormatFingerprint = object


@dataclass
class LineItem:
    number: str  # e.g. "10a" -- keep as the printed string, not an int
    description: str
    quantity: Optional[float]
    unit: Optional[str]

    # These come straight off the carrier's PDF. They are kept because the
    # totals-consistency check and any future audit trail needs them, NOT
    # because they should be shown to the homeowner -- see
    # CUSTOMER_FACING_FIELDS below for what the proposal export is allowed
    # to use directly.
    unit_price: Optional[float] = None
    tax: Optional[float] = None
    overhead_profit: Optional[float] = None
    rcv: Optional[float] = None
    age: Optional[str] = None
    life: Optional[str] = None
    condition: Optional[str] = None
    depreciation_pct: Optional[str] = None
    depreciation: Optional[float] = None
    depreciation_recoverable: Optional[bool] = None
    acv: Optional[float] = None

    section: str = ""
    notes: list = field(default_factory=list)

    # True when the description or notes cite a specific building-code
    # section (IRC/IBC/R9xx -- see codes.py) -- i.e. this line exists
    # because current code requires it, not just because of storm damage.
    # Used to total up how much of the estimate is code-driven, which is
    # the number that matters against Ordinance-or-Law coverage (see
    # claim_flags.py and the Claim Ledger reference doc, section 10).
    code_related: bool = False

    # Anything the parser wasn't confident about lands here instead of
    # silently guessing -- this is the safety valve the project's legal/
    # parsing notes call for.
    needs_review: bool = False
    review_reason: Optional[str] = None
    raw_tail_tokens: list = field(default_factory=list)


# Fields it is safe to hand to the branded proposal / contractor workspace.
# Deliberately excludes age, life, condition, depreciation*, acv -- per the
# project's legal notes, those are for the insurer/homeowner reconciliation
# only and must never be presented as what the contractor is owed.
CUSTOMER_FACING_FIELDS = (
    "number",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "rcv",
    "section",
)


@dataclass
class MeasurementBlock:
    section: str
    label: str
    value: float
    unit: str = ""  # "SF", "LF", "SY", "SQ", or "" for a bare count


@dataclass
class ClaimMetadata:
    fields: dict = field(default_factory=dict)

    def get(self, key, default=None):
        return self.fields.get(key, default)


@dataclass
class ClaimFlags:
    """What the document itself tells us about the *claim process* around
    the numbers -- as opposed to the numbers themselves. Computed by
    claim_flags.py via synonym/pattern matching against the document text,
    the same anti-guessing philosophy as needs_review: a flag is only ever
    set when a real, specific signal was found, and `notes` always says
    what that signal was so this is auditable, not a black box. See the
    Claim Ledger reference doc (published as a Claude artifact) for what
    each of these actually means and why it matters.
    """
    # -- deductible math (Claim Ledger section 8) --
    dwelling_deductible: Optional[float] = None
    dwelling_policy_limit: Optional[float] = None
    # "percentage" | "flat" | "unknown" -- "unknown" means a deductible was
    # found but there wasn't enough information (usually no policy limit
    # printed) to tell which kind it is. Never guessed.
    deductible_type: str = "unknown"
    deductible_pct: Optional[float] = None  # set only when deductible_type == "percentage"

    # -- what kind of document this is (sections 11, 13, 14) --
    is_appraisal_document: bool = False
    is_public_adjuster_document: bool = False
    is_supplement_document: bool = False

    # -- coverage/endorsement mentions (sections 9, 10, 12) --
    mortgagee_mentioned: bool = False
    ordinance_or_law_mentioned: bool = False
    cosmetic_exclusion_mentioned: bool = False

    # -- how much of the estimate is code-driven (section 10) --
    code_related_item_count: int = 0
    code_related_rcv_total: float = 0.0
    # A code citation can also appear as section-level scope language
    # ("IBC 1511.3 Roof Replacement") printed before any item number, which
    # never gets attached to one specific LineItem's notes -- this counts
    # every citation found anywhere in the document, so that case still
    # surfaces even when code_related_item_count under-counts it.
    document_code_citation_count: int = 0

    # Human-readable one-liners, one per flag actually set above, in the
    # order app.py should show them -- this list IS what the UI displays,
    # so it's the one thing this class isn't allowed to leave silently
    # implicit.
    notes: list = field(default_factory=list)


@dataclass
class CarrierSummary:
    """The document's own bottom-line ladder -- the block every carrier
    estimate prints once, after all the line items, that this package
    never read before (see carrier_summary.py). Every field here is a
    number the DOCUMENT ITSELF printed; the fields we couldn't find on a
    given document stay None rather than being computed or guessed --
    same anti-guessing rule as everywhere else in this project.

    Two different carriers word this ladder differently (Xactimate:
    "Net Claim"; Symbility: "Net Estimate") -- source_label records which
    one this document actually used, so the app can show the carrier's
    own word back to the contractor instead of a generic one.
    """
    line_item_total: Optional[float] = None
    material_sales_tax: Optional[float] = None
    overhead: Optional[float] = None
    profit: Optional[float] = None
    replacement_cost_value: Optional[float] = None
    less_depreciation: Optional[float] = None
    actual_cash_value: Optional[float] = None
    deductible: Optional[float] = None
    net_claim: Optional[float] = None
    total_recoverable_depreciation: Optional[float] = None
    net_claim_if_depreciation_recovered: Optional[float] = None
    source_label: str = "Net Claim"
    # "AA-Dwelling", "Dwelling", etc. -- set only when the document's
    # summary block is explicitly headed "Summary for <coverage>", which
    # means line_item_total covers just THAT coverage, not the whole
    # claim (a document with Dwelling + Other Structures + Personal
    # Property prints one of these per coverage). None means either a
    # single-coverage document (nothing to disambiguate) or a format
    # this module doesn't recognise a heading for.
    coverage_label: Optional[str] = None

    # Set by pipeline.py, once our own line items are summed -- None until
    # then (or forever, on a document with no "Line Item Total" line to
    # check against, e.g. Symbility -- see pipeline.py's comment).
    reconciles_with_parsed_items: Optional[bool] = None
    parsed_items_sum: Optional[float] = None

    @property
    def _op_base(self):
        """What overhead/profit are actually a percentage OF. Xactimate
        computes both off the tax-inclusive subtotal (Line Item Total +
        Material Sales Tax), not off the bare line item total -- skipping
        the tax here would read Williams1's real 15%/15% as a misleading
        15.41%/15.41%. When there's no tax line, this is just the line
        item total, so it doesn't change Doyle's clean 10.0%/10.0%."""
        if not self.line_item_total:
            return None
        return self.line_item_total + (self.material_sales_tax or 0)

    @property
    def overhead_pct(self):
        base = self._op_base
        if self.overhead is not None and base:
            return round(self.overhead / base * 100, 2)
        return None

    @property
    def profit_pct(self):
        base = self._op_base
        if self.profit is not None and base:
            return round(self.profit / base * 100, 2)
        return None

    @property
    def combined_markup_pct(self):
        """Overhead % + Profit %, rounded once combined rather than
        rounding each half first -- e.g. Doyle's 10.0% + 10.0% reads as
        20.0%, not 20.0%-ish from two independently-rounded halves."""
        base = self._op_base
        parts = [p for p in (self.overhead, self.profit) if p is not None]
        if not parts or not base:
            return None
        return round(sum(parts) / base * 100, 2)

    @property
    def has_content(self):
        return any(
            v is not None for v in (
                self.line_item_total, self.replacement_cost_value, self.net_claim,
            )
        )


@dataclass
class SectionTotals:
    section: str
    printed_numbers: list
    parsed_rcv_sum: float
    matched: bool
    closest_diff: Optional[float] = None
    skipped: bool = False  # True = this looked like a rollup/grand-total
    # line rather than a fresh section subtotal, so a "mismatch" here isn't
    # meaningful -- see totals.py.


@dataclass
class ParsedEstimate:
    metadata: ClaimMetadata
    line_items: list
    measurements: list
    section_totals: list  # list[SectionTotals]
    discarded_lines: list  # noise/unrecognized lines, kept for debugging
    warnings: list
    # Free-text prose the parser found outside any line item -- trailing
    # adjuster remarks, scope-of-work paragraphs, note blocks. Kept so the
    # contractor can see (and optionally export) what the adjuster wrote.
    document_notes: list = field(default_factory=list)
    claim_flags: ClaimFlags = field(default_factory=ClaimFlags)

    # --- what this document IS, as opposed to what it says -----------
    # All three are set by pipeline.parse_text(). They describe the parse
    # rather than the estimate, which is why the golden-snapshot lock in
    # tests/ deliberately does not cover them -- see tests/golden_support.py.
    fingerprint: Optional[_FormatFingerprint] = None
    document_type: Optional[_DocumentType] = None
    confidence: Optional[_ParseConfidence] = None
    carrier_summary: Optional[_CarrierSummary] = None

    @property
    def needs_review_items(self):
        return [li for li in self.line_items if li.needs_review]

    @property
    def source_program(self):
        """Human-readable name of the program that wrote this file, or
        None when nothing on the document or in its metadata says."""
        if self.fingerprint is None:
            return None
        return self.fingerprint.program_name or (
            self.fingerprint.identified_as if self.fingerprint.is_recognised else None
        )

    @property
    def margin_locked_at_zero(self):
        """True when this document's prices already include somebody's
        markup, so the workspace must not add another one on top."""
        return bool(self.document_type and self.document_type.prices_already_marked_up)
