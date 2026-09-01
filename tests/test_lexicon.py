"""Multi-trade lexicon tests: seed coverage, field completeness, the
/api/lexicon/search endpoint, and the RFC 4180 CSV export.

Runs against a throwaway SQLite database (the app's lifespan seeds the
lexicon on startup, mirroring production).
"""
import csv
import json
import os
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), "test_lexicon.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["SECRET_KEY"] = "test-secret-key"
for suffix in ("", "-journal", "-wal", "-shm"):
    if os.path.exists(_DB + suffix):
        os.remove(_DB + suffix)

from fastapi.testclient import TestClient  # noqa: E402

import fastapi_app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.seeds.lexicon._shared import _load_providers, build_rows  # noqa: E402
from sqlalchemy import text  # noqa: E402


class LexiconSeedTestCase(unittest.TestCase):
    """Coverage + field completeness of the seed expansion itself."""

    def test_every_trade_has_300_plus_rows(self):
        counts = {}
        for families, trade in _load_providers():
            counts[trade] = counts.get(trade, 0) + len(build_rows(families, trade))
        for trade, n in counts.items():
            self.assertGreaterEqual(
                n, 300, f"{trade} has only {n} rows (spec floor is 300)")
        self.assertGreaterEqual(sum(counts.values()), 3600)

    def test_derived_fields_are_populated(self):
        for families, trade in _load_providers():
            for row in build_rows(families, trade):
                self.assertTrue(row["uuid"], row["term"])
                self.assertTrue(row["phonetic_respelling"], row["term"])
                self.assertTrue(row["ipa_pronunciation"], row["term"])
                self.assertIsNotNone(row["common_misspellings_typos"], row["term"])
                self.assertTrue(row["definition_and_use"], row["term"])
                self.assertTrue(row["search_vector"], row["term"])

    def test_misspellings_are_lowercased_and_deduped(self):
        for families, trade in _load_providers():
            for row in build_rows(families, trade):
                typos = row["common_misspellings_typos"]
                self.assertEqual(typos, sorted(set(typos), key=typos.index), row["term"])
                for t in typos:
                    self.assertEqual(t, t.lower(), row["term"])

    def test_build_is_deterministic(self):
        families, trade = _load_providers()[0]
        a = build_rows(families, trade)
        b = build_rows(families, trade)
        for ra, rb in zip(a, b):
            for field in ("trade", "term", "aliases", "default_unit",
                          "phonetic_respelling", "ipa_pronunciation",
                          "common_misspellings_typos", "definition_and_use",
                          "search_vector"):
                self.assertEqual(ra[field], rb[field], ra["term"])

    def test_voice_synonyms_targets_exist(self):
        from app.seeds.lexicon._voice_synonyms import VOICE_SYNONYMS
        known = {fam["canonical"]
                 for families, _ in _load_providers() for fam in families}
        for target in VOICE_SYNONYMS.values():
            self.assertIn(target, known)

