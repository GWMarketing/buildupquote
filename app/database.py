"""PostgreSQL connection for the BuildUpQuote FastAPI deployment.

DATABASE_URL is read from the environment (docker-compose sets it on the
`web` service as postgresql://app_user:app_password@db:5432/buildupquote_db).
The fallback uses the same credentials against localhost so local
development against a local postgres works out of the box.
"""
import os
import uuid

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
    _needs_public_uuid_backfill = False
    if "organization_id" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN organization_id INTEGER")
    if "role" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'owner'")
    if "job_title" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN job_title VARCHAR")
    if "google_access_token" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN google_access_token VARCHAR")
    if "google_refresh_token" not in existing:
        statements.append("ALTER TABLE users ADD COLUMN google_refresh_token VARCHAR")
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
        if "tax_rate_percent" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN tax_rate_percent NUMERIC(5, 2)")
        # Public 1-click approval (share link + digital sign-off). The model
        # defaults public_uuid for new rows; existing rows get backfilled below.
        if "public_uuid" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN public_uuid VARCHAR(36)")
            _needs_public_uuid_backfill = True
        if "client_signature" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN client_signature TEXT")
        if "signed_by" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN signed_by VARCHAR")
        if "signer_email" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN signer_email VARCHAR")
        if "signer_ip" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN signer_ip VARCHAR")
        if "signer_user_agent" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN signer_user_agent TEXT")
        if "accepted_at" not in quote_cols:
            statements.append("ALTER TABLE quotes ADD COLUMN accepted_at TIMESTAMP WITH TIME ZONE")
    # Organization profile fields landed after the first organizations
    # table was created -- same idempotent in-place upgrade. `bio` superseded
    # the old `description` column, so existing databases get an in-place
    # RENAME rather than a second column.
    if "organizations" in existing_tables:
        org_cols = {c["name"] for c in inspector.get_columns("organizations")}
        if "bio" not in org_cols:
            if "description" in org_cols:
                statements.append("ALTER TABLE organizations RENAME COLUMN description TO bio")
            else:
                statements.append("ALTER TABLE organizations ADD COLUMN bio TEXT")
        for col, ddl in (
            ("phone", "ALTER TABLE organizations ADD COLUMN phone VARCHAR"),
            ("address", "ALTER TABLE organizations ADD COLUMN address VARCHAR"),
            ("tax_id", "ALTER TABLE organizations ADD COLUMN tax_id VARCHAR"),
            ("default_payment_terms", "ALTER TABLE organizations ADD COLUMN default_payment_terms VARCHAR"),
            ("currency_symbol", "ALTER TABLE organizations ADD COLUMN currency_symbol VARCHAR"),
            ("logo_url", "ALTER TABLE organizations ADD COLUMN logo_url VARCHAR"),
            ("website", "ALTER TABLE organizations ADD COLUMN website VARCHAR"),
            ("email", "ALTER TABLE organizations ADD COLUMN email VARCHAR"),
            ("license_number", "ALTER TABLE organizations ADD COLUMN license_number VARCHAR"),
        ):
            if col not in org_cols:
                statements.append(ddl)
    # Parametric assemblies gained a `calculator` column (hand-written
    # Python calculators that supersede the formula-string components).
    if "parametric_assemblies" in existing_tables:
        pa_cols = {c["name"] for c in inspector.get_columns("parametric_assemblies")}
        if "calculator" not in pa_cols:
            statements.append("ALTER TABLE parametric_assemblies ADD COLUMN calculator VARCHAR")
    if statements:
        with bind.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    # Backfill public_uuid for rows that predate the column (the model's
    # default only applies to new inserts), and lock the column down with a
    # unique index so the share links stay unguessable.
    if _needs_public_uuid_backfill:
        with bind.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_quotes_public_uuid ON quotes (public_uuid)"))
            rows = conn.execute(text("SELECT id FROM quotes WHERE public_uuid IS NULL")).fetchall()
            for (qid,) in rows:
                conn.execute(
                    text("UPDATE quotes SET public_uuid = :u WHERE id = :id"),
                    {"u": str(uuid.uuid4()), "id": qid},
                )
