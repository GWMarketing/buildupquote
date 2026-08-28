"""Parse contact data into client records: .vcf files, .csv files, and
free-form pasted lead text.

Parsers stay unit-testable the same way the workspace helpers are. Each
parser returns a list of dicts (or a single dict) shaped
{name, email, phone, site_address} -- missing fields are None and the caller
(app/routers/clients.py) decides dedupe + persistence.

vCard parsing uses `vobject` when available (real Apple/Android exports have
structured ADR and multi-valued fields), falling back to a small regex
parser so import keeps working dependency-free.
"""
import csv
import io
import re

try:
    import vobject
except ImportError:  # pragma: no cover -- dependency shipped in requirements.txt
    vobject = None

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# UK/IE/NA-prefixed numbers ("+44 7700 900123", "07700 900123") OR general
# 3-3-4 group formatting ("(555) 010-1234", "555-010-1234").
_PHONE_RE = re.compile(
    r"(?:\+?44[\s.\-]?|\+?1[\s.\-]?|0[\s.\-]?)(?:\d[\s.\-]?){9,12}"
    r"|(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"
)
# "123 Main St", "PO Box 4" ... -- a line that reads like an address.
_ADDRESS_RE = re.compile(r"^\s*(?:P\.?\s?O\.?\s?Box\s+)?\d{1,6}\s+\S", re.IGNORECASE)
_ADDRESS_PREFIXES = (
    "street", "road", "avenue", "ave", "blvd", "drive", "dr", "lane", "ln",
    "highway", "hwy", "crescent", "court", "ct", "terrace",
)
_NAME_LABEL_RE = re.compile(r"(?:Name|From|Client|Customer)\s*:\s*([^\n\r,]+)", re.IGNORECASE)
_ADDRESS_LABEL_RE = re.compile(r"(?:Address|Site|Location)\s*:\s*([^\n\r]+)", re.IGNORECASE)


def _clean(value):
    """Collapse whitespace; empty strings become None."""
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def normalize_phone(phone):
    """'(555) 010-1234' / '+44 7700 900123' -> '7700900123' (last 10 digits),
    so both representations of the same number dedupe together."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if digits else None


# ---------------------------------------------------------------------------
# Free-form lead text
# ---------------------------------------------------------------------------

def parse_lead_text(raw_text: str) -> dict:
    """Extract {name, email, phone, site_address} from a chunk of raw text
    (WhatsApp/SMS/email lead). Name and address are pulled from common
    labels when present, otherwise inferred from the first non-contact line
    and the first address-looking line."""
    text = raw_text or ""
    email_match = _EMAIL_RE.search(text)
    phone_match = _PHONE_RE.search(text)
    email = _clean(email_match.group(0)) if email_match else None
    phone = _clean(phone_match.group(0)) if phone_match else None

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_line = lines[0] if lines else ""

    name = None
    name_match = _NAME_LABEL_RE.search(text)
    if name_match:
        name = _clean(name_match.group(1))
    elif first_line and not _EMAIL_RE.search(first_line) \
            and not _PHONE_RE.search(first_line) and not _ADDRESS_RE.match(first_line) \
            and not first_line.lower().startswith(_ADDRESS_PREFIXES):
        name = first_line[:60]
    name = name or "New Lead"

    address = None
    address_match = _ADDRESS_LABEL_RE.search(text)
    if address_match:
        address = _clean(address_match.group(1))
    else:
        for line in lines:
            if _ADDRESS_RE.match(line) or line.lower().startswith(_ADDRESS_PREFIXES):
                address = line
                break

    return {"name": name, "email": email, "phone": phone, "site_address": address}

def parse_quick_text(text: str) -> list:
    """Free-form paste -> contacts. One contact per blank-line-separated
    block (each block goes through parse_lead_text). If no blank lines exist,
    every line is treated as its own contact."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    if not blocks:
        blocks = [line.strip() for line in (text or "").splitlines() if line.strip()]

    contacts = []
    for block in blocks:
        contact = parse_lead_text(block)
        if contact["name"] or contact["email"] or contact["phone"] or contact["site_address"]:
            contacts.append(contact)
    return contacts


# ---------------------------------------------------------------------------
# vCard (.vcf)
# ---------------------------------------------------------------------------

def _vobject_str(value) -> str:
    """vobject .value -> a clean string (lists of street lines join)."""
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v)
    return str(value)


