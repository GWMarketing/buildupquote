"""Renders a Quote into a branded PDF via WeasyPrint (Jinja2 -> HTML -> PDF).

Only this module knows about WeasyPrint; the routers pass plain context
dicts. Mirrors proposal/render.py's approach so the whole app uses one
renderer and one set of @page rules.
"""
import base64
import os

from jinja2 import Environment, FileSystemLoader
from weasyprint import CSS, HTML

_TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "pdf"
)
_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)

_MIME_BY_EXT = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
                "svg": "svg+xml", "webp": "webp"}

_PAGE_CSS = CSS(string="""
@page {
  size: A4 portrait;
  margin: 18mm 16mm 22mm 16mm;
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #64748b; }
}
""")


def _logo_data_uri(logo_url):
    """logo_url is a /static/... path -> embed the file as a data URI so
    the PDF is self-contained (same approach as proposal/render.py)."""
    if not logo_url or not logo_url.startswith("/static/"):
        return None
    rel = logo_url[len("/static/"):]
    path = os.path.join(_STATIC_DIR, rel)
    if not os.path.exists(path):
        return None
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    mime = _MIME_BY_EXT.get(ext, "png")
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


def signature_uri(client_signature):
    """Normalize the stored client signature (data-URI or raw base64 PNG) into
    an <img src>. Returns None when there is no signature yet."""
    if not client_signature:
        return None
    sig = client_signature.strip()
    if sig.startswith("data:image"):
        return sig
    return f"data:image/png;base64,{sig}"


# Placeholder tokens contractors can use inside their master contract text.
CONTRACT_TOKENS = ("client_name", "project_total", "site_address", "date", "quote_ref", "contractor")


def process_contract_text(text: str, values: dict) -> str:
    """Replace {{token}} placeholders in the contract text with the actual
    values for this quote. Plain string replacement (never runs Jinja on the
    contractor's text)."""
    out = text or ""
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value or ""))
    return out


def contract_for_quote(quote, organization, currency: str, date_str: str):
    """(include_contract, processed_text) for a quote's master contract.

    An override set on the quote wins over the organization's master text; an
    empty result means no contract page is rendered even if the toggle is on.
    """
    include = bool(quote.include_contract) if quote.include_contract is not None else True
    if not include:
        return False, None
    raw = quote.custom_contract_override
    if not raw and organization is not None:
        raw = organization.master_contract_text
    if not raw:
        return True, None
    values = {
        "client_name": quote.client.name if quote.client else "",
        "project_total": f"{currency}{float(quote.total or 0):.2f}",
        "site_address": quote.site_address or "",
        "date": date_str,
        "quote_ref": f"#Q-{quote.id}",
        "contractor": organization.name if organization else "",
    }
    return True, process_contract_text(raw, values)


def deposit_for_quote(quote):
    """The active deposit milestone -- the first entry of the payment
    schedule -- as {label, percent, amount}. None when no schedule is set."""
    schedule = quote.payment_schedule or []
    if not schedule:
        return None
    first = schedule[0]
    percent = float(first.get("percent") or 0)
    return {
        "label": first.get("label") or "Deposit",
        "percent": percent,
        "amount": round(float(quote.total or 0) * percent / 100.0, 2),
    }


def format_money(value, symbol="$"):
    """'$1,234.56' -- USD-style thousands grouping with the org's symbol.

    The single money formatter for server-rendered templates (public proposal
    page, PDFs, emails). Values are floats/strings from the DB; anything
    unparseable renders as the symbol + 0.00 instead of crashing a page."""
    try:
        return f"{symbol}{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return f"{symbol}0.00"


def render_quote_pdf(context: dict, output_path: str) -> str:
    organization = context.get("organization")
    render_context = dict(context)
    render_context["currency"] = (organization.currency_symbol if organization
                                  and organization.currency_symbol else "$")
    render_context["logo_uri"] = _logo_data_uri(organization.logo_url if organization else None)
    env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=True)
    env.filters["money"] = format_money
    html = env.get_template("quote.html").render(**render_context)
    HTML(string=html).write_pdf(output_path, stylesheets=[_PAGE_CSS])
    return output_path
