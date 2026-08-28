"""Contact parsing for the 1-Click Client Sync Hub.

Public service API for turning free-form lead text, vCard (.vcf) exports,
and CSV contact sheets into normalized client records shaped
{name, email, phone, site_address}. The parsing engine lives in
app.services.contact_import (unit-tested there); this module is the stable
surface the router and UI contract on, so callers never import engine
internals directly.

Normalization notes:
- All three parsers produce the same dict shape; missing fields are None.
- Address always maps to `site_address` (the Client model column).
- Phone numbers are returned as written; use normalize_phone() for the
  dedupe key (the router does this at persist time).
"""

from app.services import contact_import


def parse_lead_text(raw_text: str) -> dict:
    """Extract {name, email, phone, site_address} from a chunk of raw text.

    A single lead/contact: WhatsApp/SMS/email snippets pasted into the
    quick-paste box. Recognizes labelled fields ("Name:", "Site:",
    "Address:") as well as loose content -- emails, UK/NA phone patterns
    ("+44 7700 900123", "(555) 010-1234", "07700 900123"), the first
    address-looking line, and a first-line name fallback.
    """
    return contact_import.parse_lead_text(raw_text)


def parse_quick_text(raw_text: str) -> list:
    """Parse pasted text that may hold several leads -- one contact per blank
    line -- into a list of contact dicts. Each block goes through
    parse_lead_text, so every lead gets the same name/phone/address logic."""
    return contact_import.parse_quick_text(raw_text)


def parse_vcard_data(file_content: str) -> list:
    """Parse a .vcf export into a list of contact dicts.

    Uses `vobject` (structured ADR including region, multi-valued emails
    and phones) when available, falling back to a pure-regex parser so
    import works even without the dependency.
    """
    return contact_import.parse_vcard(file_content)


def parse_csv_contacts(file_content: str) -> list:
    """Parse a .csv contact sheet into a list of contact dicts.

    Header-aware (case-insensitive): name / full name / client / company
    (or a First Name + Last Name pair), email / e-mail / email address,
    phone / telephone / tel / mobile / phone number, and address / site
    address / street / street address.
    """
    return contact_import.parse_csv(file_content)


def normalize_phone(phone) -> str:
    """Dedupe key for a phone number: '(555) 010-1234' and '+44 7700 900123'
    both become '7700900123' so alternate representations of the same number
    collapse to one client."""
    return contact_import.normalize_phone(phone)


def has_contact_signal(raw_text: str) -> bool:
    """True when the text contains real contact info (email, phone, or a
    labelled Name/From/Client/Customer field). Used to reject junk input
    before it becomes a placeholder client row."""
    return contact_import.has_contact_signal(raw_text)
