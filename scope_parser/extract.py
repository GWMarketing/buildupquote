"""PDF -> text. The only module in this package that touches an actual PDF
file -- kept separate from the parsing logic in line_items.py etc. so
those can be tested against plain-text fixtures without needing a real PDF
on disk (see tests/).

Uses pdfplumber (built on pdfminer.six). Not PyMuPDF -- pdfplumber was
already available in this environment and does the same job for our
purposes (word-level text extraction); either would work fine here.
"""

# Some carrier PDFs include a generic "how to read your estimate" insert
# with a made-up example claim, laid out as an annotated diagram with
# callout labels overlapping explanatory text. pdfplumber's text extraction
# (which sorts words by position, not by which visual block they belong to)
# badly scrambles pages like that -- and because the fake example reuses
# item numbers 1, 2, 3... just like a real claim, its made-up dollar
# figures were bleeding into real totals-consistency checks. Found via a
# real Travelers PDF, where this insert is confined to exactly one page and
# is reliably fingerprinted by "GUIDE_EXAMPLE" -- Travelers' own internal
# name for the fake example estimate on that page. Whole pages carrying
# this marker are dropped before any line-item parsing sees them; there's
# nothing salvageable on them, so excluding the page is safer than trying
# to parse scrambled text and hoping the safety-valve catches it.
#
# The marker itself lives in the rule sheets (Xactimate's
# boilerplate_page_markers), not here -- rule-as-data, so the single
# source is profiles.py, and a future format's markers drop in with its
# sheet instead of a second copy of the value.


def _markers_for(pdf_info):
    """Which boilerplate page fingerprints to look for.

    Every rule sheet's markers are used, not just the winning one: the
    page has to be dropped BEFORE the document can be fingerprinted, so
    there is no sheet selected yet at this point. The markers are literal,
    carrier-specific strings ("GUIDE_EXAMPLE"), so checking all of them
    costs nothing and cannot produce a false positive on another format.
    """
    from .profiles import IDENTIFY_ONLY, REGISTRY

    markers = set()
    for sheet in list(REGISTRY.values()) + list(IDENTIFY_ONLY.values()):
        markers.update(sheet.boilerplate_page_markers)
    return tuple(markers)


def extract_text(pdf_path, pdf_info=None) -> str:
    """pdf_path may be a filesystem path (str) or a file-like object
    (e.g. Streamlit's UploadedFile) -- pdfplumber accepts either."""
    import pdfplumber

    markers = _markers_for(pdf_info)
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if any(marker in text for marker in markers):
                continue
            lines.extend(text.split("\n"))
            lines.append("")  # keep a page-boundary gap, like the real docs have
    return "\n".join(lines)


def extract_pdf_info(pdf_path) -> dict:
    """The file's own /Info dictionary -- Producer, Creator, CreationDate,
    Title, etc. -- as opposed to anything printed on a page. This is
    where "which program actually wrote this PDF" lives; see
    metadata.py's fields_from_pdf_info() for what gets pulled out of it.
    A file-like object that's already been read by extract_text() (e.g.
    a Streamlit upload) needs its position reset first, same reason
    pipeline.py opens it twice rather than trying to share one handle."""
    import pdfplumber

    if hasattr(pdf_path, "seek"):
        pdf_path.seek(0)
    with pdfplumber.open(pdf_path) as pdf:
        return dict(pdf.metadata or {})
