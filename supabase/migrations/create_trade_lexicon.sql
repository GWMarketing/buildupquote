-- ---------------------------------------------------------------------------
-- create_trade_lexicon.sql
-- Multi-trade lexicon for BuildUpQuote: 7 trades x 300+ terms with spoken
-- aliases, phonetics (respelling + IPA), misspellings, definitions, and
-- Postgres full-text + trigram search.
--
-- This is the Supabase/PostgreSQL DDL. The FastAPI app creates the same
-- table via SQLAlchemy (trade_lexicon) and keeps the same search function
-- warm in its lifespan; this file is for databases managed outside the app
-- (e.g. a Supabase project). It is idempotent.
--
-- Usage:  psql "$DATABASE_URL" -f supabase/migrations/create_trade_lexicon.sql
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

-- spec-shaped columns (canonical_term / spoken_aliases / ...). uuid is the
-- stable public identifier consumed by the static JS voice lexicon.
CREATE TABLE IF NOT EXISTS trade_lexicon (
    uuid                        UUID PRIMARY KEY,
    trade_category              TEXT NOT NULL,
    canonical_term              TEXT NOT NULL,
    spoken_aliases              JSONB DEFAULT '[]',
    phonetic_respelling         TEXT,
    ipa_pronunciation           TEXT,
    common_misspellings_typos   JSONB DEFAULT '[]',
    unit_of_measure             TEXT DEFAULT 'EA',
    definition_and_use          TEXT,
    search_vector_tsv           tsvector GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(canonical_term, '') || ' ' ||
            coalesce(jsonb_array_to_text_array(spoken_aliases::jsonb)::text, '') || ' ' ||
            coalesce(jsonb_array_to_text_array(common_misspellings_typos::jsonb)::text, '')
        )
    ) STORED,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (trade_category, canonical_term)
);

-- Full-text search over the generated tsvector.
CREATE INDEX IF NOT EXISTS ix_trade_lexicon_search_tsv
    ON trade_lexicon USING gin (search_vector_tsv);

-- Trigram index for fuzzy spelling / STT-misheard variants.
CREATE INDEX IF NOT EXISTS ix_trade_lexicon_canonical_trgm
    ON trade_lexicon USING gin (canonical_term gin_trgm_ops);

-- One function for every search path: trigram similarity against the
-- canonical term, plus full-text ranking over aliases/misspellings.
CREATE OR REPLACE FUNCTION search_trade_lexicon(
    q TEXT,
    max_rows INT DEFAULT 25,
    trade_filter TEXT DEFAULT NULL
)
RETURNS TABLE (
    uuid TEXT,
    trade_category TEXT,
    canonical_term TEXT,
    spoken_aliases JSONB,
    phonetic_respelling TEXT,
    ipa_pronunciation TEXT,
    common_misspellings_typos JSONB,
    unit_of_measure TEXT,
    definition_and_use TEXT,
    match_score DOUBLE PRECISION
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        l.uuid::TEXT,
        l.trade_category,
        l.canonical_term,
        l.spoken_aliases,
        coalesce(l.phonetic_respelling, ''),
        coalesce(l.ipa_pronunciation, ''),
        l.common_misspellings_typos,
        coalesce(l.unit_of_measure, 'EA'),
        coalesce(l.definition_and_use, ''),
        GREATEST(
            similarity(l.canonical_term, q),
            ts_rank(l.search_vector_tsv, websearch_to_tsquery('english', q))::double precision
        ) AS match_score
    FROM trade_lexicon l
    WHERE (trade_filter IS NULL OR l.trade_category = trade_filter)
      AND (
            l.canonical_term ILIKE '%' || q || '%'
         OR l.spoken_aliases::text ILIKE '%' || q || '%'
         OR l.common_misspellings_typos::text ILIKE '%' || q || '%'
         OR similarity(l.canonical_term, q) > 0.2
         OR l.search_vector_tsv @@ websearch_to_tsquery('english', q)
      )
    ORDER BY match_score DESC, l.canonical_term
    LIMIT max_rows;
END;
$$;
