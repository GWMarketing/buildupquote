"""Trade classification for line items.

Guesses a trade category from a line item's description so the editing
screen starts with something reasonable already filled in -- the
contractor can always override it (this is a suggestion, not a parse
result, so it deliberately lives outside scope_parser/).
"""

TRADE_OPTIONS = [
    "Roofing",
    "Siding",
    "Gutters & Downspouts",
    "Fascia & Soffit",
    "Masonry",
    "Windows & Doors",
    "Painting",
    "Drywall",
    "Flooring",
    "Insulation",
    "Electrical",
    "Plumbing",
    "HVAC",
    "Fencing",
    "Tree/Debris Removal",
    "Demolition",
    "Cleaning",
    "Contents",
    "General Labor",
    "Other",
]

# Checked in order -- more specific trades first, so e.g. "Remove Laminated
# - comp. shingle rfg." matches Roofing (via "shingle") rather than being
# caught by the generic "remove" -> Demolition fallback further down.
_KEYWORD_RULES = [
    (("shingle", "roof", "ridge", "hip cap", "drip edge", "flashing", "valley",
      "underlayment", "starter course"), "Roofing"),
    (("siding",), "Siding"),
    (("gutter", "downspout"), "Gutters & Downspouts"),
    (("fascia", "soffit"), "Fascia & Soffit"),
    (("brick", "masonry", "chimney", "stucco", "mortar", "veneer"), "Masonry"),
    (("window", "door slab", " door ", "door*", "skylight"), "Windows & Doors"),
    (("paint", "primer", "pva", "stain & finish", "seal & paint", "seal the"), "Painting"),
    (("drywall",), "Drywall"),
    (("carpet", "flooring", "vinyl plank", "tile", "hardwood", "baseboard"), "Flooring"),
    (("insulation",), "Insulation"),
    (("electrical", "wiring", "panel", "outlet", "light fixture", "ceiling fan",
      "service panel", "meter socket"), "Electrical"),
    (("plumbing", "faucet", "water heater", "pipe -"), "Plumbing"),
    (("hvac", "furnace", "air cond", "duct", "vent -", "register"), "HVAC"),
    (("fence", "fencing"), "Fencing"),
    (("tree", "debris"), "Tree/Debris Removal"),
    (("clean",), "Cleaning"),
    (("contents", "move out then reset"), "Contents"),
    (("labor minimum", "general laborer", "per hour"), "General Labor"),
    (("remove", "tear off", "tear out", "demolition"), "Demolition"),
]


def guess_trade(description: str) -> str:
    if not description:
        return "Other"
    text = f" {description.lower()} "
    for keywords, trade in _KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            return trade
    return "Other"
