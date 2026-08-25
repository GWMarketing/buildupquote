"""Recognizing the CLAIM-PROCESS realities described in the "Claim Ledger"
reference doc -- as opposed to the RCV/ACV/depreciation *math*, which the
rest of this package already handles. Percentage vs. flat deductibles,
mentions of a mortgagee, ordinance-or-law coverage, a cosmetic damage
exclusion, whether the document is itself an appraisal or public-adjuster
estimate rather than the carrier's own -- all synonym/pattern based,
deterministic, no AI, same as every other module in this package.

Every flag follows the same anti-guessing rule as `needs_review`
elsewhere in this project: a flag is only ever set when a specific,
real textual signal was found, and `ClaimFlags.notes` always records
*why*, in plain language, so this is auditable rather than a black box.
Absence of a flag means "not found in this document," never "confirmed
absent" -- e.g. a mortgagee can be on a claim without the word ever
appearing in an Xactimate-style repair estimate, which usually isn't
where that information lives.
"""
import re

from .codes import CODE_CITATION_RE
from .models import ClaimFlags
from .tokens import parse_number

# ---------------------------------------------------------------------
# Deductible math (Claim Ledger section 8)
# ---------------------------------------------------------------------

_COVERAGE_TABLE_HEADER_RE = re.compile(
    r"\bCoverage\b.*\bDeductible\b.*\bPolicy Limit\b", re.IGNORECASE
)
# "Dwelling $5,300.00 $265,000.00" -- but NOT "Dwelling - Tree Coverage
# $0.00 ..." (a sub-coverage row, not the main Coverage A limit).
_DWELLING_ROW_RE = re.compile(
    r"^Dwelling\b(?!\s*-)\s+\$?([\d,]+\.\d{2})\s+\$?([\d,]+\.\d{2})", re.IGNORECASE
)
# Fallback when there's no cover-sheet coverage table at all (common on a
# trimmed excerpt or an appraiser's counter-estimate) -- the claim-summary
# "Less Deductible (X)" line every carrier estimate prints regardless.
_LESS_DEDUCTIBLE_RE = re.compile(r"Less Deductible\D{0,3}\(?\$?([\d,]+\.\d{2})\)?", re.IGNORECASE)

# Wind/hail deductibles in Texas are overwhelmingly written as one of a
# small set of round percentages of the dwelling limit. If the printed
# dollar deductible divided by the printed dwelling limit lands within
# tolerance of one of these, it's a percentage deductible in disguise --
# the estimate itself never has to say the word "percentage" for this to
# be true, since carriers print the computed dollar figure, not its origin.
_PCT_CANDIDATES = (0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.10)
_PCT_TOLERANCE = 0.002  # +/- 0.2 percentage points, to absorb rounding


def _infer_deductible_type(deductible, policy_limit):
    if not deductible or not policy_limit:
        return "unknown", None
    ratio = deductible / policy_limit
    for pct in _PCT_CANDIDATES:
        if abs(ratio - pct) <= _PCT_TOLERANCE:
            return "percentage", round(pct * 100, 2)
    return "flat", None


# ---------------------------------------------------------------------
# Document identity / coverage & endorsement mentions (sections 9-14)
# ---------------------------------------------------------------------

_APPRAISAL_PATTERNS = (
    r"\bthis appraisal\b", r"\bthe appraisal process\b", r"\bindependent appraiser\b",
    r"\bappraisal (has been|is) (based|executed)\b", r"\bonce the award (has been|is) executed\b",
    r"\ban? umpire\b",
)
_PUBLIC_ADJUSTER_PATTERNS = (
    r"\bpublic adjuster\b", r"\bpublic insurance adjuster\b", r"\bpa license\b",
)
_SUPPLEMENT_PATTERNS = (
    r"\bsupplement(al)?\s+(estimate|claim|request)\b", r"\breinspection\b",
    r"\bsupplement\s*#\s*\d+\b",
)
_MORTGAGEE_PATTERNS = (
    r"\bmortgagee\b", r"\bloss payee\b", r"\batima\b", r"\blienholder\b",
)
_ORDINANCE_PATTERNS = (
    r"\bordinance or law\b", r"\bincreased cost of construction\b",
    r"\bcode upgrade coverage\b", r"\blaw and ordinance\b",
)
_COSMETIC_PATTERNS = (
    r"\bho-?145\b", r"\bcosmetic damage exclusion\b", r"\bcosmetic exclusion\b",
    r"\bcosmetic loss or damage\b",
)


