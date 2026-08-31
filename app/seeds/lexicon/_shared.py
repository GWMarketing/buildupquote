"""Shared generators + seed pipeline for the multi-trade lexicon.

Each trade module (app/seeds/lexicon/<trade>.py) exports FAMILIES: compact
canonical terms with spoken aliases, units, definitions and REAL sibling
variants (sizes / materials / lengths). build_rows() expands every family
into full lexicon rows -- one per canonical/variant -- and fills the derived
fields:

  * common_misspellings_typos -- deterministic phonetic/acoustic typo
    generator (drop letters, c->k, ph->f, doubled consonants, ...).
  * phonetic_respelling / ipa_pronunciation -- hand-written overrides win
    (brands like Uponor, SharkBite, USG, Square D); otherwise a small
    construction-word pronunciation table with a documented fallback.
  * search_vector -- lowercased corpus of canonical + aliases + typos for
    the Postgres tsvector/GIN index and the SQLite contains fallback.

seed_trade_lexicon() upserts idempotently keyed on (trade, term) and
converges changed fields on every startup.
"""
import re
import uuid as _uuid

from sqlalchemy.orm import Session

from app import models

# ---------------------------------------------------------------------------
# Deterministic misspelling / acoustic-typo generator
# ---------------------------------------------------------------------------
_PHONETIC_SWAPS = [
    ("ph", "f"), ("ck", "k"), ("sh", "ch"), ("ch", "sh"), ("qu", "kw"),
    ("x", "ks"), ("ee", "ea"), ("oo", "u"), ("ou", "ow"), ("gh", ""),
    ("ss", "s"), ("ll", "l"), ("tt", "t"), ("dd", "d"), ("ff", "f"),
    ("rr", "r"), ("nn", "n"), ("mm", "m"),
]

# Whole-word acoustic errors (what STT usually returns for common speech).
_WORD_ACOUSTIC = {
    "drywall": ["dry wall", "dryall", "dirwall"],
    "sheetrock": ["sheet rock", "cheat rock"],
    "studs": ["studz"],
    "receptacle": ["recepticle", "receptekle"],
    "conduit": ["condoot"],
    "sillcock": ["silcock", "sylcock", "sill cock"],
    "valve": ["valv"],
}


def _misspell(word: str) -> list:
    """Deterministic typo variants for a single word."""
    out = []
    low = word.lower()
    for f, r in _PHONETIC_SWAPS:
        if f in low:
            out.append(low.replace(f, r, 1))
    if len(low) > 4 and low not in out:
        out.append(low[:-1])  # dropped final letter
    seen = {low, word}
    return [v for v in out if v not in seen][:6]


def generate_misspellings(term: str, aliases: list) -> list:
    """Every word of the term and its aliases contributes typo variants, plus
    whole-word acoustic errors for known speech words. Acoustic (STT-relevant)
    errors are collected first and the list is capped -- deterministic order."""
    words = re.split(r"[^a-zA-Z]+", term) + [
        w for a in aliases or [] for w in re.split(r"[^a-zA-Z]+", a)
    ]
    typos = []
    seen = set()
    # Pass 1: whole-word acoustic errors (the STT-critical slang/mishears).
    for w in words:
        wl = w.lower()
        if not wl or len(wl) < 3:
            continue
        for t in _WORD_ACOUSTIC.get(wl, []):
            if t not in seen:
                seen.add(t)
                typos.append(t)
    # Pass 2: phonetic swap typos.
    for w in words:
        wl = w.lower()
        if not wl or len(wl) < 3:
            continue
        for t in _misspell(w):
            if t not in seen:
                seen.add(t)
                typos.append(t)
    return typos[:12]
