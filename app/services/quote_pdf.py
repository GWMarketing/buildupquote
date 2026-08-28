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


def render_quote_pdf(context: dict, output_path: str) -> str:
    organization = context.get("organization")
    render_context = dict(context)
    render_context["currency"] = (organization.currency_symbol if organization
                                  and organization.currency_symbol else "$")
    render_context["logo_uri"] = _logo_data_uri(organization.logo_url if organization else None)
    env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=True)
    html = env.get_template("quote.html").render(**render_context)
    HTML(string=html).write_pdf(output_path, stylesheets=[_PAGE_CSS])
    return output_path
