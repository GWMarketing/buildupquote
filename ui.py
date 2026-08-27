"""Look and feel for the workspace: the palette, the CSS, and the small
pieces of screen furniture the app builds itself out of.

Kept apart from app.py for two reasons. The obvious one is that app.py is
long enough already. The more useful one is that the handful of functions
here that actually make DECISIONS -- which rows a search box matches, what
a download gets named -- are plain Python with no Streamlit in them, so
they can be tested, and they are (tests/test_ui.py). Everything that draws
is a thin wrapper with no logic worth hiding.

The colours live in two places on purpose. `.streamlit/config.toml` sets
the five values Streamlit itself uses for widgets, which no stylesheet can
reach. PALETTE below repeats them so the CSS here can match, and adds the
status colours Streamlit has no concept of. Change one, change the other.

A note on the CSS: Streamlit renames its internal classes between
versions, so this styles only the `data-testid` hooks, which are stable,
and the semantic tags inside them. Anything that stops matching after an
upgrade quietly does nothing -- the app keeps working, it just looks
plainer. Nothing here is load-bearing.
"""

PALETTE = {
    # Matches .streamlit/config.toml
    "primary": "#0F6CBD",
    "background": "#F5F8FC",
    "surface": "#FFFFFF",
    "text": "#13212C",
    # Status colours, used for pills, cards and section rules
    "ink_soft": "#5A6B7A",
    "line": "#DCE4EC",
    "good": "#17795E",
    "good_bg": "#E6F4EF",
    "warn": "#A15C07",
    "warn_bg": "#FDF1E0",
    "bad": "#B3261E",
    "bad_bg": "#FBEAE8",
    "info": "#0F6CBD",
    "info_bg": "#E7F0FA",
    "added": "#6E4FA3",
    "added_bg": "#F0EBF8",
}

_TONES = ("good", "warn", "bad", "info", "added")


def _tone(name):
    return name if name in _TONES else "info"


