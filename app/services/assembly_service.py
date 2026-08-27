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


def calculate_assembly_lines(assembly: ParametricAssembly, dimensions: dict) -> list[dict]:
    """Every component formula, evaluated against the caller's dimensions.

    Returns structured line items: description, item_type, quantity, unit,
    unit_cost, markup_percent, and the marked-up subtotal for the line.
    """
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