def _search_any(text, patterns):
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def compute_claim_flags(lines, line_items) -> ClaimFlags:
    """lines: the noise-filtered, boilerplate-page-excluded lines pipeline.py
    already has on hand (same input line_items.py works from) -- NOT raw
    PDF text, so a carrier's own "how to read your estimate" insert page
    can't fake a mortgagee mention or a percentage deductible. line_items:
    the already-parsed LineItem list, for the code-related rollup."""
    text = "\n".join(lines)
    flags = ClaimFlags()

    # -- deductible math --
    dwelling_deductible = dwelling_policy_limit = None
    for i, line in enumerate(lines):
        if _COVERAGE_TABLE_HEADER_RE.search(line):
            for follow in lines[i + 1: i + 12]:
                m = _DWELLING_ROW_RE.match(follow.strip())
                if m:
                    dwelling_deductible = parse_number(m.group(1))
                    dwelling_policy_limit = parse_number(m.group(2))
                    break
            break
    if dwelling_deductible is None:
        m = _LESS_DEDUCTIBLE_RE.search(text)
        if m:
            dwelling_deductible = parse_number(m.group(1))

    flags.dwelling_deductible = dwelling_deductible
    flags.dwelling_policy_limit = dwelling_policy_limit
    if dwelling_deductible:
        flags.deductible_type, flags.deductible_pct = _infer_deductible_type(
            dwelling_deductible, dwelling_policy_limit
        )
        if flags.deductible_type == "percentage":
            flags.notes.append(
                f"The ${dwelling_deductible:,.2f} deductible works out to {flags.deductible_pct:g}% of the "
                f"${dwelling_policy_limit:,.2f} dwelling policy limit -- a percentage wind/hail deductible, "
                "not a flat one. See the Claim Ledger, section 8."
            )
        elif flags.deductible_type == "flat" and dwelling_policy_limit:
            flags.notes.append(
                f"${dwelling_deductible:,.2f} deductible against a ${dwelling_policy_limit:,.2f} policy "
                "limit doesn't match a common percentage deductible -- looks like a flat dollar amount."
            )
        else:
            flags.notes.append(
                f"Found a ${dwelling_deductible:,.2f} deductible but no policy limit anywhere on this "
                "document -- can't tell whether it's flat or a percentage from the estimate alone. Check "
                "the declarations page. See the Claim Ledger, section 8."
            )

    # -- what kind of document this is --
    if _search_any(text, _APPRAISAL_PATTERNS):
        flags.is_appraisal_document = True
        flags.notes.append(
            "This document repeatedly refers to itself as an appraisal -- it looks like an independent "
            "appraiser's estimate (produced for the Appraisal Clause process), not the carrier's own "
            "adjuster estimate. See the Claim Ledger, section 13."
        )
    if _search_any(text, _PUBLIC_ADJUSTER_PATTERNS):
        flags.is_public_adjuster_document = True
        flags.notes.append(
            "Mentions a public adjuster -- this may have been prepared on the homeowner's behalf by a "
            "licensed PA rather than by the carrier. See the Claim Ledger, section 14."
        )
    if _search_any(text, _SUPPLEMENT_PATTERNS):
        flags.is_supplement_document = True
        flags.notes.append(
            "Looks like a supplement or reinspection rather than an original estimate -- treat these "
            "totals as an addition to a prior claim, not the whole claim. See the Claim Ledger, section 11."
        )

    # -- coverage / endorsement mentions --
    if _search_any(text, _MORTGAGEE_PATTERNS):
        flags.mortgagee_mentioned = True
        flags.notes.append(
            "Mentions a mortgagee/loss payee -- expect the claim check to be issued jointly, which can "
            "delay when funds are actually available. See the Claim Ledger, section 12."
        )
    if _search_any(text, _ORDINANCE_PATTERNS):
        flags.ordinance_or_law_mentioned = True
        flags.notes.append(
            "Explicitly mentions Ordinance or Law / code-upgrade coverage -- confirms there's a coverage "
            "bucket for the code-driven items below, not just a code argument with nothing to fund it. "
            "See the Claim Ledger, section 10."
        )
    if _search_any(text, _COSMETIC_PATTERNS):
        flags.cosmetic_exclusion_mentioned = True
        flags.notes.append(
            "Mentions a cosmetic damage exclusion -- if this is the roof, there may be no recoverable "
            "depreciation to chase on it no matter how promptly repairs are completed. See the Claim "
            "Ledger, section 9."
        )

    # -- how much of the estimate is code-driven --
    code_items = [li for li in line_items if li.code_related]
    flags.code_related_item_count = len(code_items)
    flags.code_related_rcv_total = round(sum(li.rcv or 0 for li in code_items), 2)
    flags.document_code_citation_count = len(CODE_CITATION_RE.findall(text))
    if code_items:
        flags.notes.append(
            f"{len(code_items)} line item(s) totaling ${flags.code_related_rcv_total:,.2f} RCV cite a "
            "specific building code section -- that's the portion of this estimate that needs "
            "Ordinance-or-Law coverage to actually get paid. See the Claim Ledger, sections 10 and 15."
        )
    if flags.document_code_citation_count > len(code_items):
        # A citation printed as section-level scope language ("IBC 1511.3
        # Roof Replacement" ahead of item 1) rather than attached to one
        # specific item's notes -- still real, just not tied to a single
        # line's RCV, so it isn't in code_related_rcv_total above.
        flags.notes.append(
            f"The document also references building codes {flags.document_code_citation_count} time(s) "
            "in total, including section-level scope language not tied to one specific line item -- worth "
            "a manual read of the notes above each section before assuming the number above is complete."
        )

    return flags
