"""Renders a ProposalData into HTML, and HTML into a PDF file.

Uses wkhtmltopdf (via pdfkit) rather than WeasyPrint -- WeasyPrint wasn't
installable in the environment this was built in (no PyPI access), and
wkhtmltopdf was already present on the system. Either works fine for this
job (a print-style HTML/CSS document); if you'd rather standardize on
WeasyPrint later, only this file needs to change -- build.py and the
template don't know or care which one renders them.
"""
import base64
import os

from jinja2 import Environment, FileSystemLoader

from .models import TX_DEDUCTIBLE_LAW_CITATION, ProposalData

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

_MIME_BY_EXT = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "svg": "svg+xml"}


def _logo_data_uri(logo_path):
    if not logo_path or not os.path.exists(logo_path):
        return None
    ext = os.path.splitext(logo_path)[1].lstrip(".").lower()
    mime = _MIME_BY_EXT.get(ext, "png")
    with open(logo_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


def render_proposal_html(data: ProposalData) -> str:
    # autoescape=True (not select_autoescape) because the template file is
    # named proposal.html.j2 -- select_autoescape's extension-sniffing looks
    # at the last extension (".j2") and would silently turn escaping OFF,
    # which would let a contractor name or claim field containing "<" or "&"
    # (e.g. "Sample Roofing & Restoration") inject raw HTML into the page.
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    template = env.get_template("proposal.html.j2")
    return template.render(
        data=data,
        logo_uri=_logo_data_uri(data.contractor.logo_path),
        deductible_law_citation=TX_DEDUCTIBLE_LAW_CITATION,
    )


def render_proposal_pdf(data: ProposalData, output_path: str) -> str:
    import pdfkit

    html = render_proposal_html(data)
    options = {
        "page-size": "Letter",
        "margin-top": "0.5in",
        "margin-bottom": "0.6in",
        "margin-left": "0.5in",
        "margin-right": "0.5in",
        "encoding": "UTF-8",
        "quiet": "",
        "footer-center": "Page [page] of [topage]",
        "footer-font-size": "8",
        "footer-spacing": "5",
    }
    pdfkit.from_string(html, output_path, options=options)
    return output_path
