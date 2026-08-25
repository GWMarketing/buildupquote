"""Which program wrote this PDF, and how sure are we?

Glenn's original plan was to read the PDF's file metadata and route on it.
That is the right instinct and the metadata IS the strongest single signal
-- Xactimate stamps its exact name and version into the file. But it can't
be the only vote, for one concrete reason: a real Liberty Mutual estimate
priced off Cotality carries `Producer: Microsoft: Print To PDF`. The
estimating program's name never reached the file at all. Any document that
has been printed and re-saved, pulled from a claims portal, merged with a
photo report, or scanned loses that stamp -- while the printed page keeps
every clue it ever had.

So this scores several signals together, metadata heaviest, and always
records WHICH signals fired. A routing decision nobody can explain is the
same failure as a parsed number nobody can trace.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from . import profiles
from .tokens import find_qty_and_unit, split_fused_tokens

# Weight for a program name found in the PDF's own Creator/Producer field.
# Deliberately larger than everything else combined can reach by accident.
_W_CREATOR = 50
_W_PAGE_MARKER = 25
_W_HEADER = 20
_W_SUBTOTAL = 10
_W_ITEM_NUMBERING = 8

# Below this, no named sheet is trusted and the generic reader takes over.
_SELECT_THRESHOLD = 20
# Page evidence has to beat the metadata's answer by this much before we
# call it a real disagreement rather than noise.
_DISAGREEMENT_MARGIN = 20

_PRICE_LIST_RE = re.compile(r"Price List:\s*([A-Z]{2})([A-Z0-9]{3,})_([A-Z]{3}\d{2})")

# Symbility/Cotality names its pricing region in words instead of a code:
# "Cotality Data Driven USDC - February 2026 (Texas)". Same information,
# and it matters for the same reason -- this app's tax rules and its
# deductible notice are Texas law, so knowing the state is not cosmetic.
_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_PARENTHESISED_STATE_RE = re.compile(r"\(([A-Za-z][A-Za-z ]{3,24})\)")


@dataclass
class FormatFingerprint:
    """What we concluded, and every reason we concluded it."""

    profile_key: str = profiles.GENERIC.key   # the sheet used to PARSE
    identified_as: str = "Unrecognised format"  # what we think wrote it
    program_name: Optional[str] = None          # e.g. "Xactimate 24.6.1000.2"
    score: int = 0
    confidence: str = "low"                     # high | medium | low
    signals: list = field(default_factory=list)
    # True when the file metadata and the printed page point at different
    # programs. We keep the metadata's answer and lower confidence rather
    # than quietly picking a winner.
    disagreement: bool = False
    # Parsed off the Xactimate price-list index, e.g. "TX" from
    # "TXHO8X_AUG24". Drives which state's tax rules and contract notices
    # apply -- see the note in claim math about assuming Texas.
    jurisdiction_state: Optional[str] = None
    price_list_code: Optional[str] = None

    @property
    def is_recognised(self) -> bool:
        """True when a VERIFIED rule sheet was used to read this file."""
        return self.profile_key != profiles.GENERIC.key

    @property
    def is_identified(self) -> bool:
        """True when we know which program wrote this file, even if we
        don't yet have a rule sheet trusted to read it. Naming the program
        is useful to a contractor either way -- "this is a Symbility
        estimate" is a better answer than "unrecognised"."""
        return self.identified_as != "Unrecognised format"

    @property
    def profile(self):
        return profiles.get(self.profile_key)


def _candidates():
    """Every sheet worth scoring -- including identify-only sheets, which
    can name a document's program without being trusted to parse it."""
    out = dict(profiles.REGISTRY)
    out.pop(profiles.GENERIC.key, None)
    out.update(profiles.IDENTIFY_ONLY)
    return out


def _score_creator(profile, creator_text):
    if not creator_text:
        return 0, []
    low = creator_text.lower()
    for marker in profile.creator_markers:
        if marker in low:
            return _W_CREATOR, [f'file metadata names "{creator_text.strip()}"']
    return 0, []


