#!/usr/bin/env python3
"""Standalone runner for the trade catalog seed (trigram autocorrect data).

Usage:
    venv/bin/python scripts/seed_trade_data.py

Safe to run any number of times: existing canonical items are left alone.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.database import Base, SessionLocal, engine, ensure_legacy_columns  # noqa: E402
from app.seeds.trade_catalog_seed import seed_trade_catalog  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_legacy_columns(engine)
    db = SessionLocal()
    try:
        seed_trade_catalog(db)
        print("Trade catalog seeded: drywall, framing, plumbing, masonry, tiling, paint.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
