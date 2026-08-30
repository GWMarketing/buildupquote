"""Seed the trade catalog: canonical priced items plus the raw-term aliases
a contractor might actually type on an estimate ('sheetrock', '2x4',
'thinset'...). These feed the /api/catalog/autocorrect typeahead.

Idempotent per canonical_name, so it's safe to run on every startup or via
scripts/seed_trade_data.py.
"""
from sqlalchemy.orm import Session

from app import models

_CATALOG = [
    # Drywall
    {
        "canonical_name": "Drywall board (1/2\")",
        "renamed_from": "Drywall board (12.5mm)",
        "trade": "Drywall", "unit": "sq ft", "default_unit_cost": 8.90,
        "default_trade_type": "Material",
        "synonyms": ["drywall", "drywall board", "sheetrock", "gyprock",
                     "plasterboard", "wall board", "1/2\" board", "gypsum board"],
    },
    {
        "canonical_name": "Drywall labour",
        "trade": "Drywall", "unit": "hr", "default_unit_cost": 45.00,
        "default_trade_type": "Labor",
        "synonyms": ["drywall labour", "drywall labor", "hanging drywall",
                     "board hanging", "drywall install", "sheetrock install"],
    },
    # Framing
    {
        "canonical_name": "Timber wall stud (2x4)",
        "trade": "Framing", "unit": "each", "default_unit_cost": 4.50,
        "default_trade_type": "Material",
        "synonyms": ["timber stud", "wall stud", "2x4", "2x4 stud", "wood stud",
                     "frame stud", "stick"],
    },
    {
        "canonical_name": "Framing labour",
        "trade": "Framing", "unit": "hr", "default_unit_cost": 55.00,
        "default_trade_type": "Labor",
        "synonyms": ["framing labour", "framing labor", "carpentry framing",
                     "rough carpentry", "stick framing", "frame install"],
    },
    # Plumbing
    {
        "canonical_name": "PVC drain pipe (4\")",
        "renamed_from": "PVC drain pipe (100mm)",
        "trade": "Plumbing", "unit": "ft", "default_unit_cost": 6.80,
        "default_trade_type": "Material",
        "synonyms": ["pvc pipe", "drain pipe", "waste pipe", "4 inch pipe",
                     "4\" pvc", "sewer pipe"],
    },
    {
        "canonical_name": "Copper supply pipe (1/2\")",
        "renamed_from": "Copper supply pipe (15mm)",
        "trade": "Plumbing", "unit": "ft", "default_unit_cost": 9.50,
        "default_trade_type": "Material",
        "synonyms": ["copper pipe", "supply pipe", "supply line", "1/2\" copper",
                     "water pipe"],
    },
    {
        "canonical_name": "Plumbing labour",
        "trade": "Plumbing", "unit": "hr", "default_unit_cost": 70.00,
        "default_trade_type": "Labor",
        "synonyms": ["plumbing labour", "plumbing labor", "plumber",
                     "pipe fitting", "pipe work", "plumbing install"],
    },
    # Masonry
    {
        "canonical_name": "Concrete blocks (4\")",
        "renamed_from": "Concrete blocks (100mm)",
        "trade": "Masonry", "unit": "each", "default_unit_cost": 3.40,
        "default_trade_type": "Material",
        "synonyms": ["concrete block", "cinder block", "cmu", "cmu block",
                     "breeze block", "block", "masonry block"],
    },
    {
        "canonical_name": "Brick veneer",
        "trade": "Masonry", "unit": "sq ft", "default_unit_cost": 48.00,
        "default_trade_type": "Material",
        "synonyms": ["brick veneer", "face brick", "brick facing", "brick cladding"],
    },
    {
        "canonical_name": "Masonry labour",
        "trade": "Masonry", "unit": "hr", "default_unit_cost": 60.00,
        "default_trade_type": "Labor",
        "synonyms": ["masonry labour", "masonry labor", "bricklaying",
                     "brickwork", "block laying", "masonry install"],
    },
    # Tiling
    {
        "canonical_name": "Floor tile (ceramic)",
        "trade": "Tiling", "unit": "sq ft", "default_unit_cost": 42.00,
        "default_trade_type": "Material",
        "synonyms": ["floor tile", "ceramic tile", "porcelain tile", "wall tile",
                     "tile"],
    },
    {
        "canonical_name": "Tile adhesive",
        "trade": "Tiling", "unit": "lb", "default_unit_cost": 3.50,
        "default_trade_type": "Material",
        "synonyms": ["tile adhesive", "adhesive", "thinset", "thin set",
                     "tile glue", "tile cement"],
    },
    {
        "canonical_name": "Tiling labour",
        "trade": "Tiling", "unit": "hr", "default_unit_cost": 50.00,
        "default_trade_type": "Labor",
        "synonyms": ["tiling labour", "tiling labor", "tile setter",
                     "tile install", "tiling"],
    },
    # Paint
    {
        "canonical_name": "Interior wall paint (1 gal)",
        "renamed_from": "Interior wall paint (10L)",
        "trade": "Paint", "unit": "each", "default_unit_cost": 65.00,
        "default_trade_type": "Material",
        "synonyms": ["interior paint", "wall paint", "latex paint", "1 gal paint",
                     "emulsion", "paint"],
    },
    {
        "canonical_name": "Painting labour",
        "trade": "Paint", "unit": "hr", "default_unit_cost": 35.00,
        "default_trade_type": "Labor",
        "synonyms": ["painting labour", "painting labor", "painter", "paint job",
                     "spray painting", "paint labour"],
    },
    # Additional standard building materials
    {
        "canonical_name": "Drywall screws (box of 200)",
        "trade": "Drywall", "unit": "box", "default_unit_cost": 6.50,
        "default_trade_type": "Material",
        "synonyms": ["drywall screws", "drywall screw", "screws", "bugle head screw",
                     "screws for drywall", "plasterboard screws"],
    },
    {
        "canonical_name": "Skim coat plaster",
        "trade": "Drywall", "unit": "lb", "default_unit_cost": 1.20,
        "default_trade_type": "Material",
        "synonyms": ["skim", "skim coat", "skim plaster", "skimming", "finishing plaster",
                     "multifinish", "plaster skim"],
    },
    {
        "canonical_name": "Tile grout (25 lb)",
        "renamed_from": "Tile grout (5kg)",
        "trade": "Tiling", "unit": "box", "default_unit_cost": 12.00,
        "default_trade_type": "Material",
        "synonyms": ["grout", "tile grout", "joint filler", "anti-mould grout",
                     "wall and floor grout", "grout box"],
    },
]


