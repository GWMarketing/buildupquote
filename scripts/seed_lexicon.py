#!/usr/bin/env python3
"""Seed / refresh the multi-trade lexicon from the curated family modules.

Idempotent: existing (trade, term) rows are updated in place, new rows are
inserted, and the seed also builds the Postgres search index + stored
function. Intended for a database that already exists.

Usage:
    python3 scripts/seed_lexicon.py
    python3 scripts/seed_lexicon.py --count-only
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count-only", action="store_true",
                        help="Print per-trade row counts without writing")
    args = parser.parse_args()

    from app.database import SessionLocal, engine
    from app.seeds.lexicon._shared import _load_providers, build_rows, seed_trade_lexicon

    if args.count_only:
        from collections import Counter
        counts = Counter()
        for families, trade in _load_providers():
            for row in build_rows(families, trade):
                counts[row["trade"]] += 1
        for trade, n in sorted(counts.items()):
            print(f"  {trade}: {n}")
        print(f"TOTAL: {sum(counts.values())}")
        return

    session = SessionLocal()
    try:
        # Collapse the deploy-#92 2x duplicates (keep lowest id per
        # (trade, term)) and lock the pair UNIQUE, then upsert atomically.
        from app.seeds._upsert import ensure_lexicon_unique
        ensure_lexicon_unique(session)
        print("Duplicate (trade, term) rows collapsed; UNIQUE index ensured")
        changed = seed_trade_lexicon(session)
        print(f"Lexicon seed complete: {changed} rows created/updated")
    finally:
        session.close()

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_trade_lexicon_search_vector "
                "ON trade_lexicon USING gin (to_tsvector('english', coalesce(search_vector, '')))"
            ))
            print("Postgres GIN search index ensured")


if __name__ == "__main__":
    main()