# ---------------------------------------------------------------------------
# Phonetic respelling + IPA (hand-written wins; table + fallback otherwise)
# ---------------------------------------------------------------------------
_WORD_PHONETICS = {
    "drywall": ("DRY-wall", "/ˈdraɪˌwɔːl/"),
    "wallboard": ("WALL-bord", "/ˈwɔːlbɔːrd/"),
    "sheetrock": ("SHEET-rok", "/ˈʃiːtrɒk/"),
    "gypsum": ("JIP-sum", "/ˈdʒɪpsəm/"),
    "stud": ("STUD", "/stʌd/"),
    "studs": ("STUDS", "/stʌdz/"),
    "joist": ("JOYST", "/dʒɔɪst/"),
    "joists": ("JOYSTS", "/dʒɔɪsts/"),
    "rafter": ("RAF-ter", "/ˈræftər/"),
    "raffers": ("RAF-terz", "/ˈræftərz/"),
    "truss": ("TRUS", "/trʌs/"),
    "trusses": ("TRUS-ez", "/ˈtrʌsɪz/"),
    "shingle": ("SHING-gul", "/ˈʃɪŋɡəl/"),
    "shingles": ("SHING-gulz", "/ˈʃɪŋɡəlz/"),
    "underlayment": ("UN-der-lay-ment", "/ˈʌndərˌleɪmənt/"),
    "flashing": ("FLASH-ing", "/ˈflæʃɪŋ/"),
    "conduit": ("KON-doo-it", "/ˈkɒndjuːɪt/"),
    "receptacle": ("re-SEP-tuh-kul", "/rɪˈsɛptəkəl/"),
    "receptacles": ("re-SEP-tuh-kulz", "/rɪˈsɛptəkəlz/"),
    "breaker": ("BRAY-ker", "/ˈbreɪkər/"),
    "panel": ("PAN-ul", "/ˈpænəl/"),
    "valve": ("VALV", "/vælv/"),
    "valves": ("VALVZ", "/vælvz/"),
    "plumbing": ("PLUM-ing", "/ˈplʌmɪŋ/"),
    "concrete": ("KON-kreet", "/ˈkɒŋkriːt/"),
    "foundation": ("foun-DAY-shun", "/faʊnˈdeɪʃən/"),
    "insulation": ("in-suh-LAY-shun", "/ˌɪnsəˈleɪʃən/"),
    "primer": ("PRY-mer", "/ˈpraɪmər/"),
    "sillcock": ("SIL-kok", "/ˈsɪlkɒk/"),
    "hose": ("HOZE", "/hoʊz/"),
    "bibb": ("BIB", "/bɪb/"),
    "pipe": ("PIPE", "/paɪp/"),
    "water": ("WAW-ter", "/ˈwɔːtər/"),
    "heater": ("HEE-ter", "/ˈhiːtər/"),
    "tank": ("TANK", "/tæŋk/"),
    "faucet": ("FAW-set", "/ˈfɔːsɪt/"),
    "toilet": ("TOY-let", "/ˈtɔɪlɪt/"),
    "sink": ("SINK", "/sɪŋk/"),
    "shower": ("SHOW-er", "/ˈʃaʊər/"),
    "tub": ("TUB", "/tʌb/"),
    "pump": ("PUMP", "/pʌmp/"),
    "drain": ("DRAYN", "/dreɪn/"),
    "supply": ("suh-PLY", "/səˈplaɪ/"),
    "copper": ("KOP-er", "/ˈkɒpər/"),
    "brass": ("BRAS", "/bræs/"),
    "gas": ("GAS", "/ɡæs/"),
    "electric": ("ee-LEK-trik", "/ɪˈlɛktrɪk/"),
    "roof": ("ROOF", "/ruːf/"),
    "wall": ("WAWL", "/wɔːl/"),
    "floor": ("FLOR", "/flɔːr/"),
    "beam": ("BEEM", "/biːm/"),
    "nail": ("NAYL", "/neɪl/"),
    "screw": ("SKROO", "/skruː/"),
    "bolt": ("BOLT", "/boʊlt/"),
    "plate": ("PLAYT", "/pleɪt/"),
    "gutter": ("GUT-er", "/ˈɡʌtər/"),
    "vent": ("VENT", "/vɛnt/"),
    "duct": ("DUKT", "/dʌkt/"),
    "furnace": ("FER-nis", "/ˈfɜːrnɪs/"),
    "paint": ("PAYNT", "/peɪnt/"),
    "primer": ("PRY-mer", "/ˈpraɪmər/"),
    "joint": ("JOYNT", "/dʒɔɪnt/"),
    "tape": ("TAYP", "/teɪp/"),
    "wire": ("WIRE", "/waɪr/"),
    "cable": ("KAY-bul", "/ˈkeɪbəl/"),
    "switch": ("SWICH", "/swɪtʃ/"),
    "outlet": ("OWT-let", "/ˈaʊtlɛt/"),
    "light": ("LITE", "/laɪt/"),
    "panel": ("PAN-ul", "/ˈpænəl/"),
    "metal": ("MET-ul", "/ˈmɛtəl/"),
    "plastic": ("PLAS-tik", "/ˈplæstɪk/"),
    "shingle": ("SHING-gul", "/ˈʃɪŋɡəl/"),
    "siding": ("SIDE-ing", "/ˈsaɪdɪŋ/"),
}

