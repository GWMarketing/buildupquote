"""PostgreSQL connection for the BuildUpQuote FastAPI deployment.

DATABASE_URL is read from the environment (docker-compose sets it on the
`web` service as postgresql://app_user:app_password@db:5432/buildupquote_db).
The fallback uses the same credentials against localhost so local
development against a local postgres works out of the box.
"""
import os

from sqlalchemy import create_engine
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
