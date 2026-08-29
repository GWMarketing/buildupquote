"""Promote a user to platform admin (one-off).

Usage:
    ./venv/bin/python scripts/make_admin.py someone@example.com

Uses the same DATABASE_URL as the app (env var; defaults to localhost
postgres). For the deployed instance, prefer the ADMIN_EMAILS env var in the
VPS .env -- it grants admin at every startup and is idempotent.

Works from any working directory: the repo root is added to sys.path so
`from app import models` resolves even when run via `docker compose exec web
python scripts/make_admin.py ...` (where the script's own directory, not the
repo root, is what Python puts on the import path).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    email = sys.argv[1].strip().lower()

    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == email).first()
        if user is None:
            print(f"No user found with email {email!r}.")
            sys.exit(1)
        user.is_admin = True
        db.add(user)
        db.commit()
        print(f"{email} is now a platform admin.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