def seed_trade_catalog(db: Session) -> None:
    """Insert/sync the imperial catalog. Items created under an earlier metric
    name are renamed (matched via `renamed_from`) and every seeded row's unit
    and synonyms converge to the current spec on every startup."""
    for spec in _CATALOG:
        item = (
            db.query(models.TradeCatalogItem)
            .filter(models.TradeCatalogItem.canonical_name == spec["canonical_name"])
            .first()
        )
        if item is None and spec.get("renamed_from"):
            item = (
                db.query(models.TradeCatalogItem)
                .filter(models.TradeCatalogItem.canonical_name == spec["renamed_from"])
                .first()
            )
        if item is None:
            item = models.TradeCatalogItem(
                canonical_name=spec["canonical_name"],
                trade=spec["trade"],
                unit=spec["unit"],
                default_unit_cost=spec["default_unit_cost"],
                default_trade_type=spec["default_trade_type"],
            )
            db.add(item)
            db.flush()
        else:
            item.canonical_name = spec["canonical_name"]
            item.trade = spec["trade"]
            item.unit = spec["unit"]
            db.add(item)
            db.flush()
        # Rebuild synonyms so metric aliases ("12.5mm board") are replaced.
        db.query(models.TradeSynonym).filter_by(catalog_id=item.id).delete()
        for term in spec["synonyms"]:
            db.add(models.TradeSynonym(catalog_id=item.id, raw_term=term))
    db.commit()
