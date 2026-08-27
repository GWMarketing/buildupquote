"""Structural checks on app.py.

WHY THIS EXISTS
---------------
`app.py` is the one file the rest of the suite can't execute: running it
means running Streamlit, which needs a browser session. So the tests
around it exercise its data helpers (`test_app_logic.py`) and leave the
screen-drawing function alone.

That gap let a real bug reach Glenn's machine. A new function was inserted
into the middle of `main()` at column zero, which silently ENDED `main()`
there and swallowed the remaining 300 lines into the new function's body.
The file still compiled. Every one of the then-214 tests still passed. The
app crashed on the first upload with `NameError: name 'contractor' is not
defined`, because a line that used to live in `main()` was now executing
in a function where that variable never existed.

These tests close that gap without needing Streamlit: they read the file's
own scope table and check that every name a function reads is one it could
actually resolve at runtime, and that `main()` still owns the whole screen
rather than half of it.
"""
import builtins
import os
import symtable
import sys
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_TESTS_DIR, "..")
sys.path.insert(0, _ROOT)

APP_PATH = os.path.join(_ROOT, "app.py")


def _read_app():
    with open(APP_PATH, encoding="utf-8") as fh:
        return fh.read()


def _module_names(top):
    """Every name the module itself defines: imports, assignments, defs."""
    return {sym.get_name() for sym in top.get_symbols() if sym.is_assigned() or sym.is_imported()}


def _walk(scope, path, known, problems):
    """Check one scope, then its children, carrying enclosing names down."""
    for sym in scope.get_symbols():
        name = sym.get_name()
        # symtable marks a name read in a function but never bound there
        # (and not closed over) as global -- which at runtime means "look
        # it up in module globals, then builtins, then raise NameError".
        if sym.is_referenced() and sym.is_global() and name not in known:
            problems.append(f"{path}: reads '{name}', which nothing defines")
    child_known = known | {s.get_name() for s in scope.get_symbols() if s.is_assigned()}
    for child in scope.get_children():
        _walk(child, f"{path} > {child.get_name()}", child_known, problems)


class NameResolutionTests(unittest.TestCase):
    def test_every_name_app_reads_is_one_it_can_resolve(self):
        """The exact check that would have caught the NameError crash."""
        source = _read_app()
        top = symtable.symtable(source, "app.py", "exec")
        known = _module_names(top) | set(dir(builtins))
        problems = []
        for child in top.get_children():
            _walk(child, child.get_name(), known, problems)
        self.assertEqual(problems, [], "unresolvable names in app.py:\n  " + "\n  ".join(problems))


class ScreenStructureTests(unittest.TestCase):
    """`main()` draws the whole screen. If a chunk of it silently moves
    into another function, these fail -- which is what actually happened."""

    def setUp(self):
        self.source = _read_app()
        self.top = symtable.symtable(self.source, "app.py", "exec")
        self.scopes = {c.get_name(): c for c in self.top.get_children()}

    def test_main_still_owns_the_whole_screen(self):
        import ast

        tree = ast.parse(self.source)
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("main", functions)
        main = functions["main"]
        # Every other function must be defined BEFORE main, not inside the
        # span it occupies. A helper that lands mid-main truncates it.
        for name, node in functions.items():
            if name == "main":
                continue
            self.assertLess(
                node.end_lineno, main.lineno,
                f"{name}() is defined inside main()'s body -- that truncates main()",
            )

    def test_main_draws_the_pieces_it_is_supposed_to(self):
        """Each of these lives at a different depth in main(); losing any
        one of them means a chunk of the screen went missing."""
        for marker in (
            "_contractor_sidebar",     # sidebar
            "_render_read_banner",     # the how-we-read-this verdict
            "st.tabs",                 # the five screens
            "tab_scope", "tab_review", "tab_code", "tab_price", "tab_export",
            "_editable_table",         # the scope grid
            "Needs review",
            "_carrier_summary_panel",  # the carrier's own Overhead/Profit/Net Claim ladder
            "_code_additions_tab",     # the Texas code checklist + code-required additions
            "Totals by trade",
            "price_editor",
            "When each part gets paid",
            "Scope as CSV",
            "Branded proposal PDF",
            "export_name",             # the custom download name box
        ):
            self.assertIn(marker, self.source, f"main() no longer mentions {marker!r}")

    def test_the_banner_only_uses_what_it_is_given(self):
        """It takes one argument; it must not reach for main()'s locals."""
        banner = self.scopes.get("_render_read_banner")
        self.assertIsNotNone(banner)
        self.assertEqual(list(banner.get_parameters()), ["estimate"])
        for leaked in ("contractor", "fields", "rows", "included", "tax_rule"):
            symbol = None
            try:
                symbol = banner.lookup(leaked)
            except KeyError:
                continue
            self.assertFalse(
                symbol.is_referenced() and symbol.is_global(),
                f"_render_read_banner reads main()'s '{leaked}'",
            )


if __name__ == "__main__":
    unittest.main()
