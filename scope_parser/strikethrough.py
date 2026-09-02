# Detects text the document preparer struck through (crossed out).
#
# A strikethrough is not part of the extracted text -- it is a vector rule
# drawn over the words. pdfplumber exposes that geometry (page.lines /
# page.rects), so a strike can be recognized by position:
#
#   * it is horizontal (its two y endpoints agree),
#   * its vertical centre passes through the middle band of the word bbox
#     (30%-70% of the row height) -- table borders sit at the cell edges and
#     underlines below the baseline, so neither trips it,
#   * its horizontal span overlaps the word,
#   * it is thin relative to the text: a hairline, or a bar at most half
#     the text height -- a full-row highlight box is not a strike.
#
# Carriers strike through rows to reject or remove a line item (the Cobb
# estimate Dumpster line is a real example). parse_pdf uses this to flag
# struck items and exclude them from the quote totals; the contractor can
# re-include them with one click on the parser page.
import re

_MID_BAND = (0.30, 0.70)
# A rule taller than this is a highlight/background box, never a strike.
_MAX_RULE_HEIGHT = 12.0
_NUMBER_RE = re.compile(r'^(\d{1,3}[a-z]?)\.?\s')


def _line_y(item):
    top = item.get('top', item.get('y0'))
    bottom = item.get('bottom', item.get('y1'))
    return (top + bottom) / 2


def _rule_height(rule):
    return abs(rule.get('bottom', rule.get('y1')) - rule.get('top', rule.get('y0')))


def _horizontal_rules(page):
    lines = [l for l in page.lines if abs(l['y0'] - l['y1']) < 0.5]
    rects = [r for r in page.rects if _rule_height(r) < _MAX_RULE_HEIGHT]
    return lines + rects


def struck_word_groups(page):
    # page -> {rounded top of a visual line: [word dicts a strike passes through]}.
    rules = _horizontal_rules(page)
    if not rules:
        return {}
    groups = {}
    for word in page.extract_words(keep_blank_chars=False):
        top, bottom = word['top'], word['bottom']
        height = bottom - top
        lo = top + height * _MID_BAND[0]
        hi = top + height * _MID_BAND[1]
        for rule in rules:
            sy = _line_y(rule)
            if not (lo <= sy <= hi and rule['x0'] < word['x1'] and rule['x1'] > word['x0']):
                continue
            if _rule_height(rule) > max(3.0, 0.5 * height):
                continue
            groups.setdefault(round(top, 1), []).append(word)
            break
    return groups


def struck_lines_on_page(page):
    # The struck text lines of one page, one per visual line, as plain
    # strings (words in reading order).
    out = []
    groups = struck_word_groups(page)
    for key in sorted(groups):
        words = sorted(groups[key], key=lambda w: w['x0'])
        text = ' '.join(w['text'] for w in words).strip()
        if text:
            out.append(text)
    return out


def struck_lines_from_pdf(pdf_path):
    # All struck text lines across every page of a PDF.
    import pdfplumber
    struck = set()
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            struck.update(struck_lines_on_page(page))
    return struck


def mark_struck_items(items, struck_lines, warnings=None):
    # Flag line items whose row was struck through in the source PDF.
    #
    # Matching is deliberately strict to avoid false positives:
    #   1. a struck line that starts with an item number marks that item;
    #   2. otherwise, at least two significant words (or the whole
    #      description) must overlap a struck line;
    #   3. otherwise, the item own figures (quantity + unit + unit price)
    #      must appear in the struck line -- catches partial strikes that
    #      only cross out the figures column, not the description.
    #
    # Struck items are marked needs_review (so the workspace flags them and
    # the parser page excludes them from the totals); the contractor can
    # re-include them with one click on the parser page.
    marked = 0
    for raw in struck_lines:
        text = ' '.join(raw.split())
        m = _NUMBER_RE.match(text)
        item = None
        if m:
            number = m.group(1)
            item = next((li for li in items if li.number == number), None)
        if item is None:
            struck_tokens = {t.lower().strip('.,;:') for t in text.split() if len(t) > 1}
            for li in items:
                desc_tokens = {t.lower().strip('.,;:') for t in li.description.split() if len(t) > 1}
                overlap = struck_tokens & desc_tokens
                if overlap and (len(overlap) >= 2 or overlap == desc_tokens):
                    item = li
                    break
        if item is None:
            tokens = set(text.split())
            for li in items:
                if li.quantity is None or li.unit is None:
                    continue
                qty_tok = ('%s' % li.quantity).rstrip('0').rstrip('.')
                if qty_tok not in tokens or li.unit not in tokens:
                    continue
                if li.unit_price is not None:
                    price_tok = ('%.2f' % li.unit_price).rstrip('0').rstrip('.')
                    if price_tok not in tokens:
                        continue
                item = li
                break
        if item is not None and not item.struck_through:
            item.struck_through = True
            item.needs_review = True
            reason = ('this line is struck through in the source PDF -- excluded '
                      'from the totals; re-include it if it applies')
            item.review_reason = '; '.join(p for p in (item.review_reason, reason) if p)
            marked += 1
    if warnings is not None and marked:
        warnings.append(f'{marked} line item(s) struck through in the source PDF and excluded from the totals')
    return marked