# Unit/measure tokens that carry no pronunciation ("8'", "100 ft", "10 ft").
_STOP_TOKENS = {
    "ft", "in", "x", "ea", "ct", "pk", "box", "roll", "tube", "can", "jar",
    "lb", "lbs", "oz", "gal", "sq", "sf", "lf", "dia", "psi", "hp", "in",
}


def _respell_heuristic(word: str) -> str:
    """Fallback respelling: split into syllables, uppercase the stressed
    (first non-initial) syllable. Documented as approximate."""
    low = word.lower()
    syl = re.findall(r"[^aeiou]*[aeiou]+[^aeiou]*", low) or [low]
    if len(syl) > 1:
        syl[1] = syl[1].upper()
    return "-".join(syl).upper() if len(syl) == 1 else "-".join(syl)


def _ipa_heuristic(word: str) -> str:
    low = word.lower()
    return "/" + re.sub(r"e$", "ə", re.sub(r"oo", "uː", low)) + "/"


def _phonetics_for(term: str, overrides: dict | None) -> tuple:
    """(respelling, ipa) for a canonical term. Overrides (brands) win; then a
    per-word table; then the documented heuristic. Dimension tokens (2x4,
    8') carry no pronunciation and are skipped."""
    if overrides and overrides.get("phonetic"):
        return overrides["phonetic"], overrides.get("ipa") or ""
    parts = re.split(r"[^a-zA-Z]+", term)
    words = [w for w in parts if len(w) >= 2 and w.lower() not in _STOP_TOKENS]
    res = []
    ipa = []
    for w in words:
        r, p = _WORD_PHONETICS.get(w.lower(), (_respell_heuristic(w), _ipa_heuristic(w)))
        res.append(r)
        ipa.append(p)
    return " ".join(res), " ".join(ipa)

# ---------------------------------------------------------------------------
# Family -> rows expansion + idempotent seed
# ---------------------------------------------------------------------------


def build_rows(families: list, default_trade: str) -> list:
    """Expand curated families into full lexicon row dicts.

    Two expansion modes:
      * plain: `canonical` + optional `variants` (each a {canonical, aliases}).
      * matrix: `canonical` + `suffixes` (+ optional `lengths`) generate real
        sibling terms ("SPF Stud 2x4 x 8'"). `suffix_aliases` adds per-suffix
        aliases. This is how families reach real estimating breadth.
    """
    rows = []
    for fam in families:
        trade = fam.get("trade", default_trade)
        canonical = fam["canonical"]
        aliases = list(fam.get("aliases") or [])
        variants = fam.get("variants") or []
        suffixes = fam.get("suffixes") or []
        lengths = fam.get("lengths") or []
        unit = fam.get("unit", "EA")
        definition = fam.get("definition", "")
        phon = fam.get("phonetic"), fam.get("ipa")
        suffix_aliases = fam.get("suffix_aliases") or {}

        if suffixes:
            names = []
            name_aliases = {}
            for suffix in suffixes:
                if lengths:
                    for length in lengths:
                        nm = f"{canonical} {suffix} x {length}"
                        names.append(nm)
                        name_aliases[nm] = list(aliases) + list(suffix_aliases.get(suffix, []))
                else:
                    nm = f"{canonical} {suffix}"
                    names.append(nm)
                    name_aliases[nm] = list(aliases) + list(suffix_aliases.get(suffix, []))
        else:
            names = [canonical] + [v["canonical"] for v in variants]
            name_aliases = {}
            for v in variants:
                name_aliases[v["canonical"]] = list(aliases) + list(v.get("aliases") or [])

        for name in names:
            v_aliases = list(aliases) if suffixes else list(name_aliases.get(name, aliases))
            typos = generate_misspellings(name, v_aliases)
            corpus = " ".join(
                [name.lower()] + [a.lower() for a in v_aliases] + list(typos)
            )
            # Hand-written phonetics (brands) apply to every row in the family
            # -- a 1/2" SharkBite is still pronounced "SHARK-bite".
            if phon[0] or phon[1]:
                resp, ipa = phon[0] or "", phon[1] or ""
            else:
                resp, ipa = _phonetics_for(name, None)
            rows.append({
                "trade": trade,
                "term": name,
                "aliases": v_aliases or None,
                "default_unit": unit,
                "uuid": str(_uuid.uuid4()),
                "phonetic_respelling": resp,
                "ipa_pronunciation": ipa,
                "common_misspellings_typos": typos,
                "definition_and_use": definition,
                "search_vector": corpus,
            })
    return rows


