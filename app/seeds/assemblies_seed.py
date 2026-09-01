"""Idempotent seed data: global parametric assemblies + trade lexicon.

Run automatically at app startup (see fastapi_app.py's lifespan) and
standalone with:  python -m app.seeds.assemblies_seed
"""
from sqlalchemy.orm import Session

from app import models
from app.seeds._upsert import ensure_seed_uniques, upsert_row


def seed_assemblies_and_lexicon(db: Session) -> None:
    """Insert any missing lexicon terms and global assemblies. Safe to run
    on every startup -- existing rows converge in place (atomic upserts, so
    concurrent gunicorn workers can't double-seed)."""
    ensure_seed_uniques(db)
    _seed_lexicon(db)
    _seed_assemblies(db)
    db.commit()


# ---------------------------------------------------------------------------
# Trade lexicon
# ---------------------------------------------------------------------------

_LEXICON_ROWS = [
    # (trade, term, aliases, default_unit)
    ("drywall", "drywall", ["sheetrock", "plasterboard", "gyprock", "wallboard", "1/2\" boards"], "sq ft"),
    ("drywall", "drywall screw", ["drywall screws", "bugle head screw", "screws for drywall"], "each"),
    ("carpentry", "wall stud", ["stud", "timber stud", "vertical stud", "2x4 stud"], "each"),
    ("carpentry", "timber plate", ["top plate", "bottom plate", "sole plate", "wall plate"], "ft"),
    ("carpentry", "decking board", ["deck board", "treated timber board", "timber decking"], "sq ft"),
    ("carpentry", "joist", ["floor joist", "timber joist", "support joist"], "each"),
    ("carpentry", "support post", ["post", "timber post", "deck post"], "each"),
    ("general", "decking screw", ["deck screws", "timber screws", "coated screws"], "each"),
    ("general", "tile", ["floor tile", "ceramic tile", "porcelain tile"], "sq ft"),
    ("general", "tile adhesive", ["adhesive", "thin-set", "tile cement"], "lb"),
    ("general", "grout", ["tile grout", "joint filler"], "lb"),
    ("general", "primer", ["floor primer", "sealer", "tanking primer"], "gal"),
    ("general", "labor", ["labour", "installation", "fitting hours", "install"], "hr"),
    ("tiling", "tile", ["floor tile", "ceramic tile", "porcelain tile", "tiles"], "sq ft"),
    ("tiling", "tile adhesive", ["adhesive", "thin-set", "tile cement"], "lb"),
    ("tiling", "grout", ["tile grout", "joint filler"], "lb"),
    ("plumbing", "radiator", ["radiator", "radiators", "panel radiator"], "each"),
    ("electrical", "wiring", ["wire", "cable", "rewire", "electrical"], "ft"),
    ("general", "sub-base", ["sub base", "subbase", "sub-floor"], "sq ft"),
]


def _seed_lexicon(db: Session) -> None:
    for trade, term, aliases, unit in _LEXICON_ROWS:
        _, _ = upsert_row(
            db,
            models.TradeLexicon,
            {"trade": trade, "term": term, "aliases": aliases,
             "default_unit": unit},
            conflict_cols=["trade", "term"],
            update_cols=["default_unit", "aliases"],
        )


# ---------------------------------------------------------------------------
# Parametric assemblies (global templates, organization_id = None)
# ---------------------------------------------------------------------------

_ASSEMBLIES = [
    {
        "code": "WALL_STUD_PARTITION",
        "name": "Stud Partition Wall",
        "category": "Framing",
        "description": (
            "Timber stud partition with 1/2\" drywall both faces, screws, "
            "and framing/boarding labour."
        ),
        "required_inputs": ["length", "height"],
        "calculator": "calculate_partition_wall",
        "components": [
            # (description, item_type, unit, formula, unit_cost, markup)
            ("Wall studs @ 16\" OC", "material", "each", "(length * 0.75) + 1", 4.50, 20.00),
            ("Top and bottom plates", "material", "lin ft", "length * 2", 3.20, 20.00),
            ("Drywall boards (1/2\") both faces +10% waste", "material", "sq ft", "length * height * 1.1", 8.90, 20.00),
            ("Drywall screws", "material", "each", "length * height * 8", 0.05, 20.00),
            ("Framing and boarding labour", "labor", "hr", "length * height * 0.05", 45.00, 20.00),
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
            ("Treated timber joists @ 16\" OC", "material", "each", "(length * 0.75) + 1", 12.50, 20.00),
            ("Support posts", "material", "each", "((length / 6) + 1) * 2", 18.00, 20.00),
            ("Decking boards (treated) +10% waste", "material", "sq ft", "length * width * 1.1", 38.00, 20.00),
            ("Decking screws", "material", "each", "length * width * 2", 0.12, 20.00),
            ("Deck installation labour", "labor", "hr", "length * width * 0.06", 55.00, 20.00),
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
        "calculator": "calculate_floor_tiling",
        "components": [
            ("Floor tile +15% cuts allowance", "material", "sq ft", "length * width * 1.15", 42.00, 20.00),
            ("Thinset mortar", "material", "lb", "length * width * 1", 3.50, 20.00),
            ("Tile grout", "material", "lb", "length * width * 0.1", 6.00, 20.00),
            ("Floor primer", "material", "gal", "length * width * 0.003", 18.00, 20.00),
            ("Tiling labour", "labor", "hr", "length * width * 0.14", 50.00, 20.00),
        ],
    },
]


def _seed_assemblies(db: Session) -> None:
    for spec in _ASSEMBLIES:
        row = {
            "organization_id": None,
            "code": spec["code"],
            "name": spec["name"],
            "category": spec["category"],
            "description": spec["description"],
            "required_inputs": spec["required_inputs"],
        }
        update_cols = ["name", "category", "description", "required_inputs"]
        # A calculator, once added to a spec, also converges -- but a spec
        # that has none must not wipe a previously-converged one.
        if spec.get("calculator"):
            row["calculator"] = spec["calculator"]
            update_cols.append("calculator")
        assembly_id, _ = upsert_row(
            db,
            models.ParametricAssembly,
            row,
            conflict_cols=["code"],
            update_cols=update_cols,
            index_where=models.ParametricAssembly.organization_id.is_(None),
        )
        # Replace components so unit/formula/description changes (metric -> US)
        # apply to existing installs instead of only new ones.
        db.query(models.AssemblyComponent).filter_by(assembly_id=assembly_id).delete()
        for description, item_type, unit, formula, cost, markup in spec["components"]:
            db.add(models.AssemblyComponent(
                assembly_id=assembly_id,
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