def _score_page(profile, text, upper, lines):
    score = 0
    signals = []

    for marker in profile.page_markers:
        if marker in upper.lower():
            score += _W_PAGE_MARKER
            signals.append(f'the page mentions "{marker}"')
            break

    hits = [t for t in profile.signature_header_tokens if t in upper]
    if len(hits) >= 2:
        score += _W_HEADER
        signals.append("column headers read " + ", ".join(hits[:4]))

    if profile.subtotal_signature is not None:
        for line in lines:
            if profile.subtotal_signature.search(line.strip()):
                score += _W_SUBTOTAL
                signals.append("subtotal lines are phrased this format's way")
                break

    if profile.item_number_re is not None:
        numbered = sum(1 for line in lines if profile.item_number_re.match(line.strip()))
        if numbered >= 3:
            score += _W_ITEM_NUMBERING
            signals.append(f"{numbered} rows use this format's item numbering")

    for name, pattern, weight in profile.extra_signals:
        if pattern.search(text):
            score += weight
            signals.append(f"found this format's {name}")

    return score, signals


def _jurisdiction(text):
    m = _PRICE_LIST_RE.search(text)
    if m:
        state, middle, period = m.groups()
        return state, f"{state}{middle}_{period}"
    # Only consulted on documents that actually name a pricing database,
    # so a stray "(Texas)" in an address or a note can't set jurisdiction.
    if re.search(r"Pricing Database", text, re.IGNORECASE):
        for candidate in _PARENTHESISED_STATE_RE.findall(text):
            abbreviation = _STATE_NAMES.get(candidate.strip().lower())
            if abbreviation:
                return abbreviation, None
    return None, None


def fingerprint(lines, pdf_info=None) -> FormatFingerprint:
    """`lines` is the extracted page text; `pdf_info` is the PDF's own
    /Info dictionary when there was a real file to read one from (a
    text-only parse simply has none, which is handled, not guessed at)."""
    text = "\n".join(lines)
    upper = text.upper()
    creator = ""
    if pdf_info:
        creator = " ".join(
            str(pdf_info.get(k) or "") for k in ("Creator", "Producer", "Title")
        )

    results = {}
    for key, profile in _candidates().items():
        c_score, c_sig = _score_creator(profile, creator)
        p_score, p_sig = _score_page(profile, text, upper, lines)
        results[key] = {
            "profile": profile,
            "creator": c_score,
            "page": p_score,
            "total": c_score + p_score,
            "signals": c_sig + p_sig,
        }

    state, price_list = _jurisdiction(text)

    if not results:
        return FormatFingerprint(jurisdiction_state=state, price_list_code=price_list)

    best_key = max(results, key=lambda k: results[k]["total"])
    best = results[best_key]

    fp = FormatFingerprint(
        score=best["total"],
        signals=list(best["signals"]),
        jurisdiction_state=state,
        price_list_code=price_list,
    )

    if best["total"] < _SELECT_THRESHOLD:
        fp.signals.append("nothing on this document identifies the program that made it")
        return fp

    fp.identified_as = best["profile"].label
    from .metadata import parse_creator

    if best["creator"] and pdf_info:
        name, version = parse_creator(str(pdf_info.get("Creator") or ""))
        if name:
            fp.program_name = f"{name} {version}".strip() if version else name

    # A sheet we can identify but haven't validated against a real fixture
    # names the program WITHOUT being trusted to parse it. The generic
    # reader handles the document; the contractor still gets told what it
    # looks like.
    if best_key in profiles.IDENTIFY_ONLY:
        fp.profile_key = profiles.GENERIC.key
        fp.confidence = "medium"
        fp.signals.append(
            f"{best['profile'].label} is recognised but has no verified rule sheet yet, "
            "so this was read with the general reader"
        )
        return fp

    fp.profile_key = best_key

    # Does the printed page argue for someone else?
    if best["creator"]:
        rival = max(
            (r for k, r in results.items() if k != best_key),
            key=lambda r: r["page"],
            default=None,
        )
        if rival and rival["page"] - best["page"] >= _DISAGREEMENT_MARGIN:
            fp.disagreement = True
            fp.confidence = "low"
            fp.signals.append(
                "the file's metadata and the printed page disagree about which program "
                "wrote this -- going with the metadata, but check the result"
            )
            return fp

    fp.confidence = "high" if best["creator"] else "medium"
    return fp


def looks_like_line_item_document(lines, unit_tokens=None) -> bool:
    """Does this document contain priced scope rows at all?

    Used to tell a real estimate apart from a settlement statement, which
    carries only summary figures by design. See doc_type.py.
    """
    units = unit_tokens or profiles.GENERIC.unit_tokens
    anchors = 0
    for line in lines:
        toks = split_fused_tokens(line.split(), units)
        if find_qty_and_unit(toks, units) is not None:
            anchors += 1
            if anchors >= 3:
                return True
    return False
