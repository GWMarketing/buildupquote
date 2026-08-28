"""Hand-written parametric assembly calculators.

These replace the generic formula-string components for assemblies that have
a `calculator` name set on the ParametricAssembly row: the maths (stud
counts, board coverage, adhesive bags, labour hours...) is explicit Python
instead of an AST-evaluated formula string, and each line carries its trade
and display type.

Each calculator takes named dimensions and returns full line items shaped
{description, trade, type (Material/Labor/...), quantity, unit, unit_cost,
markup_pct}. The assembly service normalizes those into the app's line
contract (lowercased item_type, computed subtotal) when it dispatches.
"""
import math


def calculate_partition_wall(length_m: float, height_m: float, waste_pct: float = 0.10):
    """Timber/metal stud partition with drywall both faces.

    Studs at 600mm centres, top + bottom track, plasterboard coverage from
    2.4x1.2m sheets, screws by the 200-count box, and framing/boarding hours.
    """
    area = length_m * height_m

    # 600mm centre spacing for studs
    num_studs = math.ceil((length_m / 0.6) + 1) * (1.0 + waste_pct)
    # Track top and bottom
    track_lm = (length_m * 2) * (1.0 + waste_pct)
    # 2 sides of drywall boards (2.88m2 per sheet: 2.4 x 1.2)
    board_sheets = math.ceil(((area * 2) / 2.88) * (1.0 + waste_pct))
    screws_box = math.ceil((board_sheets * 30) / 200)  # 200 per box
    labor_hours = round(area * 0.45, 1)  # ~0.45h per m2

    return [
        {"trade": "Carpentry", "description": f"70mm Metal/Timber Studs ({height_m}m)",
         "type": "Material", "quantity": math.ceil(num_studs), "unit": "pcs",
         "unit_cost": 4.50, "markup_pct": 20},
        {"trade": "Carpentry", "description": "72mm Track / Runner Channel",
         "type": "Material", "quantity": math.ceil(track_lm), "unit": "linear_m",
         "unit_cost": 3.80, "markup_pct": 20},
        {"trade": "Drywall", "description": "12.5mm Plasterboard (2.4m x 1.2m)",
         "type": "Material", "quantity": board_sheets, "unit": "sheet",
         "unit_cost": 9.20, "markup_pct": 20},
        {"trade": "Drywall", "description": "Drywall Screws 35mm (Box of 200)",
         "type": "Material", "quantity": screws_box, "unit": "box",
         "unit_cost": 6.50, "markup_pct": 20},
        {"trade": "Drywall", "description": "Partition Wall Framing & Boarding Labor",
         "type": "Labor", "quantity": labor_hours, "unit": "hour",
         "unit_cost": 35.00, "markup_pct": 15},
    ]


def calculate_floor_tiling(length_m: float, width_m: float, waste_pct: float = 0.12):
    """Floor tiling: tile coverage + waste, adhesive by 20kg bag, grout by
    5kg bag, and installation hours."""
    area = length_m * width_m
    total_area_with_waste = area * (1.0 + waste_pct)

    # 20kg bag covers approx 4m2
    adhesive_bags = math.ceil(total_area_with_waste / 4.0)
    # 5kg grout covers approx 15m2
    grout_bags = math.ceil(total_area_with_waste / 15.0)
    labor_hours = round(area * 0.75, 1)  # ~0.75h per m2

    return [
        {"trade": "Tiling", "description": f"Floor Tiles (Coverage for {round(area, 1)}m² + waste)",
         "type": "Material", "quantity": round(total_area_with_waste, 2), "unit": "m2",
         "unit_cost": 28.00, "markup_pct": 25},
        {"trade": "Tiling", "description": "Flexible Tile Adhesive (20kg Bag)",
         "type": "Material", "quantity": adhesive_bags, "unit": "bag",
         "unit_cost": 18.50, "markup_pct": 20},
        {"trade": "Tiling", "description": "Wall & Floor Anti-Mould Grout (5kg)",
         "type": "Material", "quantity": grout_bags, "unit": "bag",
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
    "calculate_partition_wall": {"length_m": "length", "height_m": "height"},
    "calculate_floor_tiling": {"length_m": "length", "width_m": "width"},
}


def get_calculator(name: str):
    """The registered calculator for `name`, or None."""
    return _CALCULATORS.get(name)
