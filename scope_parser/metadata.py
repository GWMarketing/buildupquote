"""Pulls claim/policy metadata (insured name, claim number, policy number,
property address, etc.) out of the header/cover-sheet text.

Carriers print several "Label: Value" pairs on one physical line (e.g.
"Insured: GRAHAM WILLIAMS Home: (281) 235-5775"), so this can't be a
simple one-match-per-line regex -- it scans for every known label on a
line and slices the value between consecutive matches.
"""
import re

_LABELS = [
    "Insured", "Customer", "Home", "Cell", "E-mail", "Property",
    r"Claim Rep\.", "Estimator", "Business", "Claim Number", "Policy Number",
    "Type of Loss", "Insurance Company", "Company", "Date Contacted",
    "Date of Loss", "Date Received", "Date Inspected", "Date Entered",
    "Date Completed", "Price List", "Estimate", "Position",
    "Claim Professional", "Deductible", "Policy Limit",
    # Labels used by Symbility/Cotality carrier documents, which name the
    # same fields differently ("CLAIM NO.", "Policy No.", "Type of Claim")
    # and print the loss address separately from the mailing address.
    r"CLAIM NO\.", r"Policy No\.", "Type of Claim", "Loss address",
    "Pricing Database", "Claim Rep",
    # Not fields worth keeping in themselves -- they are here as
    # BOUNDARIES. Several carriers print two or three label/value pairs on
    # one physical line, and a value only stops where the next recognised
    # label begins. Without "Address" in this list, a date of loss came
    # out as "05/06/2025 Address: 5009 Andalusia Trl".
    "Address", "Contact Name", "Contact Phone", "Contact Email",
    "Home phone", "Business phone", "Mobile phone", "Policy Type",
    "Underwriting Co", "Effective from", "Assigned", "Contacted",
    "Inspected", "Estimated", "Fax", "Phone", "License",
]
# Case-insensitive because carriers don't agree on case: Xactimate prints
# "Insured:" and "Claim Number:", while Symbility/Liberty Mutual prints
# "INSURED:" and "CLAIM NO.:" in small caps. Without this, a real claim
# number came out as "060929297 INSURED: Tammy Cobb" -- the value ran on
# through the next label because the next label wasn't recognised.
_LABEL_RE = re.compile(r"(?P<label>" + "|".join(_LABELS) + r"):", re.IGNORECASE)

_CANONICAL = {
    "insured": "insured_name", "customer": "insured_name",
    "claim rep.": "claim_rep", "claim professional": "claim_rep",
    "estimator": "estimator", "claim number": "claim_number",
    "policy number": "policy_number", "type of loss": "type_of_loss",
    "insurance company": "insurance_company", "company": "company",
    "date of loss": "date_of_loss", "date completed": "date_completed",
    "price list": "price_list", "estimate": "estimate_name",
    "property": "property_address", "e-mail": "email", "cell": "cell_phone",
    "deductible": "deductible", "policy limit": "policy_limit",
    "claim no": "claim_number", "policy no": "policy_number",
    "type of claim": "type_of_loss", "loss address": "property_address",
    "pricing database": "price_list", "claim rep": "claim_rep",
    # The mailing address is NOT the loss address; keeping them apart
    # matters, because the proposal is written about the damaged property.
    "address": "mailing_address",
}


def _slug(label):
    key = label.strip().lower().rstrip(".")
    return _CANONICAL.get(key, re.sub(r"[^a-z0-9]+", "_", key).strip("_"))


def extract_metadata(lines):
    """Returns a plain dict of canonical-key -> value. Only the first
    value seen for a given key is kept (the cover sheet is authoritative;
    later repeated blocks -- e.g. a guide/example page -- should not
    clobber it)."""
    fields = {}
    property_lines = []
    capturing_property = False

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            capturing_property = False
            continue
        matches = list(_LABEL_RE.finditer(stripped))
        if not matches:
            if capturing_property and len(stripped.split()) <= 8:
                property_lines.append(stripped)
            continue
        capturing_property = False
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(stripped)
            value = stripped[start:end].strip()
            key = _slug(m.group("label"))
            if not value:
                continue
            if key == "property_address" and "property_address" not in fields:
                property_lines = [value]
                capturing_property = True
                continue
            fields.setdefault(key, value)

    if property_lines and "property_address" not in fields:
        fields["property_address"] = ", ".join(property_lines)
    return fields


# ---------------------------------------------------------------------
# PDF *container* metadata -- not text printed on the page at all, but
# the file's own /Info dictionary (what "Get Info" on macOS, or Adobe's
# Document Properties, shows). Every PDF-writing library stamps a
# Creator/Producer string in here, and Xactimate's own PDF export
# happens to write its own name and exact version -- e.g.
# "Xactimate 24.4.1001.1" -- straight into the file. That's a far more
# reliable "which program wrote this" signal than guessing from column
# headers or section-total phrasing (see the "Beyond Xactimate"
# reference doc), when it's present -- worth surfacing to the
# contractor and, longer-term, using as the first check before anything
# format-specific in the pipeline runs.
#
# This can only ever be populated from an actual PDF file (pdfplumber's
# `.metadata`), never from parse_text()'s plain-text fixtures -- there
# is no file for that text to have come from. Fields simply don't show
# up on a text-only parse, same as a needs_review-style "nothing found".
# ---------------------------------------------------------------------

_VERSION_RE = re.compile(r"^(.*?)\s+(\d+(?:\.\d+)+)$")
_PDF_DATE_RE = re.compile(r"^D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


def parse_creator(creator: str):
    """"Xactimate 24.4.1001.1" -> ("Xactimate", "24.4.1001.1"). A creator
    string with no trailing version number (e.g. a generic PDF library
    that doesn't stamp one) returns the whole string as the program name
    and None for the version, rather than guessing where one might be."""
    if not creator or not creator.strip():
        return None, None
    m = _VERSION_RE.match(creator.strip())
    if m:
        return m.group(1).strip(), m.group(2)
    return creator.strip(), None


def parse_pdf_date(raw: str):
    """PDF dates look like "D:20240722183217-05'00'" (year, month, day,
    hour, minute, second, then a timezone offset this doesn't bother
    parsing). Returns a plain "YYYY-MM-DD HH:MM:SS" string, or the raw
    value unchanged if it doesn't match that shape -- never raises, and
    never silently drops a date just because it's an unusual format."""
    if not raw:
        return None
    m = _PDF_DATE_RE.match(raw)
    if not m:
        return raw
    year, month, day, hour, minute, second = m.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second}"


def fields_from_pdf_info(info: dict) -> dict:
    """info: whatever pdfplumber's `pdf.metadata` returned (a plain dict,
    possibly empty) for the file actually uploaded. Returns canonical
    fields in the same shape extract_metadata() produces, so app.py's
    existing _best() helper works on these exactly like any other
    metadata field -- no special-casing needed on the display side."""
    fields = {}
    program, version = parse_creator(info.get("Creator"))
    if program:
        fields["source_program"] = program
    if version:
        fields["source_program_version"] = version
    created = parse_pdf_date(info.get("CreationDate"))
    if created:
        fields["pdf_created_at"] = created
    title = (info.get("Title") or "").strip()
    if title and title.lower() != "untitled":
        fields["pdf_title"] = title
    return fields
