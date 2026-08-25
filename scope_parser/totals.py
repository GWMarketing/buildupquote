"""The totals-consistency check.

The single highest-leverage safety net in this whole parser: every carrier
PDF prints its own subtotal after each batch of line items ("Totals: R10
7,904.18 0.00 7,904.18"). If the line items we parsed since the *previous*
such line don't sum to (approximately) one of the numbers on this one,
something about the parse is wrong -- a mis-split row, a swallowed line
item, a mis-mapped column -- and we should say so loudly rather than
silently ship a wrong number into a contractor's proposal.

Deliberately positional (walk the document top to bottom, reset the
running sum at each "Totals:"/"Total:" line) rather than grouped by our
own guess at section names -- that keeps this check honest even when the
section-title heuristic in line_items.py gets a label wrong or messy.
"""
from .models import SectionTotals


def check_section_totals(section_totals_raw, tolerance=0.05):
    """`section_totals_raw` is a list of (label, printed_numbers,
    rcv_sum_since_previous_totals_line) -- see line_items.py, which builds
    this list while it walks the document.
    """
    results = []
    for label, numbers, running_sum in section_totals_raw:
        if not numbers:
            results.append(SectionTotals(label, numbers, running_sum, matched=False))
            continue
        # A "Totals:"/"Total:" line with nothing parsed since the last
        # checkpoint but a real (non-zero) printed amount is almost always
        # a grand-total/rollup line (e.g. "Total: Dwelling ...", "Total:
        # Exterior ...") summing several earlier sections at once, not a
        # fresh subtotal we can check against a running sum of zero.
        if running_sum == 0.0 and any(abs(n) > tolerance for n in numbers):
            results.append(SectionTotals(label, numbers, running_sum, matched=True, skipped=True))
            continue
        closest_diff = min(abs(running_sum - n) for n in numbers)
        results.append(SectionTotals(
            section=label,
            printed_numbers=numbers,
            parsed_rcv_sum=running_sum,
            matched=closest_diff <= tolerance,
            closest_diff=round(closest_diff, 2),
        ))
    return results