def _parse_vcard_with_vobject(text: str) -> list:
    contacts = []
    for vcard in vobject.readComponents(text):  # noqa: F821 (guarded above)
        name = None
        if hasattr(vcard, "fn"):
            name = _clean(_vobject_str(vcard.fn.value))
        if not name and hasattr(vcard, "n"):
            n = vcard.n.value
            name = _clean(" ".join(p for p in (getattr(n, "given", None), getattr(n, "family", None)) if p))

        email = phone = None
        for key in ("email", "tel"):
            if not hasattr(vcard, key):
                continue
            values = getattr(vcard, key)
            if not isinstance(values, list):
                values = [values]
            for entry in values:
                value = _clean(_vobject_str(entry.value))
                if key == "email":
                    email = email or value
                else:
                    phone = phone or value

        address = None
        if hasattr(vcard, "adr"):
            adrs = getattr(vcard, "adr")
            if not isinstance(adrs, list):
                adrs = [adrs]
            for entry in adrs:
                adr = entry.value
                parts = []
                for attr in ("street", "city", "region", "code", "country"):
                    val = _clean(_vobject_str(getattr(adr, attr, None) or ""))
                    if val:
                        parts.append(val)
                if parts:
                    address = ", ".join(parts)
                    break

        if name or email or phone or address:
            contacts.append({
                "name": name, "email": email, "phone": phone, "site_address": address,
            })
    return contacts


def _parse_vcard_regex(text: str) -> list:
    """BEGIN:VCARD blocks -> [{name, email, phone, site_address}] (fallback)."""
    contacts = []
    for block in re.split(r"(?=BEGIN:VCARD)", text, flags=re.IGNORECASE):
        if "BEGIN:VCARD" not in block.upper():
            continue
        name = email = phone = address = None
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.upper().startswith(("BEGIN:", "END:", "VERSION:", "REV:", "UID:")):
                continue
            raw_key, _, value = line.partition(":")
            key = raw_key.split(";")[0].upper()  # strip vCard type params ("EMAIL;TYPE=INTERNET")
            value = value.strip()
            if key == "FN":
                name = name or _clean(value)
            elif key == "N":
                # "Doe;Jane;;;" -> "Doe Jane" (family first in vCard 3.0).
                name = name or _clean(" ".join(p for p in value.split(";") if p.strip()))
            elif key == "EMAIL":
                email = email or _clean(value.split(";")[-1])
            elif key == "TEL":
                phone = phone or _clean(value.split(";")[-1])
            elif key == "ADR":
                if address is None:
                    # ADR;TYPE=HOME:;;123 Main St;Anytown;CA;12345
                    parts = [p.strip() for p in value.split(";") if p.strip()]
                    if parts:
                        address = ", ".join(parts)
        if name or email or phone or address:
            contacts.append({
                "name": name, "email": email, "phone": phone, "site_address": address,
            })
    return contacts


def parse_vcard(text: str) -> list:
    """Parse a .vcf export. vobject handles real-world structured cards;
    the regex parser is the fallback (also the reference behaviour)."""
    if vobject is not None:
        try:
            return _parse_vcard_with_vobject(text)
        except Exception:  # noqa: BLE001 -- malformed cards fall back cleanly
            pass
    return _parse_vcard_regex(text)


# ---------------------------------------------------------------------------
# CSV contacts
# ---------------------------------------------------------------------------

def parse_csv(text: str) -> list:
    """Header-aware CSV. Recognized headers (case-insensitive): name / full
    name / client / company (or a First Name + Last Name pair), email /
    e-mail / email address, phone / telephone / tel / mobile / phone number,
    and address / site address / street / street address."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    lowered = {header.strip().lower(): header for header in reader.fieldnames if header}
    name_header = next((lowered[k] for k in
                        ("name", "full name", "client", "company") if k in lowered), None)
    first_name_header = lowered.get("first name")
    last_name_header = lowered.get("last name")
    email_header = next((lowered[k] for k in
                         ("email", "e-mail", "email address") if k in lowered), None)
    phone_header = next((lowered[k] for k in
                         ("phone", "telephone", "tel", "mobile", "phone number") if k in lowered), None)
    address_header = next((lowered[k] for k in
                           ("address", "site address", "street", "street address") if k in lowered), None)

    contacts = []
    for row in reader:
        if not any((row.get(k) or "").strip() for k in row):
            continue
        name = _clean(row.get(name_header)) if name_header else None
        if not name and (first_name_header or last_name_header):
            name = _clean(f"{row.get(first_name_header) or ''} {row.get(last_name_header) or ''}".strip())
        contact = {
            "name": name,
            "email": _clean(row.get(email_header)) if email_header else None,
            "phone": _clean(row.get(phone_header)) if phone_header else None,
            "site_address": _clean(row.get(address_header)) if address_header else None,
        }
        if contact["name"] or contact["email"] or contact["phone"]:
            contacts.append(contact)
    return contacts