CSS = """
<style>
/* ---------- page rhythm ---------- */
.block-container { padding-top: 1.6rem; max-width: 1500px; padding-bottom: 96px; }

/* ---------- the coloured masthead ---------- */
.bq-mast {
  background: linear-gradient(100deg, #0F6CBD 0%, #1B8AB0 55%, #17795E 100%);
  color: #fff; border-radius: 12px; padding: 20px 26px; margin-bottom: 20px;
  box-shadow: 0 2px 10px rgba(15,108,189,.16);
}
.bq-mast h1 { margin: 0; font-size: 1.85rem; font-weight: 700; letter-spacing: -.02em; color:#fff; }
.bq-mast p  { margin: 6px 0 0; opacity: .93; font-size: .96rem; }

/* ---------- sticky quote totals bar ---------- */
/* Pinned to the bottom of the viewport on every tab, rebuilt each rerun
   from the live quote totals (see app._quote_totals / ui.totals_bar). */
.bq-totals-bar {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 999;
  display: flex; align-items: center; justify-content: flex-end; gap: 30px;
  background: #0F2A43; color: #fff; padding: 10px 26px;
  border-top: 3px solid #0F6CBD;
  box-shadow: 0 -3px 12px rgba(15, 42, 67, .18);
  font-size: .82rem; letter-spacing: .02em;
}
.bq-totals-bar .bq-total-item { color: #B9CEE4; white-space: nowrap; }
.bq-totals-bar .bq-total-item b { color: #fff; font-weight: 700; font-size: .98rem; margin-left: 4px; }
.bq-totals-bar .bq-total-grand b { font-size: 1.24rem; }

/* ---------- section headings ---------- */
.bq-sec { display: flex; align-items: center; gap: 10px; margin: 6px 0 2px; }
.bq-sec .bq-bar { width: 5px; height: 24px; border-radius: 3px; }
.bq-sec h3 { margin: 0; font-size: 1.16rem; font-weight: 700; letter-spacing: -.01em; }

/* ---------- claim summary cards ---------- */
.bq-cards { display: flex; flex-wrap: wrap; gap: 12px; margin: 2px 0 14px; }
.bq-card {
  flex: 1 1 190px; background: #fff; border: 1px solid #DCE4EC;
  border-left: 4px solid #0F6CBD; border-radius: 9px; padding: 12px 15px;
}
.bq-card .bq-label {
  font-size: .69rem; letter-spacing: .09em; text-transform: uppercase;
  color: #5A6B7A; font-weight: 700; margin-bottom: 3px;
}
.bq-card .bq-value {
  font-size: 1.06rem; font-weight: 650; color: #13212C;
  word-break: break-word; line-height: 1.32;
}

/* ---------- status pills ---------- */
.bq-pill {
  display: inline-block; padding: 3px 11px; border-radius: 999px;
  font-size: .74rem; font-weight: 700; letter-spacing: .045em;
  text-transform: uppercase; border: 1px solid currentColor; margin-right: 6px;
}

/* ---------- tabs ---------- */
/* Made deliberately large -- these are the app's main menu, and the
   first thing Glenn said needed to be easier to see (2026-08-25). */
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 3px solid #DCE4EC; }
.stTabs [data-baseweb="tab"] {
  height: 58px; padding: 0 28px; font-weight: 700; font-size: 1.14rem;
  border-radius: 10px 10px 0 0; color: #5A6B7A;
}
.stTabs [data-baseweb="tab"] p { font-size: 1.14rem; font-weight: 700; }
.stTabs [aria-selected="true"] {
  background: #E7F0FA; color: #0F6CBD; box-shadow: inset 0 -3px 0 #0F6CBD;
}

/* ---------- metrics ---------- */
[data-testid="stMetric"] {
  background: #fff; border: 1px solid #DCE4EC; border-radius: 9px;
  padding: 13px 16px; border-top: 3px solid #0F6CBD;
}
[data-testid="stMetricLabel"] p { font-weight: 650; color: #5A6B7A; font-size: .8rem; }
[data-testid="stMetricValue"] { font-size: 1.42rem; color: #13212C; }

/* ---------- tables ---------- */
[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"],
[data-testid="stDataEditor"] { border-radius: 9px; border: 1px solid #DCE4EC; }

/* ---------- expanders, alerts, sidebar ---------- */
[data-testid="stExpander"] details {
  border: 1px solid #DCE4EC; border-radius: 9px; background: #fff;
}
[data-testid="stExpander"] summary { font-weight: 650; font-size: 1.02rem; }
[data-testid="stAlert"] { border-radius: 9px; }
[data-testid="stSidebar"] { border-right: 1px solid #DCE4EC; }
[data-testid="stSidebar"] h2 { font-size: 1.05rem; }

/* The built-in sidebar collapse control (the chevron at the top of the
   sidebar) is a permanent fixture -- always visible, never faded or
   hidden, with a comfortable hit area. */
[data-testid="stSidebarCollapseButton"] {
  visibility: visible; opacity: 1;
  min-width: 36px; min-height: 32px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 8px;
}
[data-testid="stSidebarCollapseButton"]:hover { background: #E7F0FA; }

/* ---------- buttons ---------- */
.stButton button, .stDownloadButton button {
  border-radius: 8px; font-weight: 650;
}
.stDownloadButton button { width: 100%; }

/* ---------- a quieter caption ---------- */
[data-testid="stCaptionContainer"] p { color: #5A6B7A; }
</style>
"""


# ---------------------------------------------------------------------
# Decisions -- plain Python, no Streamlit, covered by tests/test_ui.py
# ---------------------------------------------------------------------

def filter_rows(frame, query, columns=None):
    """Rows where `query` appears anywhere, case-insensitively.

    Deliberately dumb: one box, no syntax to learn, matches any column
    including numbers ("172.5" finds a quantity, "R&R" finds a
    description, "Roof" finds a trade). Multiple words must ALL appear
    somewhere in the row, which is how people expect search to behave --
    "remove carpet" finds a line that says "Remove Carpet" and also one
    that says "Carpet - remove and haul".

    Returns the frame UNCHANGED for an empty query, and always keeps the
    original index, because the caller writes edits back to the master
    table by index.
    """
    if frame is None or len(frame) == 0:
        return frame
    terms = [t for t in str(query or "").lower().split() if t]
    if not terms:
        return frame
    searchable = frame if columns is None else frame[[c for c in columns if c in frame.columns]]
    # Built row by row rather than column by column on purpose: the table
    # mixes text, numbers and booleans, and pandas re-infers dtypes on a
    # column-wise pass, which puts floats back into what should be an
    # all-string frame and breaks the join.
    haystack = searchable.apply(
        lambda row: " ".join(str(value) for value in row).lower(), axis=1
    )
    keep = haystack.apply(lambda text: all(term in text for term in terms))
    return frame[keep]


