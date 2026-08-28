"""Parse contact data into client records: .vcf files, .csv files, and
free-form pasted lead text.

Pure Python (no framework, no third-party libs) so every parser is
unit-testable the same way the workspace helpers are. Each parser returns a
list of dicts shaped {name, email, phone, site_address} -- missing fields are
None and the caller (app/routers/clients.py) decides dedupe + persistence.
"""
import csv
import io
import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{3}\)|(?:^|\s)\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}"
)
# "123 Main St", "PO Box 4" ... -- a line that reads like an address.
_ADDRESS_RE = re.compile(r"^\s*(?:P\.?\s?O\.?\s?Box\s+)?\d{1,6}\s+\S", re.IGNORECASE)
_ADDRESS_PREFIXES = (
    "street", "road", "avenue", "ave", "blvd", "drive", "dr", "lane", "ln",
    "highway", "hwy", "crescent", "court", "ct", "terrace",
)


def _clean(value):
    """Collapse whitespace; empty strings become None."""
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def normalize_phone(phone):
    """' (555) 010-1234 ' -> '5550101234' (last 10 digits)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if digits else None


def parse_vcard(text):
    """BEGIN:VCARD blocks -> [{name, email, phone, site_address}]."""
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


def parse_csv(text):
    """Header-aware CSV. Recognized headers (case-insensitive): name/full
    name/client/company, email/e-mail, phone/telephone/tel/mobile, and
    address/site address/street/street address."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    lowered = {header.strip().lower(): header for header in reader.fieldnames if header}
    name_header = next((lowered[k] for k in
                        ("name", "full name", "client", "company") if k in lowered), None)
    email_header = next((lowered[k] for k in ("email", "e-mail") if k in lowered), None)
    phone_header = next((lowered[k] for k in
                         ("phone", "telephone", "tel", "mobile", "phone number") if k in lowered), None)
    address_header = next((lowered[k] for k in
                           ("address", "site address", "street", "street address") if k in lowered), None)

    contacts = []
    for row in reader:
        if not any((row.get(k) or "").strip() for k in row):
            continue
        contact = {
            "name": _clean(row.get(name_header)) if name_header else None,
            "email": _clean(row.get(email_header)) if email_header else None,
            "phone": _clean(row.get(phone_header)) if phone_header else None,
            "site_address": _clean(row.get(address_header)) if address_header else None,
        }
        if contact["name"] or contact["email"] or contact["phone"]:
            contacts.append(contact)
    return contacts


def parse_quick_text(text):
    """Free-form paste -> contacts. One contact per blank-line-separated
    block; within a block each line is classified as email / phone /
    address / name. If no blank lines exist, every line is treated as its
    own contact."""
    blocks = [b for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not blocks:
        blocks = [[line] for line in lines]

    contacts = []
    for block in blocks:
        name_parts, email, phone, address = [], None, None, None
        for line in (l.strip() for l in block.splitlines() if l.strip()):
            email_match = _EMAIL_RE.search(line)
            if email_match:
                email = email or email_match.group(0)
                line = line.replace(email_match.group(0), "").strip()
            phone_match = _PHONE_RE.search(line)
            if phone_match:
                phone = phone or _clean(phone_match.group(0))
                line = line.replace(phone_match.group(0), "").strip()
            if address is None and line and (
                    _ADDRESS_RE.match(line) or line.lower().startswith(_ADDRESS_PREFIXES)):
                address = line
            elif line and not email_match and not phone_match:
                name_parts.append(line)
        name = _clean(" ".join(name_parts))
        if name or email or phone or address:
            contacts.append({
                "name": name, "email": email, "phone": phone, "site_address": address,
            })
    return contacts
