"""Work out which trailing columns are the unit price and the line total,
without knowing any of their names.

This is what lets the parser read an estimating program it has never been
taught about. The named-format path reads the printed header row and maps
column names; there is no such luxury here, so instead of READING the
columns we SOLVE for them.

The identity: for a row with a printed quantity, one column times the
quantity equals another column. Some formats print extra columns between
those two (tax, overhead and profit) that are added into the total, so the
general form is

    quantity x price  +  every numeric column between price and total  =  total

That is arithmetic, not a guess. A single row could satisfy it by
coincidence; the same two column POSITIONS satisfying it across every row
in a section cannot. When no pair wins, or two pairs tie, this returns
nothing at all and the caller flags the section for a human -- the same
anti-guessing rule the rest of this parser follows.

Known-honest failure modes, all of which end in "flag it" rather than
"pick one":
  * every quantity on the page is 1.00, so price and total are
    indistinguishable from each other
  * the printed total already includes something not shown as its own
    column, so nothing balances
  * rows priced at 0.00, which satisfy everything and are therefore
    excluded from the vote rather than allowed to decide it
"""
from dataclasses import dataclass, field
from typing import Optional

from .tokens import parse_number

# Two cents, or a tenth of a percent on large figures -- enough for
# rounding in the source document, far too tight to match by accident.
def _tolerance(value):
    return max(0.02, abs(value) * 0.001)


@dataclass
class ColumnSolution:
    price_index: int
    total_index: int
    # Numeric columns sitting between them that are added into the total
    # (tax, O&P and friends). Their presence is why a contractor's margin
    # must be applied to the price, never to the total.
    addend_indexes: tuple = ()
    rows_matched: int = 0
    rows_eligible: int = 0
    notes: list = field(default_factory=list)

    @property
    def match_ratio(self) -> float:
        return self.rows_matched / self.rows_eligible if self.rows_eligible else 0.0

    @property
    def total_includes_extras(self) -> bool:
        return bool(self.addend_indexes)


def numeric_tail(tokens):
    """Parse a cleaned tail into positional values, keeping None for
    anything that isn't a number (age/life, condition, a percentage) so
    column POSITIONS stay aligned across rows."""
    return [parse_number(t) for t in tokens]


def _row_matches(qty, values, i, j):
    vi, vj = values[i], values[j]
    if vi is None or vj is None or vi == 0 or vj == 0:
        return False, ()
    addends = tuple(k for k in range(i + 1, j) if values[k] is not None)
    total = qty * vi + sum(values[k] for k in addends)
    return abs(total - vj) <= _tolerance(vj), addends


def _is_informative(qty, values, i, j, addends):
    """Does this row actually PROVE anything about these two columns?

    A row with a quantity of 1 and nothing added in between satisfies
    "column i times quantity equals column j" for any two columns that
    happen to hold the same number -- and estimates are full of those
    (replacement cost equals actual cash value whenever depreciation is
    zero). Such a match is true but carries no information, so it is
    counted separately and never allowed to decide the answer.

    Found on a real fixture: on the appraiser/Williams1 estimate this was
    the difference between reading the unit price correctly and reading
    the replacement-cost column as if it were the unit price.
    """
    if qty != 1.0:
        return True
    return any(values[k] not in (None, 0) for k in addends)


def solve(rows, min_rows=2, min_ratio=0.6) -> Optional[ColumnSolution]:
    """`rows` is a list of (quantity, [values]) for one section.

    Returns the winning ColumnSolution, or None when the section is
    ambiguous or nothing balances.
    """
    eligible = [(q, v) for q, v in rows if q not in (None, 0) and sum(x is not None for x in v) >= 2]
    if len(eligible) < min_rows:
        return None

    width = max(len(v) for _, v in eligible)
    tally = {}
    for qty, values in eligible:
        padded = list(values) + [None] * (width - len(values))
        for i in range(width):
            for j in range(i + 1, width):
                ok, addends = _row_matches(qty, padded, i, j)
                if ok:
                    entry = tally.setdefault(
                        (i, j), {"count": 0, "informative": 0, "addends": set()}
                    )
                    entry["count"] += 1
                    if _is_informative(qty, padded, i, j, addends):
                        entry["informative"] += 1
                    entry["addends"].add(addends)

    if not tally:
        return None

    def rank(item):
        (i, j), data = item
        # Rows that actually PROVE the relationship come first; a pile of
        # information-free quantity-of-1 matches must never outrank them.
        # After that: most rows explained, then the simplest relationship
        # (fewest columns folded in, then closest together).
        spread = max((len(a) for a in data["addends"]), default=0)
        return (-data["informative"], -data["count"], spread, j - i)

    ordered = sorted(tally.items(), key=rank)
    (best_i, best_j), best = ordered[0]

    if best["count"] < max(min_rows, int(round(min_ratio * len(eligible)))):
        return None
    # A winner supported only by quantity-of-1 rows has not been proven at
    # all -- that is the ambiguous case, and ambiguity gets reported.
    if best["informative"] == 0:
        return None

    # A genuine tie between two different relationships is ambiguity, and
    # ambiguity is reported, never resolved by preference.
    if len(ordered) > 1:
        (_, runner) = ordered[1]
        if rank(ordered[1]) == rank(ordered[0]):
            return None

    addends = max(best["addends"], key=len) if best["addends"] else ()
    solution = ColumnSolution(
        price_index=best_i,
        total_index=best_j,
        addend_indexes=tuple(addends),
        rows_matched=best["count"],
        rows_eligible=len(eligible),
    )
    if solution.total_includes_extras:
        solution.notes.append(
            "the printed line total includes "
            f"{len(addends)} extra column(s) (tax, overhead and profit or similar); "
            "your margin is applied to the unit price, not to that total"
        )
    if solution.match_ratio < 1.0:
        solution.notes.append(
            f"{solution.rows_eligible - solution.rows_matched} row(s) in this section "
            "did not balance and are flagged for review"
        )
    return solution


def diagnose(rows) -> str:
    """A plain-language reason a section couldn't be solved, for the
    contractor to read. Never speculates beyond what the numbers show."""
    eligible = [(q, v) for q, v in rows if q not in (None, 0) and sum(x is not None for x in v) >= 2]
    if not rows:
        return "no priced rows were found in this section"
    if len(eligible) < 2:
        return (
            "this section has too few rows with both a quantity and figures beside them "
            "to check the arithmetic against"
        )
    quantities = {q for q, _ in eligible}
    if quantities == {1.0}:
        return (
            "every quantity in this section is 1, so the unit price and the line total "
            "are the same number and can't be told apart"
        )
    return (
        "no column in this section multiplies out to another one, so the printed line "
        "totals include something this reader can't see -- the figures need checking by eye"
    )
