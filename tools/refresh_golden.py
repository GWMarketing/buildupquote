"""Rewrite the golden snapshots from the current parser output.

    python3 tools/refresh_golden.py

Run this ONLY when you have deliberately changed what the parser produces
and you have read the resulting `git diff` line by line. If you did not
mean to change the output, do not run this -- fix the code instead.
"""
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tests"))

import golden_support as gs  # noqa: E402


def main():
    os.makedirs(gs.GOLDEN_DIR, exist_ok=True)
    for name in gs.FIXTURE_NAMES:
        data = gs.snapshot(gs.parse_fixture(name))
        with open(gs.golden_path(name), "w", encoding="utf-8") as fh:
            fh.write(gs.dumps(data))
        print(f"wrote {gs.golden_path(name)}  ({len(data['line_items'])} line items)")
    print("\nNow read `git diff tests/golden/` before committing.")


if __name__ == "__main__":
    main()