class LexiconApiTestCase(unittest.TestCase):
    """The seeded DB + /api/lexicon/search endpoint."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(fastapi_app.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def register(self, email, org="Acme Roofing"):
        r = self.client.post("/api/auth/register", json={
            "email": email, "password": "pw12345678",
            "organization_name": org, "full_name": "Tester",
        })
        self.assertIn(r.status_code, (200, 201), r.text)
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def test_db_has_300_plus_per_trade(self):
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT trade, COUNT(*) AS n FROM trade_lexicon "
                "GROUP BY trade ORDER BY trade")).fetchall()
        finally:
            db.close()
        totals = {r[0]: r[1] for r in rows}
        expected = {fam_trade for families, fam_trade in _load_providers()}
        for trade in expected:
            self.assertGreaterEqual(
                totals.get(trade, 0), 300, f"{trade} has {totals.get(trade, 0)} rows")

    def test_search_returns_spec_shape(self):
        auth = self.register("lex@acme.com")
        r = self.client.get("/api/lexicon/search", params={"q": "silcock"}, headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["query"], "silcock")
        results = body["results"]
        self.assertTrue(results, r.text)
        hit = next((res for res in results if "Hose Bibb" in res["canonical_term"]), None)
        self.assertIsNotNone(hit, results)
        self.assertEqual(hit["trade_category"], "Plumbing")
        self.assertIn("sillcock", hit["spoken_aliases"])
        self.assertTrue(hit["phonetic_respelling"])
        self.assertTrue(hit["ipa_pronunciation"])
        self.assertTrue(hit["common_misspellings_typos"])
        self.assertTrue(hit["definition_and_use"])
        self.assertTrue(hit["uuid"])
        self.assertEqual(hit["unit_of_measure"], "EA")

    def test_search_matches_misspelling(self):
        auth = self.register("lex2@acme.com")
        r = self.client.get("/api/lexicon/search", params={"q": "sheet rock"}, headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        names = [res["canonical_term"] for res in r.json()["results"]]
        self.assertTrue(any("Gypsum Wallboard" in n for n in names), names)

    def test_search_trade_filter(self):
        auth = self.register("lex3@acme.com")
        r = self.client.get("/api/lexicon/search",
                            params={"q": "valve", "trade": "Plumbing"}, headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        results = r.json()["results"]
        self.assertTrue(results, r.text)
        for res in results:
            self.assertEqual(res["trade_category"], "Plumbing", res)

    def test_search_short_query_is_empty(self):
        auth = self.register("lex4@acme.com")
        r = self.client.get("/api/lexicon/search", params={"q": "a"}, headers=auth)
        self.assertEqual(r.json()["results"], [])

    def test_search_requires_auth(self):
        r = self.client.get("/api/lexicon/search", params={"q": "valve"})
        self.assertIn(r.status_code, (401, 403))


class LexiconCsvTestCase(unittest.TestCase):
    """RFC 4180 export from the seed definitions (no DB needed)."""

    def _export(self):
        import scripts.export_lexicon_csv as exporter
        rows = exporter._rows_from_seeds()
        tmp = os.path.join(tempfile.gettempdir(), "lexicon_export_test.csv")
        exporter.write_csv(tmp, rows)
        return tmp, rows

    def test_csv_round_trip(self):
        path, rows = self._export()
        self.assertTrue(path.endswith(".csv"))
        with open(path, "r", encoding="utf-8", newline="") as fh:
            raw = fh.read()
            self.assertIn("\r\n", raw)  # RFC 4180 CRLF
        with open(path, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, [
                "trade_category", "canonical_term", "spoken_aliases",
                "phonetic_respelling", "ipa_pronunciation",
                "common_misspellings_typos", "unit_of_measure",
                "definition_and_use",
            ])
            parsed = list(reader)
        self.assertEqual(len(parsed), len(rows))
        self.assertIsInstance(json.loads(parsed[0]["spoken_aliases"]), list)
        self.assertGreaterEqual(len(parsed), 3800)


class LexiconDedupeTestCase(unittest.TestCase):
    """Regression test for the production 2x-lexicon bug.

    `gunicorn -w 4` runs the lifespan seed in every worker and the old
    check-then-insert raced, so every new-trade lexicon was seeded twice
    (311 rows became 622). ensure_lexicon_unique() collapses the leftover
    duplicates (keeping the lowest id) and the UNIQUE index makes a
    re-occurrence impossible.
    """

    # Hand-built legacy schema WITHOUT the (trade, term) unique constraint,
    # exactly as the pre-fix production table was -- so the duplicate rows
    # that the racing seed actually produced can be inserted.
    _LEGACY_DDL = """
    CREATE TABLE trade_lexicon (
        id INTEGER NOT NULL,
        trade VARCHAR,
        term VARCHAR,
        aliases JSON,
        default_unit VARCHAR,
        uuid VARCHAR(36),
        phonetic_respelling TEXT,
        ipa_pronunciation TEXT,
        common_misspellings_typos JSON,
        definition_and_use TEXT,
        search_vector TEXT,
        PRIMARY KEY (id)
    )
    """

    def _fresh_db(self):
        import tempfile

        from sqlalchemy import create_engine, text  # noqa: F401
        from sqlalchemy.orm import sessionmaker

        from app.database import Base
        from app import models  # noqa: F401
        from app.seeds._upsert import ensure_lexicon_unique, upsert_row

        db_path = os.path.join(tempfile.gettempdir(), "test_lexicon_dedupe.db")
        for suffix in ("", "-journal", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(text(self._LEGACY_DDL))
        Base.metadata.create_all(bind=engine)  # adds the other tables
        Session = sessionmaker(bind=engine)
        return engine, Session()

    def test_dedupe_collapses_duplicate_rows_and_locks_the_key(self):
        from app import models
        from app.seeds._upsert import ensure_lexicon_unique

        engine, db = self._fresh_db()
        try:
            # The racing seed produced two identical rows per (trade, term).
            db.add_all([
                models.TradeLexicon(trade="general", term="labor", default_unit="hr"),
                models.TradeLexicon(trade="general", term="labor", default_unit="hr"),
                models.TradeLexicon(trade="tiling", term="grout", default_unit="lb"),
                models.TradeLexicon(trade="tiling", term="grout", default_unit="lb"),
            ])
            db.commit()
            self.assertEqual(db.query(models.TradeLexicon).count(), 4)

            ensure_lexicon_unique(db)

            rows = (db.query(models.TradeLexicon)
                    .order_by(models.TradeLexicon.id).all())
            self.assertEqual(len(rows), 2)  # one row per (trade, term)
            self.assertEqual(rows[0].id, 1)  # lowest id kept
            self.assertEqual(rows[0].term, "labor")
            self.assertEqual(rows[1].id, 3)
            self.assertEqual(rows[1].term, "grout")

            # The UNIQUE index now blocks a fresh duplicate outright.
            db.add(models.TradeLexicon(trade="general", term="labor"))
            with self.assertRaises(Exception):
                db.commit()
            db.rollback()

            # A second ensure pass is a no-op and never raises.
            ensure_lexicon_unique(db)
        finally:
            db.close()
            engine.dispose()
            for suffix in ("", "-journal", "-wal", "-shm"):
                p = os.path.join(tempfile.gettempdir(), "test_lexicon_dedupe.db") + suffix
                if os.path.exists(p):
                    os.remove(p)

    def test_atomic_upsert_converges_instead_of_duplicating(self):
        from app import models
        from app.seeds._upsert import ensure_lexicon_unique, upsert_row

        engine, db = self._fresh_db()
        try:
            db.add_all([
                models.TradeLexicon(trade="general", term="labor", default_unit="hr"),
                models.TradeLexicon(trade="general", term="labor", default_unit="hr"),
            ])
            db.commit()
            ensure_lexicon_unique(db)

            # Upserting the same key converges (changed=0) rather than
            # inserting a third row or throwing.
            _, changed = upsert_row(
                db, models.TradeLexicon,
                {"trade": "general", "term": "labor", "default_unit": "hr"},
                conflict_cols=["trade", "term"],
                update_cols=["default_unit"],
            )
            db.commit()
            self.assertEqual(changed, 0)
            self.assertEqual(db.query(models.TradeLexicon).count(), 1)

            # A genuinely changed value converges and counts as changed.
            _, changed = upsert_row(
                db, models.TradeLexicon,
                {"trade": "general", "term": "labor", "default_unit": "each"},
                conflict_cols=["trade", "term"],
                update_cols=["default_unit"],
            )
            db.commit()
            self.assertEqual(changed, 1)
            self.assertEqual(
                db.query(models.TradeLexicon).filter_by(trade="general").first().default_unit,
                "each",
            )
        finally:
            db.close()
            engine.dispose()
            for suffix in ("", "-journal", "-wal", "-shm"):
                p = os.path.join(tempfile.gettempdir(), "test_lexicon_dedupe.db") + suffix
                if os.path.exists(p):
                    os.remove(p)


if __name__ == "__main__":
    unittest.main()

