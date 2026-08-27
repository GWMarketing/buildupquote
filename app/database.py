"""PostgreSQL connection for the BuildUpQuote FastAPI deployment.

DATABASE_URL is read from the environment (docker-compose sets it on the
`web` service as postgresql://app_user:app_password@db:5432/buildupquote_db).
The fallback uses the same credentials against localhost so local
development against a local postgres works out of the box.
"""
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://app_user:app_password@localhost:5432/buildupquote_db",
)

# create_engine is lazy: nothing connects until a session is actually used,
# so importing this module never requires postgres to be up.
engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_legacy_columns(bind):
    """Idempotently add the multi-tenancy columns the pre-organization
    deploy's `users` table is missing.

    `create_all` never alters an existing table, and the VPS `users` table
    was created before `organization_id`/`role` existed. With zero
    production data this is a plain ADD COLUMN -- safe, and a no-op forever
    after. Column existence is checked first (rather than relying on
    `IF NOT EXISTS`, which PostgreSQL supports but SQLite doesn't) so the
    same code runs against both.
    """
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()
    if "users" not in existing_tables:
        return  # brand-new database: create_all already has the full schema
    existing = {c["name"] for c in inspector.get_columns("users")}
    statements = []
    if "organization_id" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN organization_id INTEGER")
    if "role" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'owner'")
    # The quotes table predates client_id/site_address/status (added when
    # the CRM UI landed) -- same in-place upgrade, still zero data.
    if "quotes" in existing_tables:
        quote_cols = {c["name"] for c in inspector.get_columns("quotes")}
        if "client_id" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN client_id INTEGER")
        if "site_address" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN site_address VARCHAR")
        if "status" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN status VARCHAR DEFAULT 'draft'")
    if statements:
        with bind.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