# Characters no operating system will accept in a file name. Everything
# else the contractor typed is kept, spaces included -- this is their file
# and "Doyle kitchen rebuild v2" is a perfectly good name for it.
_ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name, extension, fallback, separator=" "):
    """Turn whatever someone typed into a safe file name.

    Strips the characters Windows and macOS refuse, collapses runs of
    whitespace (to `separator` -- a space for a name the contractor typed
    themselves, "_" for an auto-built name joining several pieces, see
    app.py's _slugify), removes a duplicate extension if they typed one,
    and trims to a sane length. An empty or all-punctuation name falls
    back rather than producing a file called ".pdf". An empty `extension`
    returns the cleaned name itself (no trailing dot) -- how the
    auto-built default download name is produced without inventing one.
    """
    cleaned = "".join(ch for ch in str(name or "") if ch not in _ILLEGAL_FILENAME_CHARS)
    cleaned = separator.join(cleaned.split()).strip(" .")
    if cleaned.lower().endswith("." + extension.lower()):
        cleaned = cleaned[: -(len(extension) + 1)].strip(" .")
    if not cleaned:
        return fallback
    if not extension:
        return cleaned
    return f"{cleaned[:120]}.{extension}"


def row_label(number, position, added=False, prefix="A"):
    """The "#" shown at the left of every table row.

    A carrier's own printed line number is the useful one -- it is what
    lets a contractor put this table beside the PDF and check a figure in
    two seconds. Rows the contractor added have no carrier number, so
    they get "A1", "A2"... which can never be mistaken for one. A
    code-required addition uses `prefix="L"` (for "legally required")
    instead, so it reads apart from a line the contractor chose to add
    on their own -- see app.py's `_next_code_label`.
    """
    if added or not str(number or "").strip():
        return f"{prefix}{position}"
    return str(number).strip()


# ---------------------------------------------------------------------
# Drawing -- thin wrappers, no logic
# ---------------------------------------------------------------------

def inject_css(st):
    st.markdown(CSS, unsafe_allow_html=True)


def masthead(st, title, subtitle):
    st.markdown(
        f'<div class="bq-mast"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def totals_bar(st, subtotal, tax_amount, total, line_count):
    """The sticky quote-totals bar pinned to the bottom of the page.
    Rebuilt every rerun from the live totals, so it always matches the
    current tables -- no state kept here, it's pure display."""
    st.markdown(
        '<div class="bq-totals-bar">'
        f'<span class="bq-total-item">Lines <b>{line_count}</b></span>'
        f'<span class="bq-total-item">Subtotal <b>${subtotal:,.2f}</b></span>'
        f'<span class="bq-total-item">Tax <b>${tax_amount:,.2f}</b></span>'
        f'<span class="bq-total-item bq-total-grand">Total <b>${total:,.2f}</b></span>'
        "</div>",
        unsafe_allow_html=True,
    )


def section(st, title, tone="info", caption=None):
    colour = PALETTE[_tone(tone)]
    st.markdown(
        f'<div class="bq-sec"><span class="bq-bar" style="background:{colour}"></span>'
        f"<h3>{title}</h3></div>",
        unsafe_allow_html=True,
    )
    if caption:
        st.caption(caption)


def pill(text, tone="info"):
    """Returns HTML -- write it with st.markdown(..., unsafe_allow_html=True)."""
    colour = PALETTE[_tone(tone)]
    background = PALETTE[_tone(tone) + "_bg"]
    return (
        f'<span class="bq-pill" style="color:{colour};background:{background}">{text}</span>'
    )


def cards(st, items):
    """items: sequence of (label, value, tone). One coloured card each."""
    blocks = []
    for label, value, tone in items:
        colour = PALETTE[_tone(tone)]
        blocks.append(
            f'<div class="bq-card" style="border-left-color:{colour}">'
            f'<div class="bq-label">{label}</div>'
            f'<div class="bq-value">{value}</div></div>'
        )
    st.markdown(f'<div class="bq-cards">{"".join(blocks)}</div>', unsafe_allow_html=True)
