"""Atomic upsert + unique-key hardening for the startup seeds.

`gunicorn -w 4` runs the lifespan seed in every worker at boot, and a plain
check-then-insert races: two workers both see a missing row, both insert,
and the row ends up duplicated -- the production 2x trade-lexicon bug that
doubled every new-trade lexicon at deploy #91 (311 rows became 622, etc.).

Postgres gets a real INSERT ... ON CONFLICT ... DO UPDATE, which is safe by
construction. SQLite (dev/tests, single process) keeps the check-then-insert
path, which cannot race there. Both paths return (row_id, changed) so every
seed behaves identically on either database.

The ensure_*_unique() functions collapse any duplicates left over from the
old buggy seed (keep the lowest id per key) and then create the UNIQUE index
that makes a re-occurrence impossible. They are idempotent -- once the index
exists the DELETE is skipped and the call is a no-op.
"""
from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

# ---------------------------------------------------------------------------
# Atomic upsert (one row)
# ---------------------------------------------------------------------------


def upsert_row(db, model, row, conflict_cols, update_cols, index_where=None):
    """Insert `row`, or converge the conflicting row's update_cols in place.

    Returns (row_id, changed): row_id is the id of the inserted or updated
    row; changed is 1 when the row was created or actually modified and 0
    when it already matched (so a converged seed is a no-op on every boot).
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        stmt = pg_insert(model).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=conflict_cols,
            index_where=index_where,
            set_={c: stmt.excluded[c] for c in update_cols},
            where=or_(
                *[getattr(model, c).is_distinct_from(stmt.excluded[c])
                  for c in update_cols]
            ),
        ).returning(model.id)
        res = db.execute(stmt)
        return res.scalar_one_or_none(), int(res.rowcount or 0)
    # SQLite / dev / tests: single process, check-then-insert cannot race.
    filt = {c: row[c] for c in conflict_cols}
    exists = db.query(model).filter_by(**filt).first()
    if exists is None:
        obj = model(**row)
        db.add(obj)
        db.flush()
        return obj.id, 1
    dirty = False
    for c in update_cols:
        if row.get(c) != getattr(exists, c):
            setattr(exists, c, row.get(c))
            dirty = True
    if dirty:
        db.flush()
    return exists.id, int(dirty)


# ---------------------------------------------------------------------------
# One-time dedupe + UNIQUE index for the seeded dictionary tables
# ---------------------------------------------------------------------------


def _index_exists(conn, name):
    if conn.dialect.name == "postgresql":
        return bool(conn.execute(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}
        ).scalar())
    return bool(conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :n"),
        {"n": name},
    ).scalar())


def _ensure_unique(db, table, group_cols, unique_name, create_sql, scope=None):
    """Delete duplicate rows (keep lowest id) then lock the key UNIQUE."""
    bind = db.get_bind()
    with bind.begin() as conn:
        if _index_exists(conn, unique_name):
            return  # already hardened -- the index prevents new duplicates
        inner_scope = f" WHERE {scope}" if scope else ""
        inner = (
            f"SELECT MIN(id) FROM {table}{inner_scope} "
            f"GROUP BY {', '.join(group_cols)}"
        )
        outer_scope = f" AND {scope}" if scope else ""
        conn.execute(text(
            f"DELETE FROM {table} WHERE id NOT IN ({inner}){outer_scope}"
        ))
        conn.execute(text(create_sql))


def ensure_lexicon_unique(db) -> None:
    """Collapse duplicate (trade, term) rows and lock the pair unique."""
    _ensure_unique(
        db,
        table="trade_lexicon",
        group_cols=["trade", "term"],
        unique_name="uq_trade_lexicon_trade_term",
        create_sql=(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_lexicon_trade_term "
            "ON trade_lexicon (trade, term)"
        ),
    )


def ensure_assembly_unique(db) -> None:
    """Collapse duplicate global (organization_id IS NULL) assembly codes.

    Organisation-owned assemblies are untouched -- the partial index only
    constrains the seeded global templates.
    """
    _ensure_unique(
        db,
        table="parametric_assemblies",
        group_cols=["code"],
        unique_name="uq_parametric_assemblies_global_code",
        create_sql=(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_parametric_assemblies_global_code "
            "ON parametric_assemblies (code) WHERE organization_id IS NULL"
        ),
        scope="organization_id IS NULL",
    )


def ensure_seed_uniques(db) -> None:
    """Hardening pass for every seeded table that lacks its own constraint.

    trade_catalog_item is already covered by the model's unique=True on
    canonical_name (so its seed can never silently duplicate -- it races
    into an IntegrityError instead, which the atomic upsert now prevents).
    """
    ensure_lexicon_unique(db)
    ensure_assembly_unique(db)
