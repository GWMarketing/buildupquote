"""Renders a ProposalData into HTML, and HTML into a PDF file.

Uses WeasyPrint -- a pure-pip renderer with no system-level installer
(unlike wkhtmltopdf, whose Homebrew cask was removed). Only this file
knows about the renderer; build.py and the template are agnostic.
"""
import base64
import os

from jinja2 import Environment, FileSystemLoader
from weasyprint import CSS, HTML

from .models import TX_DEDUCTIBLE_LAW_CITATION, ProposalData

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

_MIME_BY_EXT = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "svg": "svg+xml"}
# Page-number footer as a @page rule -- WeasyPrint's replacement for
# wkhtmltopdf's --footer-center/-topage command-line options.
_PAGE_FOOTER_CSS = CSS(string="""
@page {
  size: Letter;
  margin: 0.5in 0.5in 0.6in 0.5in;
  @bottom-center { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; }
}
""")


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
    html = render_proposal_html(data)
    HTML(string=html, base_url=TEMPLATE_DIR).write_pdf(
        output_path, stylesheets=[_PAGE_FOOTER_CSS]
    )
    return output_path
