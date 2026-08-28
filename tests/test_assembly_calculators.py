"""Unit tests for the hand-written assembly calculators and the dispatch
that lets an assembly's `calculator` name supersede its formula components.

The app modules are imported lazily inside setUpClass: this module is loaded
early in `unittest discover` (alphabetical order), and test_crm_api.py must
set DATABASE_URL to SQLite *before* app.database is first imported. These
pure-logic tests never touch the database, so a top-level import here would
lock in the wrong (default Postgres) engine for the whole suite.
"""
import unittest


class _AppImportsMixin:
    @classmethod
    def setUpClass(cls):
        from app.models import AssemblyComponent, ParametricAssembly
        from app.services import assembly_calculators
        from app.services.assembly_service import (
            AssemblyFormulaError,
            calculate_assembly_lines,
        )

        cls.calculators = assembly_calculators
        cls.assembly_formula_error = AssemblyFormulaError
        # staticmethod so `self.calculate_assembly_lines(...)` doesn't bind
        # self as the first argument.
        cls.calculate_assembly_lines = staticmethod(calculate_assembly_lines)
        cls.parametric_assembly = ParametricAssembly
        cls.assembly_component = AssemblyComponent


class PartitionWallCalculatorTest(_AppImportsMixin, unittest.TestCase):
    def test_returns_five_priced_lines(self):
        lines = self.calculators.calculate_partition_wall(4.0, 2.4)
        self.assertEqual(len(lines), 5)
        studs, track, boards, screws, labor = lines
        self.assertEqual(studs["trade"], "Carpentry")
        self.assertEqual(studs["type"], "Material")
        self.assertEqual(studs["unit"], "pcs")
        self.assertEqual(studs["unit_cost"], 4.50)
        self.assertEqual(studs["markup_pct"], 20)
        # ceil((4 / 0.6) + 1) = 8 studs, then +10% waste -> 9
        self.assertEqual(studs["quantity"], 9)
        # ceil(4*2*1.1) = 9 m of track
        self.assertEqual(track["quantity"], 9)
        # 2 sides, 2.88 m2/sheet, +10% -> ceil(19.2/2.88*1.1) = 8 sheets
        self.assertEqual(boards["quantity"], 8)
        # 8 sheets * 30 screws / 200 per box -> 2 boxes
        self.assertEqual(screws["quantity"], 2)
        # 4*2.4 = 9.6 m2 * 0.45 h -> 4.3 h
        self.assertEqual(labor["quantity"], 4.3)
        self.assertEqual(labor["type"], "Labor")
        self.assertEqual(labor["unit"], "hour")


class FloorTilingCalculatorTest(_AppImportsMixin, unittest.TestCase):
    def test_returns_four_priced_lines(self):
        lines = self.calculators.calculate_floor_tiling(4.0, 3.0)
        self.assertEqual(len(lines), 4)
        tiles, adhesive, grout, labor = lines
        self.assertEqual(tiles["trade"], "Tiling")
        self.assertEqual(tiles["type"], "Material")
        self.assertEqual(tiles["unit"], "m2")
        self.assertEqual(tiles["unit_cost"], 28.00)
        self.assertEqual(tiles["markup_pct"], 25)
        # 12 m2 * 1.12 waste = 13.44 m2
        self.assertEqual(tiles["quantity"], 13.44)
        # ceil(13.44 / 4) = 4 bags
        self.assertEqual(adhesive["quantity"], 4)
        # ceil(13.44 / 15) = 1 bag
        self.assertEqual(grout["quantity"], 1)
        # 12 m2 * 0.75 h = 9.0 h
        self.assertEqual(labor["quantity"], 9.0)
        self.assertEqual(labor["type"], "Labor")


class DispatchTest(_AppImportsMixin, unittest.TestCase):
    def _assembly(self, calculator=None, components=None):
        assembly = self.parametric_assembly(
            code="TEST", name="Test", category="Test", required_inputs=["length", "height"],
            calculator=calculator,
        )
        assembly.components = components or []
        return assembly

    def test_dispatch_uses_calculator_when_set(self):
        assembly = self._assembly(calculator="calculate_partition_wall")
        lines = self.calculate_assembly_lines(assembly, {"length": 4, "height": 2.4})
        self.assertEqual(len(lines), 5)
        first = lines[0]
        self.assertEqual(first["trade"], "Carpentry")
        self.assertEqual(first["item_type"], "material")  # lowercased from "Material"
        self.assertEqual(first["unit"], "pcs")
        self.assertEqual(first["quantity"], 9)
        # subtotal = 9 * 4.50 * 1.20 = 48.60
        self.assertAlmostEqual(first["subtotal"], 48.60, places=2)

    def test_dispatch_lowercases_trade_types(self):
        assembly = self._assembly(calculator="calculate_floor_tiling")
        lines = self.calculate_assembly_lines(assembly, {"length": 4, "width": 3})
        self.assertEqual([l["item_type"] for l in lines], ["material", "material", "material", "labor"])

    def test_unknown_calculator_raises(self):
        assembly = self._assembly(calculator="does_not_exist")
        with self.assertRaises(self.assembly_formula_error):
            self.calculate_assembly_lines(assembly, {"length": 4, "height": 2.4})

    def test_missing_dimension_raises(self):
        assembly = self._assembly(calculator="calculate_partition_wall")
        with self.assertRaises(self.assembly_formula_error):
            self.calculate_assembly_lines(assembly, {"length": 4})  # height missing

    def test_formula_components_still_work_without_calculator(self):
        component = self.assembly_component(
            description="Plates", item_type="material", unit="m",
            formula="length * 2", default_unit_cost=3.20, default_markup_percent=20,
        )
        assembly = self._assembly(calculator=None, components=[component])
        lines = self.calculate_assembly_lines(assembly, {"length": 4, "height": 2.4})
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["quantity"], 8.0)
        self.assertEqual(lines[0]["subtotal"], 8.0 * 3.20 * 1.2)


if __name__ == "__main__":
    unittest.main()
