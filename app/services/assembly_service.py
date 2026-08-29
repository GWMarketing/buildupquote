"""Parametric assembly math engine.

Formulas live in the database as plain strings ("length * height * 1.1")
and are evaluated by a small AST-based solver: the expression is parsed,
then walked node by node allowing ONLY numbers, arithmetic operators, and
dimension names supplied by the caller. Nothing arbitrary -- no calls, no
attributes, no imports -- can ever execute.
"""
import ast
import operator

from app.models import ParametricAssembly
from app.services import assembly_calculators


class AssemblyFormulaError(ValueError):
    """Raised when a formula or its inputs are invalid."""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_eval_formula(formula: str, dimensions: dict) -> float:
    """Evaluate a formula string against {dimension_name: number}."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise AssemblyFormulaError(f"Formula is not valid: {formula!r}") from exc
    try:
        return float(_eval_node(tree.body, dimensions))
    except AssemblyFormulaError:
        raise
    except Exception as exc:  # noqa: BLE001 -- any math failure is a formula error
        raise AssemblyFormulaError(f"Could not evaluate {formula!r}: {exc}") from exc


def _eval_node(node, dims):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in dims:
            raise AssemblyFormulaError(f"Formula uses unknown dimension '{node.id}'")
        return float(dims[node.id])
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise AssemblyFormulaError("Unsupported operator in formula")
        return op(_eval_node(node.left, dims), _eval_node(node.right, dims))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise AssemblyFormulaError("Unsupported unary operator in formula")
        return op(_eval_node(node.operand, dims))
    raise AssemblyFormulaError(f"Unsupported expression element: {type(node).__name__}")


def _run_calculator(name: str, dimensions: dict) -> list[dict]:
    """Dispatch to a hand-written Python calculator (see
    assembly_calculators.py) and normalize its lines into the app's line
    contract: lowercase item_type, computed subtotal, optional trade."""
    func = assembly_calculators.get_calculator(name)
    if func is None:
        raise AssemblyFormulaError(f"Unknown assembly calculator '{name}'")

    mapping = assembly_calculators.CALC_DIMENSION_MAP.get(name, {})
    kwargs = {}
    for param, dim_key in mapping.items():
        value = (dimensions or {}).get(dim_key)
        if value is None:
            raise AssemblyFormulaError(f"Missing required input '{dim_key}'")
        kwargs[param] = _to_float(dim_key, value)

    lines = []
    for raw in func(**kwargs):
        quantity = float(raw.get("quantity") or 0)
        unit_cost = float(raw.get("unit_cost") or 0)
        markup = float(raw.get("markup_pct") or 0)
        lines.append({
            "description": str(raw.get("description") or ""),
            "item_type": str(raw.get("type") or "material").lower(),
            "trade": (str(raw.get("trade") or "").strip() or None),
            "quantity": round(quantity, 3),
            "unit": str(raw.get("unit") or "each"),
            "unit_cost": round(unit_cost, 2),
            "markup_percent": round(markup, 2),
            "subtotal": round(quantity * unit_cost * (1 + markup / 100.0), 2),
        })
    return lines


def calculate_calculator(name: str, dimensions: dict) -> list[dict]:
    """Run a registered Python calculator by name and normalize its lines
    into the app's line contract. Public entry point for the catalog's
    calculate-assembly endpoint."""
    return _run_calculator(name, dimensions)


def calculate_assembly_lines(assembly: ParametricAssembly, dimensions: dict) -> list[dict]:
    """Price an assembly for the caller's dimensions.

    Assemblies with a `calculator` name dispatch to a hand-written Python
    calculator; the rest evaluate their component formulas (both produce the
    same {description, item_type, quantity, unit, unit_cost, markup_percent,
    subtotal} line contract).
    """
    calculator = getattr(assembly, "calculator", None)
    if calculator:
        return _run_calculator(calculator, dimensions)

    dims = {k: _to_float(k, v) for k, v in (dimensions or {}).items()}
    missing = [i for i in (assembly.required_inputs or []) if i not in dims]
    if missing:
        raise AssemblyFormulaError(f"Missing required inputs: {', '.join(missing)}")

    lines = []
    for component in sorted(assembly.components, key=lambda c: c.id or 0):
        quantity = safe_eval_formula(component.formula, dims)
        if quantity < 0:
            raise AssemblyFormulaError(
                f"Formula for {component.description!r} produced a negative "
                f"quantity ({quantity:g})"
            )
        unit_cost = float(component.default_unit_cost or 0)
        markup = float(
            component.default_markup_percent
            if component.default_markup_percent is not None
            else 20.0
        )
        lines.append({
            "description": component.description,
            "item_type": component.item_type,
            "quantity": round(quantity, 3),
            "unit": component.unit,
            "unit_cost": round(unit_cost, 2),
            "markup_percent": round(markup, 2),
            "subtotal": round(quantity * unit_cost * (1 + markup / 100.0), 2),
        })
    return lines


def _to_float(name: str, value):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AssemblyFormulaError(
            f"Dimension '{name}' must be a number, got {value!r}"
        ) from None


def _apply_waste(raw_lines: list[dict], waste_percent: float):
    """Inflate MATERIAL line quantities by the waste factor and summarize.

    Waste applies ONLY to item_type == 'material' lines (quantities and
    therefore their cost); labor/plant/subcontractor lines pass through
    untouched. Returns (lines_with_waste, summary) where summary is:
      {waste_percent, materials_raw, waste_added, labor_total, total}.
    """
    waste = max(0.0, float(waste_percent or 0)) / 100.0
    materials_raw = 0.0
    waste_added = 0.0
    labor_total = 0.0
    out = []
    for line in raw_lines:
        if line["item_type"] == "material" and waste > 0:
            qty = float(line["quantity"])
            raw_sub = round(qty * line["unit_cost"] * (1 + line["markup_percent"] / 100.0), 2)
            inflated = round(qty * (1 + waste), 3)
            new_sub = round(inflated * line["unit_cost"] * (1 + line["markup_percent"] / 100.0), 2)
            materials_raw += raw_sub
            waste_added += round(new_sub - raw_sub, 2)
            line = dict(line, quantity=inflated, subtotal=new_sub)
        elif line["item_type"] == "material":
            materials_raw += float(line["subtotal"])
        else:
            labor_total += float(line["subtotal"])
        out.append(line)
    summary = {
        "waste_percent": round(float(waste_percent or 0), 2),
        "materials_raw": round(materials_raw, 2),
        "waste_added": round(waste_added, 2),
        "labor_total": round(labor_total, 2),
        "total": round(sum(float(l["subtotal"]) for l in out), 2),
    }
    return out, summary


def calculate_assembly_with_summary(assembly: ParametricAssembly, dimensions: dict,
                                    waste_percent: float = 10.0):
    """Price an assembly for the caller's dimensions, applying the material
    waste factor. Returns (lines, summary) -- both the inflated line list and
    the materials-raw / waste-added / labor breakdown for the cockpit."""
    raw = calculate_assembly_lines(assembly, dimensions, waste_percent=0)
    return _apply_waste(raw, waste_percent)


def calculate_assembly_lines(assembly: ParametricAssembly, dimensions: dict,
                             waste_percent: float = 0.0) -> list[dict]:
    """Price an assembly for the caller's dimensions.

    Assemblies with a `calculator` name dispatch to a hand-written Python
    calculator; the rest evaluate their component formulas (both produce the
    same {description, item_type, quantity, unit, unit_cost, markup_percent,
    subtotal} line contract). When waste_percent > 0, material quantities are
    inflated by (1 + waste/100) -- labor is never multiplied.
    """
    calculator = getattr(assembly, "calculator", None)
    if calculator:
        raw = _run_calculator(calculator, dimensions)
    else:
        dims = {k: _to_float(k, v) for k, v in (dimensions or {}).items()}
        missing = [i for i in (assembly.required_inputs or []) if i not in dims]
        if missing:
            raise AssemblyFormulaError(f"Missing required inputs: {', '.join(missing)}")

        raw = []
        for component in sorted(assembly.components, key=lambda c: c.id or 0):
            quantity = safe_eval_formula(component.formula, dims)
            if quantity < 0:
                raise AssemblyFormulaError(
                    f"Formula for {component.description!r} produced a negative "
                    f"quantity ({quantity:g})"
                )
            unit_cost = float(component.default_unit_cost or 0)
            markup = float(
                component.default_markup_percent
                if component.default_markup_percent is not None
                else 20.0
            )
            raw.append({
                "description": component.description,
                "item_type": component.item_type,
                "quantity": round(quantity, 3),
                "unit": component.unit,
                "unit_cost": round(unit_cost, 2),
                "markup_percent": round(markup, 2),
                "subtotal": round(quantity * unit_cost * (1 + markup / 100.0), 2),
            })

    lines, _summary = _apply_waste(raw, waste_percent)
    return lines
