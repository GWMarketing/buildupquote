"""The Texas building/electrical/mechanical/plumbing code checklist, plus
OSHA worksite-safety items -- source: the reference guide Glenn pasted in
on 2026-08-25, saved verbatim as a project doc
(`claude/texas-building-codes-reference.md`).

Deliberately lives OUTSIDE scope_parser/, same reason as trades.py: this
checks the CONTRACTOR's live, currently-edited scope table against a
reference list, not anything read off the PDF. scope_parser/ knows
nothing about the UI or the contractor's edits; this module only knows
about the UI's rows.

Matching is plain keyword-in-description, same "deterministic, no AI,
auditable" rule as every parser module. It is a HELPFUL HINT, not a
finding: a real code item can be satisfied by a description this
module's keyword list doesn't happen to catch, and plenty of these items
don't apply to every job at all (there's no garage on this claim, no
dryer work in scope, etc.). Nothing here is a claim that a code
requirement IS or ISN'T being followed on the actual job -- only whether
a matching line item is or isn't in THIS PROPOSAL yet. Same anti-guessing
rule as claim_flags.py: absence of a match means "no matching line found
here," never "confirmed missing from the repair."
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CodeItem:
    id: str
    citation: str
    category: str
    title: str
    requirement: str
    # Lower-cased substrings; a match on ANY one counts as "found in this
    # scope" for this item. Deliberately plain substrings, not regex --
    # keeps the list itself readable and auditable by a non-coder.
    keywords: tuple = field(default_factory=tuple)
    # Must be one of trades.TRADE_OPTIONS -- pinned by a test, since a
    # typo here would silently break the "Add to scope" button's Trade
    # dropdown.
    default_trade: str = "Other"


# Section 1 of the reference guide: which code a Texas city has adopted,
# and the windstorm-zone rule. Background, not a checklist item -- none
# of these map to a line item a contractor would add to a proposal, so
# they're kept separate and shown as plain reference text, not a
# checklist. (The deductible-waiver law, also originally in this section,
# is deliberately NOT repeated here -- it's already enforced elsewhere,
# see proposal/models.py's TX_DEDUCTIBLE_NOTICE.)
STATUTORY_CONTEXT = (
    {
        "citation": "Tex. Local Gov't Code § 214.212",
        "title": "Residential baseline",
        "detail": "Adopts the IRC as the baseline residential code for every Texas city. "
                   "Cities layer their own local amendments on top (e.g. NCTCOG standards "
                   "in North Texas).",
    },
    {
        "citation": "Tex. Local Gov't Code § 214.216",
        "title": "Commercial baseline",
        "detail": "Adopts the IBC for commercial, industrial, and multi-family structures.",
    },
    {
        "citation": "Tex. Local Gov't Code § 214.214 / TDLR",
        "title": "Electrical baseline",
        "detail": "Mandates statewide adoption of the NEC through the Texas Department of "
                   "Licensing and Regulation.",
    },
    {
        "citation": "Tex. Ins. Code Ch. 2210 / TDI Windstorm",
        "title": "Coastal windstorm zone",
        "detail": "Structural/high-wind requirements across the 14 first-tier coastal "
                   "counties and parts of Harris County -- WPI-8/WPI-8-E engineering and a "
                   "TDI inspection certificate. Only relevant if the property is in one of "
                   "those counties.",
    },
)


CODE_ITEMS = (
    # ---------------- Roofing, Flashings & Envelope ----------------
    CodeItem(
        id="irc_r905_2_8_5_drip_edge",
        citation="IRC R905.2.8.5",
        category="Roofing, Flashings & Envelope",
        title="Drip edge flashing",
        requirement="Mandatory at both eaves and rakes of asphalt shingle roofs -- at least "
                     "2\" overlap, nailed every 12\". Underlayment laps over drip edge at "
                     "eaves and under it at rakes.",
        keywords=("drip edge",),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r905_1_1_underlayment_laps",
        citation="IRC R905.1.1 & R905.2.7",
        category="Roofing, Flashings & Envelope",
        title="Underlayment end-lap offsets",
        requirement="End-laps must offset at least 6 feet, head-laps by 2\" -- the basis for "
                     "the standard 10-15% waste factor on felt/synthetic underlayment.",
        keywords=("underlayment",),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r905_2_2_low_slope",
        citation="IRC R905.2.2",
        category="Roofing, Flashings & Envelope",
        title="Low-slope double underlayment",
        requirement="Asphalt shingles prohibited below 2:12. Between 2:12 and 4:12, requires "
                     "double-layer underlayment (19\" starter lap, 36\" sheets).",
        keywords=("low slope", "double coverage", "2 layers", "two layers", "double underlayment"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r905_1_2_ice_barrier",
        citation="IRC R905.1.2 & Table R905.1.2",
        category="Roofing, Flashings & Envelope",
        title="Ice barrier membrane",
        requirement="Self-adhering polymer-modified bitumen membrane from the lowest eave "
                     "edge to >= 24\" inside the interior wall line, and in all closed "
                     "valleys, in freeze-prone jurisdictions.",
        keywords=("ice barrier", "ice & water", "ice and water", "ice shield", "ice guard"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r905_2_8_2_valley_step",
        citation="IRC R905.2.8.2 & R905.2.8.3",
        category="Roofing, Flashings & Envelope",
        title="Valley lining & sidewall step flashing",
        requirement="Full replacement of valley metal and sidewall step flashing during a "
                     "roof replacement -- reusing damaged or punctured metal is prohibited.",
        keywords=("valley metal", "valley lining", "step flashing", "valley flashing"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r905_2_8_4_penetration_flashing",
        citation="IRC R905.2.8.4",
        category="Roofing, Flashings & Envelope",
        title="Roof penetration flashings",
        requirement="Approved base and cap flashings around vent stacks, chimneys, and "
                     "electrical masts. Caulking over a rotted or rusted boot is not a fix.",
        keywords=("pipe jack", "vent pipe flashing", "boot", "penetration flashing"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r903_2_1_kickout",
        citation="IRC R903.2.1 & R903.2.2",
        category="Roofing, Flashings & Envelope",
        title="Kick-out diverter flashing",
        requirement="Required where an eave meets a vertical sidewall, to route runoff into "
                     "the gutter instead of behind the siding/stucco.",
        keywords=("kick-out", "kickout", "kick out", "diverter flashing"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r1003_20_cricket",
        citation="IRC R1003.20 / IBC 1503.6",
        category="Roofing, Flashings & Envelope",
        title="Chimney crickets",
        requirement="Mandatory on the ridge side of any chimney or penetration wider than "
                     "30 inches.",
        keywords=("cricket", "saddle"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r806_attic_ventilation",
        citation="IRC R806.1 & R806.2",
        category="Roofing, Flashings & Envelope",
        title="Attic net free ventilation",
        requirement="Balanced cross-ventilation -- >= 1 sq ft per 150 sq ft of attic floor "
                     "area (1/300 with a vapor barrier) -- to prevent shingle blistering and "
                     "condensation.",
        keywords=("ridge vent", "attic vent", "soffit vent", "turbine vent", "roof vent"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r908_bare_deck",
        citation="IRC R908.3.1.1 / IBC 1511.3",
        category="Roofing, Flashings & Envelope",
        title="Tear-off to bare deck",
        requirement="Existing roof covering must come off down to bare deck -- overlaying "
                     "new shingles on rotted or structurally inadequate decking is "
                     "prohibited.",
        keywords=("tear off", "tear-off"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="ibc_1511_5_no_reused_flashing",
        citation="IBC 1511.5",
        category="Roofing, Flashings & Envelope",
        title="No reinstalling damaged flashings",
        requirement="Damaged or deteriorated flashings, metal edgings, drain outlets, and "
                     "counterflashings may not be reinstalled -- they get replaced.",
        keywords=("new flashing", "replace flashing", "reflash"),
        default_trade="Roofing",
    ),
    CodeItem(
        id="irc_r703_wrb",
        citation="IRC R703.2 & R703.4",
        category="Roofing, Flashings & Envelope",
        title="Water-resistive barrier & opening flashing",
        requirement="Continuous WRB/house wrap (2\" horizontal, 6\" vertical laps), plus "
                     "approved head flashings above exterior windows and doors.",
        keywords=("house wrap", "weather resistant barrier", "wrb", "window flashing",
                   "door flashing", "head flashing"),
        default_trade="Siding",
    ),

    # ---------------- Framing, Drywall & Fire Separation ----------------
    CodeItem(
        id="irc_r302_5_1_garage_door",
        citation="IRC R302.5.1",
        category="Framing, Drywall & Fire Separation",
        title="Garage-to-house door protection",
        requirement="A door between an attached garage and the house needs to be solid "
                     "wood/steel >= 1-3/8\", or a 20-minute fire-rated door with a "
                     "self-closing hinge.",
        keywords=("fire door", "fire-rated door", "self-closing", "self closing"),
        default_trade="Windows & Doors",
    ),
    CodeItem(
        id="irc_r302_6_garage_separation",
        citation="IRC R302.6",
        category="Framing, Drywall & Fire Separation",
        title="Garage fire separation",
        requirement=">= 1/2\" gypsum on the garage side of common walls; >= 5/8\" Type X "
                     "gypsum on ceilings under habitable space.",
        keywords=("type x", "5/8 drywall", "fire separation", "garage ceiling"),
        default_trade="Drywall",
    ),
    CodeItem(
        id="irc_r302_11_fireblocking",
        citation="IRC R302.11 / M1801.9",
        category="Framing, Drywall & Fire Separation",
        title="Fireblocking penetrations",
        requirement="Non-combustible fireblocking foam/caulk required around mechanical "
                     "vents, flues, wiring, and plumbing passing through stud bays and "
                     "ceiling plates.",
        keywords=("fireblock", "fire block", "firestop", "fire caulk"),
        default_trade="Other",
    ),
    CodeItem(
        id="irc_r503_subfloor",
        citation="IRC R503.2.2 & R503.2.3",
        category="Framing, Drywall & Fire Separation",
        title="Subfloor sheathing & gapping",
        requirement="Tongue-and-groove or blocked panels with 1/8\" edge expansion gaps, to "
                     "prevent buckling and floor failure.",
        keywords=("subfloor", "sub-floor", "osb", "sheathing"),
        default_trade="Flooring",
    ),
    CodeItem(
        id="irc_r317_1_treated_lumber",
        citation="IRC R317.1",
        category="Framing, Drywall & Fire Separation",
        title="Treated lumber at concrete contact",
        requirement="Framing, sills, and sleepers touching a concrete slab or exterior "
                     "foundation masonry must be pressure-preservative treated wood.",
        keywords=("treated lumber", "pressure treated", "pt lumber", "sill plate"),
        default_trade="Other",
    ),
    CodeItem(
        id="irc_r310_egress",
        citation="IRC R310.1 & R310.2",
        category="Framing, Drywall & Fire Separation",
        title="Emergency egress openings",
        requirement="Sleeping rooms need an egress window: net clear opening >= 5.7 sq ft "
                     "(5.0 sq ft at grade floor), clear height >= 24\", clear width >= 20\", "
                     "sill height <= 44\" from the floor.",
        keywords=("egress window", "egress"),
        default_trade="Windows & Doors",
    ),
    CodeItem(
        id="irc_r702_drywall_fastening",
        citation="IRC R702.3.5 / ASTM C840",
        category="Framing, Drywall & Fire Separation",
        title="Drywall fastening standards",
        requirement="Screw/nail spacing on framing -- drywall may not bridge over "
                     "compromised, warped, or water-damaged substrate without re-blocking "
                     "the framing first.",
        keywords=("drywall", "sheetrock", "gypsum board"),
        default_trade="Drywall",
    ),

    # ---------------- Electrical, Mechanical, Plumbing & Energy ----------------
    CodeItem(
        id="nec_210_8_gfci",
        citation="NEC 210.8(A)",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="GFCI protection",
        requirement="Required on 125V-250V receptacles in kitchens, bathrooms, laundry "
                     "rooms, crawlspaces, unfinished basements, and outdoors.",
        keywords=("gfci", "ground fault"),
        default_trade="Electrical",
    ),
    CodeItem(
        id="nec_210_12_afci",
        citation="NEC 210.12",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="AFCI protection",
        requirement="Required on branch circuits supplying bedrooms, living rooms, dining "
                     "rooms, hallways, and closets.",
        keywords=("afci", "arc fault"),
        default_trade="Electrical",
    ),
    CodeItem(
        id="irc_m1502_dryer_duct",
        citation="IRC M1502 / IMC 504",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="Dryer exhaust ducts",
        requirement="Rigid metal (>= 0.0157\" thick, 4\" diameter), smooth interior, vents "
                     "directly outside, <= 35 ft (with offsets for turns).",
        keywords=("dryer vent", "dryer duct", "dryer exhaust"),
        default_trade="HVAC",
    ),
    CodeItem(
        id="irc_p3103_plumbing_vent",
        citation="IRC P3103.1 / IPC 903.1",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="Plumbing vent roof extension",
        requirement="Plumbing vent pipes through the roof must extend >= 6\" above the "
                     "roofline and seal watertight with an approved flashing collar.",
        keywords=("plumbing vent", "vent stack", "roof jack"),
        default_trade="Plumbing",
    ),
    CodeItem(
        id="irc_p2906_fixture_shutoffs",
        citation="IRC P2906.5 / IPC 605.4",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="Fixture water shutoffs",
        requirement="Dedicated stop valves (angle stops) required at every water connection "
                     "-- sinks, toilets, ice makers.",
        keywords=("shutoff valve", "shut-off valve", "angle stop", "stop valve"),
        default_trade="Plumbing",
    ),
    CodeItem(
        id="irc_m1411_hvac_drain_pan",
        citation="IRC M1411.3 / IMC 307",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="HVAC secondary drain pan / float switch",
        requirement="Auxiliary drain pan with an independent drain line, or an electric "
                     "water-level float shut-off switch, required for every attic/ceiling "
                     "air handler.",
        keywords=("drain pan", "float switch", "condensate"),
        default_trade="HVAC",
    ),
    CodeItem(
        id="iecc_r402_thermal_envelope",
        citation="IECC R402.1 / R402.2",
        category="Electrical, Mechanical, Plumbing & Energy",
        title="Thermal envelope R-values",
        requirement="Minimum insulation by Texas climate zone (Zones 2/3: attic R-38 to "
                     "R-49; walls R-13 to R-20) whenever insulation is removed or disturbed.",
        keywords=("attic insulation", "blown insulation", "batt insulation", "r-38", "r-49",
                   "r-13", "r-20"),
        default_trade="Insulation",
    ),

    # ---------------- Workplace Safety (OSHA 29 CFR) ----------------
    # Per Glenn (2026-08-25): included with "Add to scope" like every
    # other category, since fall-protection/scaffolding setup is
    # sometimes its own billed line on a steep-slope job -- not always
    # just an overhead cost the contractor absorbs.
    CodeItem(
        id="osha_1926_501_fall_protection",
        citation="29 CFR 1926.501(b)(13) / 1926.502",
        category="Workplace Safety (OSHA)",
        title="Fall protection",
        requirement="Guardrails, safety nets, or a Personal Fall Arrest System (rated "
                     "5,000 lbs) required for anyone working >= 6 ft above a lower level.",
        keywords=("fall protection", "harness", "pfas", "guardrail", "safety net"),
        default_trade="Other",
    ),
    CodeItem(
        id="osha_1926_451_scaffolding",
        citation="29 CFR 1926.451",
        category="Workplace Safety (OSHA)",
        title="Scaffolding safety standards",
        requirement="Base plates, guardrails, and plank overlaps for pump jacks, staging, "
                     "or modular scaffolding used on siding or exterior wall repairs.",
        keywords=("scaffold", "pump jack", "staging"),
        default_trade="Other",
    ),
    CodeItem(
        id="osha_1910_1200_ppe",
        citation="29 CFR 1910.1200 / 1926.59",
        category="Workplace Safety (OSHA)",
        title="Hazard communication & PPE",
        requirement="Safety documentation, job-site hazard signage, and required PPE for "
                     "demolition, mold remediation, and flood cleanup.",
        keywords=("ppe", "hazmat", "hazard communication", "respirator", "containment"),
        default_trade="Other",
    ),
)

# The order categories should be shown in -- matches Glenn's own source
# document, sections 2 through 5.
CATEGORY_ORDER = (
    "Roofing, Flashings & Envelope",
    "Framing, Drywall & Fire Separation",
    "Electrical, Mechanical, Plumbing & Energy",
    "Workplace Safety (OSHA)",
)


def check_coverage(descriptions, items=CODE_ITEMS) -> dict:
    """For each item, True if ANY of its keywords appears in ANY of the
    given descriptions (case-insensitive substring match). `descriptions`
    is every line's Description in the contractor's current scope table
    -- carrier lines and added lines alike, regardless of whether the row
    is currently checked "Include".

    Deterministic and auditable, same rule as everywhere else in this
    project: no AI, no fuzzy scoring, just "is this substring present."
    A miss does not mean the requirement isn't met on the actual job --
    only that nothing in this proposal's wording matches yet."""
    haystack = " | ".join(str(d or "").lower() for d in descriptions)
    return {
        item.id: any(kw in haystack for kw in item.keywords)
        for item in items
    }