def _load_providers():
    from app.seeds.lexicon import (  # noqa: PLC0415
        carpentry_finish, concrete_masonry, drywall_paint, electrical, framing,
        general, hvac, plumbing, roofing_siding, tiling,
    )

    return [
        # New canonical trades (each ~300 rows).
        (framing.FAMILIES, framing.TRADE),
        (plumbing.FAMILIES, plumbing.TRADE),
        (electrical.FAMILIES, electrical.TRADE),
        (hvac.FAMILIES, hvac.TRADE),
        (drywall_paint.FAMILIES, drywall_paint.TRADE),
        (roofing_siding.FAMILIES, roofing_siding.TRADE),
        (concrete_masonry.FAMILIES, concrete_masonry.TRADE),
        # The app's legacy trade names, filled to the same ~300+ floor so the
        # app's existing trade categories and voice recognition recognize
        # every trade equally. Overlapping trades reuse the matching content
        # (accepted redundancy -- consolidated in a later taxonomy pass).
        (framing.FAMILIES + carpentry_finish.FAMILIES, "carpentry"),
        (drywall_paint.FAMILIES, "drywall"),
        (electrical.FAMILIES, "electrical"),
        (plumbing.FAMILIES, "plumbing"),
        (general.FAMILIES, general.TRADE),
        (tiling.FAMILIES, tiling.TRADE),
    ]


def seed_trade_lexicon(db: Session, providers: list | None = None) -> int:
    """Idempotent upsert of every trade module's families (created + updated
    row count). Safe to run on every startup."""
    providers = providers or _load_providers()
    changed = 0
    for families, default_trade in providers:
        for row in build_rows(families, default_trade):
            exists = (
                db.query(models.TradeLexicon)
                .filter_by(trade=row["trade"], term=row["term"])
                .first()
            )
            if exists is None:
                db.add(models.TradeLexicon(**row))
                changed += 1
                continue
            dirty = False
            for field in ("aliases", "default_unit", "phonetic_respelling",
                          "ipa_pronunciation", "common_misspellings_typos",
                          "definition_and_use", "search_vector"):
                if row.get(field) != getattr(exists, field):
                    setattr(exists, field, row.get(field))
                    dirty = True
            if dirty:
                changed += 1
    db.commit()
    return changed


def serialize_row(row) -> dict:
    """TradeLexicon ORM row -> the API response shape (spec field names)."""
    return {
        "id": row.id,
        "uuid": row.uuid,
        "trade_category": row.trade,
        "canonical_term": row.term,
        "spoken_aliases": row.aliases or [],
        "phonetic_respelling": row.phonetic_respelling or "",
        "ipa_pronunciation": row.ipa_pronunciation or "",
        "common_misspellings_typos": row.common_misspellings_typos or [],
        "unit_of_measure": row.default_unit or "EA",
        "definition_and_use": row.definition_and_use or "",
    }

