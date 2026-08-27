"""Idempotent seed data: global parametric assemblies + trade lexicon.

Run automatically at app startup (see fastapi_app.py's lifespan) and
standalone with:  python -m app.seeds.assemblies_seed
"""
from sqlalchemy.orm import Session

from app import models


def seed_assemblies_and_lexicon(db: Session) -> None:
    """Insert any missing lexicon terms and global assemblies. Safe to run
    on every startup -- existing rows are left untouched."""
    _seed_lexicon(db)
    _seed_assemblies(db)
    db.commit()


# ---------------------------------------------------------------------------
# Trade lexicon
# ---------------------------------------------------------------------------

_LEXICON_ROWS = [
    # (trade, term, aliases, default_unit)
    ("drywall", "drywall", ["sheetrock", "plasterboard", "gyprock", "wallboard", "12.5mm boards"], "m2"),
    ("drywall", "drywall screw", ["drywall screws", "bugle head screw", "screws for drywall"], "each"),
    ("carpentry", "wall stud", ["stud", "timber stud", "vertical stud", "2x4 stud"], "each"),
    ("carpentry", "timber plate", ["top plate", "bottom plate", "sole plate", "wall plate"], "m"),
    ("carpentry", "decking board", ["deck board", "treated timber board", "timber decking"], "m2"),
    ("carpentry", "joist", ["floor joist", "timber joist", "support joist"], "each"),
    ("carpentry", "support post", ["post", "timber post", "deck post"], "each"),
    ("general", "decking screw", ["deck screws", "timber screws", "coated screws"], "each"),
    ("general", "tile", ["floor tile", "ceramic tile", "porcelain tile"], "m2"),
    ("general", "tile adhesive", ["adhesive", "thin-set", "tile cement"], "kg"),
    ("general", "grout", ["tile grout", "joint filler"], "kg"),
    ("general", "primer", ["floor primer", "sealer", "tanking primer"], "l"),
    ("general", "labor", ["labour", "installation", "fitting hours", "install"], "hr"),
    ("tiling", "tile", ["floor tile", "ceramic tile", "porcelain tile", "tiles"], "m2"),
    ("tiling", "tile adhesive", ["adhesive", "thin-set", "tile cement"], "kg"),
    ("tiling", "grout", ["tile grout", "joint filler"], "kg"),
    ("plumbing", "radiator", ["radiator", "radiators", "panel radiator"], "each"),
    ("electrical", "wiring", ["wire", "cable", "rewire", "electrical"], "m"),
    ("general", "sub-base", ["sub base", "subbase", "sub-floor"], "m2"),
]


def _seed_lexicon(db: Session) -> None:
    for trade, term, aliases, unit in _LEXICON_ROWS:
        exists = db.query(models.TradeLexicon).filter_by(trade=trade, term=term).first()
        if exists is None:
            db.add(models.TradeLexicon(trade=trade, term=term, aliases=aliases, default_unit=unit))


# ---------------------------------------------------------------------------
# Parametric assemblies (global templates, organization_id = None)
# ---------------------------------------------------------------------------

_ASSEMBLIES = [
    {
        "code": "WALL_STUD_PARTITION",
        "name": "Stud Partition Wall",
        "category": "Framing",
        "description": (
            "Timber stud partition with 12.5mm drywall both faces, screws, "
            "and framing/boarding labour."
        ),
        "required_inputs": ["length", "height"],
        "components": [
            # (description, item_type, unit, formula, unit_cost, markup)
            ("Wall studs @ 400mm centres", "material", "each", "(length / 0.4) + 1", 4.50, 20.00),
            ("Top and bottom plates", "material", "m", "length * 2", 3.20, 20.00),
            ("Drywall boards (12.5mm) both faces +10% waste", "material", "m2", "length * height * 1.1", 8.90, 20.00),
            ("Drywall screws", "material", "each", "length * height * 8", 0.05, 20.00),
            ("Framing and boarding labour", "labor", "hr", "length * height * 0.75", 45.00, 20.00),
        ],
    },
    {
        "code": "TIMBER_DECKING_TREATED",
        "name": "Treated Timber Decking",
        "category": "Exterior",
        "description": (
            "Treated pine deck on joists and posts, decking boards +10% "
            "waste, screws, and installation labour."
        ),
        "required_inputs": ["length", "width"],
        "components": [
            ("Treated timber joists @ 400mm centres", "material", "each", "(length / 0.4) + 1", 12.50, 20.00),
            ("Support posts", "material", "each", "((length / 2) + 1) * 2", 18.00, 20.00),
            ("Decking boards (treated) +10% waste", "material", "m2", "length * width * 1.1", 38.00, 20.00),
            ("Decking screws", "material", "each", "length * width * 12", 0.12, 20.00),
            ("Deck installation labour", "labor", "hr", "length * width * 0.6", 55.00, 20.00),
        ],
    },
    {
        "code": "TILE_BATHROOM_FLOOR",
        "name": "Bathroom Floor Tiling",
        "category": "Tiling",
        "description": (
            "Bathroom floor tile with 15% cuts allowance, adhesive, grout, "
            "primer, and tiling labour."
        ),
        "required_inputs": ["length", "width"],
        "components": [
            ("Floor tile +15% cuts allowance", "material", "m2", "length * width * 1.15", 42.00, 20.00),
            ("Tile adhesive", "material", "kg", "length * width * 4", 3.50, 20.00),
            ("Grout", "material", "kg", "length * width * 0.5", 6.00, 20.00),
            ("Floor primer", "material", "l", "length * width * 0.1", 18.00, 20.00),
            ("Tiling labour", "labor", "hr", "length * width * 1.5", 50.00, 20.00),
        ],
    },
]


def _seed_assemblies(db: Session) -> None:
    for spec in _ASSEMBLIES:
        exists = db.query(models.ParametricAssembly).filter_by(code=spec["code"]).first()
        if exists is not None:
            continue
        assembly = models.ParametricAssembly(
            organization_id=None,
            code=spec["code"],
            name=spec["name"],
            category=spec["category"],
            description=spec["description"],
            required_inputs=spec["required_inputs"],
        )
        db.add(assembly)
        db.flush()  # assembly.id
        for description, item_type, unit, formula, cost, markup in spec["components"]:
            db.add(models.AssemblyComponent(
                assembly_id=assembly.id,
                description=description,
                item_type=item_type,
                unit=unit,
                formula=formula,
                default_unit_cost=cost,
                default_markup_percent=markup,
            ))


if __name__ == "__main__":
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        seed_assemblies_and_lexicon(db)
        print("Seeded trade lexicon + parametric assemblies.")
    finally:
        db.close()
