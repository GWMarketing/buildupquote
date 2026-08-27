"""Data structures for the branded proposal export.

`ProposalLineItem` deliberately has no room for depreciation, ACV, age, or
condition -- unlike scope_parser.models.LineItem, which keeps those for
the totals-consistency check. That's on purpose: it's a structural
guarantee (not just a filter someone could forget to apply) that those
insurance-only numbers can never end up on a document handed to a
homeowner. See the project's legal/parsing notes, problem #3.
"""
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_TERMS = (
    "This proposal reflects the scope of work described above at the price(s) shown, "
    "which represents the full cost to complete the described repairs regardless of "
    "how the loss is settled with the insurance carrier. Any policy deductible is the "
    "homeowner's responsibility and is separate from, and in addition to, insurance "
    "proceeds -- it is not deducted from or absorbed into the price above. This "
    "proposal is valid for 30 days from the date below. Any change to the scope of "
    "work after acceptance requires a written change order signed by both parties. "
    "This is a general-purpose template -- have it reviewed against your state's "
    "contracting and insurance-restoration requirements before use."
)

# Texas Business and Commerce Code § 27.02 ("Goods or Services Paid for by
# Insurance Proceeds: Payment of Deductible Required") requires a contract
# of $1,000 or more, where the seller reasonably expects to be paid from
# property insurance proceeds, to carry EXACTLY this notice, in at least
# 12-point boldfaced type -- not a paraphrase. Violating it (waiving,
# absorbing, or helping a homeowner avoid their deductible) is a Class B
# misdemeanor. This is distinct from Texas Insurance Code § 707.002, which
# only obligates the INSURED to pay their own deductible and carries no
# penalty of its own -- § 27.02 is the one that actually binds a
# contractor and requires this printed notice. (An earlier version of the
# project's legal notes cited this as Insurance Code § 707.002 / HB 2102,
# then as "Insurance Code § 27.02" -- both wrong on the code title; verified
# directly against the statute text 2026-08-24, see parsing-engine-status.md.)
TX_DEDUCTIBLE_NOTICE = (
    "Texas law requires a person insured under a property insurance policy to pay "
    "any deductible applicable to a claim made under the policy. It is a violation "
    "of Texas law for a seller of goods or services who reasonably expects to be "
    "paid wholly or partly from the proceeds of a property insurance claim to "
    "knowingly allow the insured person to fail to pay, or assist the insured "
    "person's failure to pay, the applicable insurance deductible."
)
TX_DEDUCTIBLE_NOTICE_THRESHOLD = 1000.0

# Both statutes a 15-year contractor asked to see cited together on the
# payment schedule (2026-08-24): Sec. 27.02 is the one that actually binds
# the seller/contractor (see TX_DEDUCTIBLE_NOTICE above); Insurance Code
# Ch. 707 is the companion provision obligating the INSURED to pay their
# own deductible. Citing both together is accurate -- they're not
# alternate citations for the same rule, they're the two halves of it.
TX_DEDUCTIBLE_LAW_CITATION = "Texas Business & Commerce Code Sec. 27.02 and Texas Insurance Code Ch. 707"


@dataclass
class ProposalLineItem:
    trade: str
    description: str
    quantity: float
    unit: str
    unit_price: float
    line_total: float
    # Used only to compute sales tax under a separated-contract rule (see
    # tax.py) -- defaults to True (treated as material) since undercharging
    # tax is the costlier mistake. Never rendered on the proposal itself.
    is_material: bool = True
    # Plain-English why-this-line-is-here, carried from the row's Review
    # Note for code-required additions (see build_proposal). Rendered on
    # the proposal as a small note row underneath the item -- the
    # justification an adjuster can read without having to ask.
    note: str = ""


@dataclass
class TradeGroup:
    trade: str
    items: List[ProposalLineItem]
    subtotal: float


@dataclass
class ContractorInfo:
    name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    license_number: str = ""
    logo_path: Optional[str] = None  # path to an image file; embedded inline when rendered


@dataclass
class ClaimInfo:
    insured_name: str = ""
    property_address: str = ""
    insurance_company: str = ""
    claim_number: str = ""
    policy_number: str = ""
    type_of_loss: str = ""
    date_of_loss: str = ""


@dataclass
class ProposalData:
    contractor: ContractorInfo
    claim: ClaimInfo
    grouped_items: List[TradeGroup]
    total_price: float
    proposal_date: str = ""
    proposal_number: str = ""
    terms: str = field(default=DEFAULT_TERMS)
    # See TX_DEDUCTIBLE_NOTICE above -- the template only prints this when
    # total_price >= TX_DEDUCTIBLE_NOTICE_THRESHOLD, per the statute's own
    # $1,000 trigger. Kept as its own field (not folded into `terms`)
    # because the statute requires it in its own distinct 12pt bold type,
    # not just present somewhere in the terms paragraph.
    deductible_notice: str = field(default=TX_DEDUCTIBLE_NOTICE)
    # Pre-tax total and the sales tax added on top of it, if any --
    # total_price is always subtotal + tax_amount. tax_label is only ever
    # set (and only ever shown on the proposal) for a tax rule that
    # itemizes tax back to the client -- see tax.ITEMIZES_TAX. Defaults
    # keep every existing caller's total_price behavior unchanged.
    subtotal: float = 0.0
    tax_amount: float = 0.0
    tax_label: str = ""

    # Payment breakdown (see proposal/build.py's build_proposal() and
    # app.py's _payment_breakdown() for the shared spec/math). All four
    # always sum exactly to total_price -- first_check_amount is computed
    # as the remainder of the other three, never independently.
    deductible_amount: float = 0.0
    first_check_amount: float = 0.0
    recoverable_depreciation_amount: float = 0.0
    supplements_amount: float = 0.0
