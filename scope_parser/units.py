"""Unit-of-measure vocabularies.

There are deliberately TWO sets here, and they must never be merged.

`XACTIMATE_UNIT_TOKENS` is the exact sixteen-token list this parser has
always used. It is frozen: every regression fixture was validated against
it, and widening it is a silent, hard-to-see change to how rows are
anchored. (Proven the hard way -- widening this list was one of three
deliberate sabotages run against the golden-snapshot lock, and it was the
one that slipped through undetected, because the three fixtures happen not
to exercise it. A risk that no test can see is a risk that has to be
prevented structurally instead.)

`GENERIC_UNIT_TOKENS` is the wider vocabulary the generic reader uses for
estimating programs we haven't been taught about. It is a SUPERSET, but it
lives behind its own name so it can never leak into the Xactimate path.

`UNIT_TOKENS` stays as an alias of the Xactimate set purely so existing
imports keep meaning exactly what they meant before.
"""

XACTIMATE_UNIT_TOKENS = frozenset({
    "SQ", "EA", "LF", "SF", "SY", "HR", "DA", "RM", "CF", "CY",
    "GAL", "LS", "MO", "TN", "WK", "PR",
})

# Everything above, plus units seen in other estimating programs and in
# non-Xactimate carrier templates. Only ever used by the generic reader.
GENERIC_UNIT_TOKENS = XACTIMATE_UNIT_TOKENS | frozenset({
    "IN", "FT", "YD", "BF", "SQFT", "SQYD", "LNFT", "CT", "BX", "RL",
    "BG", "TON", "DAY", "WKS", "SET", "PC", "PCS", "UN", "ITEM",
})

# Backwards-compatible alias -- deliberately the Xactimate set.
UNIT_TOKENS = XACTIMATE_UNIT_TOKENS