# The "Section" value every code-required row gets, so it can be told
# apart from a carrier section AND from a line the contractor chose to
# add on their own (Glenn, 2026-08-25: "it shouldn't be added into the
# scope but additions that the contractor has to add by law"). Still
# flows through the SAME pricing/totals/export machinery as every other
# row -- it's the label, not a different code path, that keeps it out of
# the Scope tab and the "Added by you" list. See app.py's
# _added_mask()/Scope tab filtering and the Code Additions tab.
SECTION_LABEL = "Code-required addition"


def material_description(item) -> str:
    """The description for a code item's own base line -- just its
    citation and title, so it reads the same way on the Scope table, the
    CSV, and the proposal PDF as any other line item's description."""
    return f"{item.citation} — {item.title}"


def labor_line(item, workers, hours, rate):
    """One crew/hours/rate combo, turned into the numbers one priced
    line item needs. There's no separate "workers" column anywhere in
    this app's row shape, so the crew size is folded into Quantity as
    total crew-hours (workers x hours) with Unit Cost as the hourly rate
    -- Qty x Unit Cost is still exactly the labor cost, and the
    human-readable breakdown (workers, hours, rate) is kept in the
    description so nothing about "8 workers x 30 hrs" is lost by folding
    it into one number.

    Returns (description, total_hours) -- total_hours is None (and the
    caller should skip this line entirely) if workers, hours, or rate
    isn't a usable positive number, since a $0 or blank labor line is
    not something to silently add to a bill.
    """
    try:
        workers = float(workers)
        hours = float(hours)
        rate = float(rate)
    except (TypeError, ValueError):
        return None, None
    if workers <= 0 or hours <= 0 or rate <= 0:
        return None, None
    total_hours = round(workers * hours, 2)
    worker_word = "worker" if workers == 1 else "workers"
    description = (
        f"{item.citation} — Labor ({workers:g} {worker_word} x {hours:g} hrs "
        f"@ ${rate:,.2f}/hr)"
    )
    return description, total_hours
