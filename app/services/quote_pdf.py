"""Renders a Quote into a branded PDF via WeasyPrint (Jinja2 -> HTML -> PDF).

Only this module knows about WeasyPrint; the routers pass plain context
dicts. Mirrors proposal/render.py's approach so the whole app uses one
renderer and one set of @page rules.
"""
import os

from jinja2 import Environment, FileSystemLoader
from weasyprint import CSS, HTML

_TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "pdf"
)

_PAGE_CSS = CSS(string="""
@page {
  size: Letter;
  margin: 0.55in 0.55in 0.65in 0.55in;
  @bottom-center { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #64748b; }
}
""")


def render_quote_pdf(context: dict, output_path: str) -> str:
    env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=True)
    html = env.get_template("quote.html").render(**context)
    HTML(string=html).write_pdf(output_path, stylesheets=[_PAGE_CSS])
    return output_path
