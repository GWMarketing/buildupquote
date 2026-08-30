"""Hand-written parametric assembly calculators (imperial).

These replace the generic formula-string components for assemblies that have
a `calculator` name set on the ParametricAssembly row: the maths (stud
counts, board coverage, adhesive bags, labour hours...) is explicit Python
instead of an AST-evaluated formula string, and each line carries its trade
and display type.

Each calculator takes named dimensions in FEET and returns full line items
shaped {description, trade, type (Material/Labor/...), quantity, unit,
unit_cost, markup_pct}. The assembly service normalizes those into the app's
line contract (lowercased item_type, computed subtotal) when it dispatches.
"""
import math


def calculate_partition_wall(length_ft: float, height_ft: float, waste_pct: float = 0.10):
    """Timber/metal stud partition with drywall both faces (US imperial).

    Studs at 24\" OC, top + bottom track, drywall coverage from 4'x8' sheets,
    screws by the 200-count box, and framing/boarding hours.
    """
    area = length_ft * height_ft

    # 24" on-center stud spacing (2 ft)
    num_studs = math.ceil((length_ft / 2) + 1) * (1.0 + waste_pct)
    # Track top and bottom
    track_lf = (length_ft * 2) * (1.0 + waste_pct)
    # 2 sides of drywall boards (32 sq ft per 4'x8' sheet)
    board_sheets = math.ceil(((area * 2) / 32) * (1.0 + waste_pct))
    screws_box = math.ceil((board_sheets * 30) / 200)  # 200 per box
    labor_hours = round(area * 0.05, 1)  # ~0.05h per sq ft

    return [
        {"trade": "Carpentry", "description": f"2x4 Metal/Timber Studs ({height_ft} ft)",
         "type": "Material", "quantity": math.ceil(num_studs), "unit": "each",
         "unit_cost": 4.50, "markup_pct": 20},
        {"trade": "Carpentry", "description": "Track / Runner Channel",
         "type": "Material", "quantity": math.ceil(track_lf), "unit": "lin ft",
         "unit_cost": 3.80, "markup_pct": 20},
        {"trade": "Drywall", "description": "1/2\" Drywall Board (4' x 8')",
         "type": "Material", "quantity": board_sheets, "unit": "sheet",
         "unit_cost": 9.20, "markup_pct": 20},
        {"trade": "Drywall", "description": "Drywall Screws 1-1/4\" (Box of 200)",
         "type": "Material", "quantity": screws_box, "unit": "box",
         "unit_cost": 6.50, "markup_pct": 20},
        {"trade": "Drywall", "description": "Partition Wall Framing & Boarding Labor",
         "type": "Labor", "quantity": labor_hours, "unit": "hour",
         "unit_cost": 35.00, "markup_pct": 15},
    ]


def calculate_floor_tiling(length_ft: float, width_ft: float, waste_pct: float = 0.12):
    """Floor tiling (US imperial): tile coverage + waste, thinset by 50 lb bag,
    grout by 25 lb box, and installation hours."""
    area = length_ft * width_ft
    total_area_with_waste = area * (1.0 + waste_pct)

    # 50 lb thinset bag covers ~50 sq ft
    adhesive_bags = math.ceil(total_area_with_waste / 50.0)
    # 25 lb grout box covers ~200 sq ft
    grout_boxes = math.ceil(total_area_with_waste / 200.0)
    labor_hours = round(area * 0.07, 1)  # ~0.07h per sq ft

    return [
        {"trade": "Tiling", "description": f"Floor Tiles (Coverage for {round(area, 1)} sq ft + waste)",
         "type": "Material", "quantity": round(total_area_with_waste, 2), "unit": "sq ft",
         "unit_cost": 28.00, "markup_pct": 25},
        {"trade": "Tiling", "description": "Thinset Mortar (50 lb Bag)",
         "type": "Material", "quantity": adhesive_bags, "unit": "bag",
         "unit_cost": 18.50, "markup_pct": 20},
        {"trade": "Tiling", "description": "Tile Grout (25 lb Box)",
         "type": "Material", "quantity": grout_boxes, "unit": "box",
         "unit_cost": 12.00, "markup_pct": 20},
        {"trade": "Tiling", "description": "Floor Tile Installation & Grouting Labor",
         "type": "Labor", "quantity": labor_hours, "unit": "hour",
         "unit_cost": 40.00, "markup_pct": 15},
    ]


_CALCULATORS = {
    "calculate_partition_wall": calculate_partition_wall,
    "calculate_floor_tiling": calculate_floor_tiling,
}

# calculator parameter -> assembly dimension key (the builder sends
# {length, height, width} from the assembly's required_inputs).
CALC_DIMENSION_MAP = {
    "calculate_partition_wall": {"length_ft": "length", "height_ft": "height"},
    "calculate_floor_tiling": {"length_ft": "length", "width_ft": "width"},
}


def get_calculator(name: str):
    """The registered calculator for `name`, or None."""
    return _CALCULATORS.get(name)
