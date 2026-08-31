#!/usr/bin/env python3
"""Export the multi-trade lexicon to a CSV file (RFC 4180).

Writes every TradeLexicon row with the spec columns. Aliases and
misspellings are emitted as compact JSON arrays in their own columns so
round-tripping into a spreadsheet or back into the DB is lossless.

Usage:
    python3 scripts/export_lexicon_csv.py [output.csv] [--trade Plumbing]

Defaults to export_trade_lexicon.csv in the repo root. Requires a live
database (DATABASE_URL). Use --from-seeds to export the seed definitions
without touching the database.
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.seeds.lexicon._shared import _load_providers, build_rows, serialize_row  # noqa: E402

COLUMNS = [
    "trade_category",
    "canonical_term",
    "spoken_aliases",
    "phonetic_respelling",
    "ipa_pronunciation",
    "common_misspellings_typos",
    "unit_of_measure",
    "definition_and_use",
]


def _rows_from_db(session) -> list:
    from sqlalchemy import text

    rows = session.execute(text(
        "SELECT * FROM trade_lexicon ORDER BY trade, term"
    )).mappings().all()
    out = []
    for r in rows:
        out.append({
            "trade_category": r["trade"],
            "canonical_term": r["term"],
            "spoken_aliases": r["aliases"] or [],
            "phonetic_respelling": r["phonetic_respelling"] or "",
            "ipa_pronunciation": r["ipa_pronunciation"] or "",
            "common_misspellings_typos": r["common_misspellings_typos"] or [],
            "unit_of_measure": r["default_unit"] or "EA",
            "definition_and_use": r["definition_and_use"] or "",
        })
    return out


def _rows_from_seeds() -> list:
    rows = []
    for families, trade in _load_providers():
        for row in build_rows(families, trade):
            rows.append({
                "trade_category": row["trade"],
                "canonical_term": row["term"],
                "spoken_aliases": row["aliases"] or [],
                "phonetic_respelling": row["phonetic_respelling"] or "",
                "ipa_pronunciation": row["ipa_pronunciation"] or "",
                "common_misspellings_typos": row["common_misspellings_typos"] or [],
                "unit_of_measure": row["default_unit"] or "EA",
                "definition_and_use": row["definition_and_use"] or "",
            })
    return rows


def write_csv(path: str, rows: list) -> int:
    """RFC 4180: CRLF line endings, quoting any field with comma/quote/newline."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL,
                                lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                col: json.dumps(row[col], ensure_ascii=False)
                if col in ("spoken_aliases", "common_misspellings_typos")
                else row[col]
                for col in COLUMNS
            })
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", nargs="?", default="export_trade_lexicon.csv")
    parser.add_argument("--trade", default=None, help="Only export this trade category")
    parser.add_argument("--from-seeds", action="store_true",
                        help="Export the seed definitions without a database")
    args = parser.parse_args()

    if args.from_seeds:
        rows = _rows_from_seeds()
    else:
        from app.database import SessionLocal
        session = SessionLocal()
        try:
            rows = _rows_from_db(session)
        finally:
            session.close()
    if args.trade:
        rows = [r for r in rows if r["trade_category"] == args.trade]
        if not rows:
            print(f"No rows for trade {args.trade!r}")
            sys.exit(1)
    count = write_csv(args.output, rows)
    print(f"Wrote {count} rows to {args.output}")


if __name__ == "__main__":
    main()
