"""How much should a contractor trust this parse?

Three states, always exactly one of them, never a silent parse. The words
here are the words that appear on screen, so they are written for someone
about to send a proposal to a homeowner -- not for a developer reading a
log.

The evidence is the document's own arithmetic. Every carrier estimate
prints its own subtotals; if what we parsed adds up to what the document
says it should, the parse is trustworthy no matter which program wrote it.
That check (totals.py) is completely format-blind, which is what makes a
confidence score meaningful for a format nobody has ever taught us.
"""
from dataclasses import dataclass

RECOGNISED = "recognised"
GENERIC_OK = "generic_ok"
LOW = "low"
NOT_A_SCOPE = "not_a_scope"


@dataclass
class ParseConfidence:
    state: str = LOW
    headline: str = ""
    detail: str = ""
    items_total: int = 0
    items_flagged: int = 0
    sections_checked: int = 0
    sections_reconciled: int = 0

    @property
    def all_reconciled(self) -> bool:
        return self.sections_checked > 0 and self.sections_reconciled == self.sections_checked

    @property
    def needs_attention(self) -> bool:
        return self.state in (LOW, NOT_A_SCOPE)


def _totals_phrase(checked, reconciled):
    if checked == 0:
        return "this document prints no subtotals we could check the figures against"
    if reconciled == checked:
        return "every subtotal printed on the document reconciles"
    return f"{reconciled} of {checked} printed subtotals reconcile"


def assess(line_items, section_totals, fingerprint, document_type) -> ParseConfidence:
    total = len(line_items)
    flagged = sum(1 for i in line_items if i.needs_review)
    checkable = [s for s in section_totals if not s.skipped]
    checked = len(checkable)
    reconciled = sum(1 for s in checkable if s.matched)

    conf = ParseConfidence(
        items_total=total,
        items_flagged=flagged,
        sections_checked=checked,
        sections_reconciled=reconciled,
    )

    # A document that was never a scope isn't a failed parse.
    if not document_type.line_items_expected:
        conf.state = NOT_A_SCOPE
        conf.headline = document_type.label
        conf.detail = document_type.advice
        return conf

    if total == 0:
        conf.state = LOW
        conf.headline = "We couldn't find any priced work in this file"
        conf.detail = (
            "Nothing in it looks like a scope line -- a quantity, a unit and a price. "
            "If it is an estimate, send it over and we'll add support for its layout."
        )
        return conf

    parsed_phrase = (
        f"all {total} line items came through"
        if flagged == 0
        else f"{total - flagged} of {total} line items came through cleanly"
    )
    totals_phrase = _totals_phrase(checked, reconciled)
    good_totals = checked > 0 and reconciled == checked
    mostly_good = checked > 0 and reconciled >= max(1, int(round(0.8 * checked)))
    few_flags = total > 0 and flagged <= max(2, int(round(0.1 * total)))

    if fingerprint.is_recognised and good_totals and flagged == 0 and not fingerprint.disagreement:
        conf.state = RECOGNISED
        conf.headline = f"Read with our {fingerprint.identified_as} rules"
        conf.detail = f"{parsed_phrase.capitalize()}, and {totals_phrase}."
        return conf

    if (good_totals or mostly_good) and few_flags:
        conf.state = GENERIC_OK
        if fingerprint.is_recognised:
            conf.headline = f"Read with our {fingerprint.identified_as} rules"
        elif fingerprint.is_identified:
            # We know what wrote it; we just don't have rules for it yet
            # that have been checked against a real document. Saying so is
            # more useful, and more honest, than "unrecognised".
            conf.headline = f"This looks like {fingerprint.identified_as}"
            conf.detail = (
                "We don't have checked rules for that program yet, so we read it with "
                "our general reader. "
            )
        else:
            conf.headline = "We don't recognise the program that made this file"
            conf.detail = "We read it with our general reader. "
        conf.detail += f"{parsed_phrase.capitalize()}, and {totals_phrase}."
        if flagged:
            conf.detail += (
                f" Check the {flagged} flagged row{'s' if flagged != 1 else ''} before you "
                "send this."
            )
        return conf

    conf.state = LOW
    conf.headline = "This one needs your eyes"
    conf.detail = (
        f"{parsed_phrase.capitalize()}, and {totals_phrase}. Treat everything below as a "
        "starting point rather than a checked scope -- go through it line by line."
    )
    # Two different problems wear the same face here, and the advice is
    # not the same. A layout we know means the numbers disagree with the
    # document's own subtotals; a layout we don't means we may simply be
    # reading the columns wrong.
    if fingerprint.is_recognised:
        conf.detail += (
            " The layout is one we know, so this is the document's own arithmetic not "
            "matching: a printed subtotal disagrees with the items above it."
        )
    else:
        conf.detail += " Send us the file and we'll add proper support for this format."
    return conf
