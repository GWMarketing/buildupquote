"""The single entry point the rest of the app should call.

    from parser import parse_pdf
    estimate = parse_pdf("some_estimate.pdf")

or, if you already have extracted text (e.g. in a test):

    from parser import parse_text
    estimate = parse_text(text)
"""
from . import (
    carrier_summary as carrier_summary_mod,
    claim_flags,
    confidence as confidence_mod,
    doc_type,
    fingerprint as fingerprint_mod,
    generic_reader,
    line_items,
    measurements,
    metadata,
    noise_filter,
    profiles,
    totals,
)
from .models import ClaimMetadata, ParsedEstimate


def parse_text(text: str, pdf_info=None) -> ParsedEstimate:
    lines = text.splitlines()

    meta = ClaimMetadata(fields=metadata.extract_metadata(lines))

    kept_lines, discarded_lines = noise_filter.strip_noise(lines)

    # Step one: which program wrote this? Metadata is the heaviest signal
    # but never the only one -- see fingerprint.py for why.
    fp = fingerprint_mod.fingerprint(kept_lines, pdf_info)
    profile = fp.profile

    # Step two: read the rows. A recognised format is read by mapping its
    # printed column headers; anything else is read by solving for the
    # price and total columns arithmetically. The two are separate
    # scanners on purpose, so work on one can never disturb the other.
    if profile.column_strategy == "arithmetic":
        items, section_totals_raw, li_warnings = generic_reader.parse_generic(kept_lines, profile)
    else:
        items, section_totals_raw, li_warnings = line_items.parse_items_and_sections(
            kept_lines, profile
        )
    measurement_tuples = measurements.extract_measurements(kept_lines)
    section_totals = totals.check_section_totals(section_totals_raw)

    warnings = list(li_warnings)
    review_count = sum(1 for li in items if li.needs_review)
    if review_count:
        warnings.append(f"{review_count} line item(s) need manual review -- see needs_review_items")
    mismatched = [st for st in section_totals if not st.matched and not st.skipped]
    for st in mismatched:
        warnings.append(
            f"section '{st.section}': parsed line items sum to {st.parsed_rcv_sum}, "
            f"which doesn't match any printed total {st.printed_numbers}"
        )

    from .models import MeasurementBlock
    measurement_blocks = [
        MeasurementBlock(section=s, label=label, value=value, unit=unit)
        for s, label, value, unit in measurement_tuples
    ]

    # Computed from the same noise-filtered, boilerplate-excluded lines
    # (kept_lines) the rest of the pipeline already worked from -- not raw
    # extraction -- so a fake "how to read your estimate" insert page can't
    # spoof a mortgagee mention or a deductible/policy-limit pair. See
    # claim_flags.py.
    flags = claim_flags.compute_claim_flags(kept_lines, items)

    # Step three: what KIND of document is this? Separate question from
    # which program wrote it, and the one that decides what the app is
    # allowed to do with the numbers -- see doc_type.py.
    kind = doc_type.detect(
        kept_lines,
        item_count=len(items),
        claim_flags=flags,
        has_anchors=fingerprint_mod.looks_like_line_item_document(kept_lines),
    )
    # Deliberately NOT appended to `warnings`. A warning means something
    # may be wrong with the parse; "this is an appraisal document" is a
    # fact about the document, not a problem with reading it. It reaches
    # the contractor through document_type.advice and the confidence
    # banner instead. (The golden-snapshot lock caught this the first time
    # it was written the other way -- exactly what it is there for.)
    conf = confidence_mod.assess(items, section_totals, fp, kind)

    # Step four: the document's own bottom-line ladder (Line Item Total /
    # Overhead / Profit / Replacement Cost Value / Net Claim). Separate
    # from everything above -- this is a document-level FLAT total, not
    # tied to any one row, so it was never visible from summing line
    # items alone. See carrier_summary.py.
    summary = carrier_summary_mod.find_summary(kept_lines)
    # Only cross-check against our own parsed sum when the summary block
    # is NOT explicitly scoped to one coverage ("Summary for AA-Dwelling")
    # -- a document with Dwelling + Other Structures + Personal Property
    # prints a separate Line Item Total per coverage, and none of those is
    # supposed to equal the whole document's line items. Comparing them
    # would manufacture a false "something's wrong" warning on a document
    # that parsed perfectly -- exactly what the anti-guessing rule this
    # whole package follows exists to prevent. See carrier_summary.py's
    # coverage_label docstring, and the allstate_5410/appraiser_williams1
    # fixtures, both multi-coverage documents this guard was written for.
    if summary is not None and summary.line_item_total is not None and not summary.coverage_label:
        parsed_sum = round(sum(li.rcv or 0 for li in items), 2)
        summary.parsed_items_sum = parsed_sum
        summary.reconciles_with_parsed_items = (
            abs(parsed_sum - summary.line_item_total) <= 0.05
        )
        if not summary.reconciles_with_parsed_items:
            warnings.append(
                f"the document's own \"Line Item Total\" is {summary.line_item_total:,.2f}, but the "
                f"line items we parsed sum to {parsed_sum:,.2f} -- something in the parse may be off"
            )

    return ParsedEstimate(
        metadata=meta,
        line_items=items,
        measurements=measurement_blocks,
        section_totals=section_totals,
        discarded_lines=discarded_lines,
        warnings=warnings,
        claim_flags=flags,
        fingerprint=fp,
        document_type=kind,
        confidence=conf,
        carrier_summary=summary,
    )


def parse_pdf(pdf_path) -> ParsedEstimate:
    """pdf_path may be a filesystem path (str) or a file-like object
    (e.g. a Streamlit file upload) -- see extract.py."""
    from .extract import extract_pdf_info, extract_text

    # Read the container metadata FIRST: it is the strongest single signal
    # for which program wrote this file, and the parse routes on it.
    pdf_info = extract_pdf_info(pdf_path)
    estimate = parse_text(extract_text(pdf_path, pdf_info=pdf_info), pdf_info=pdf_info)
    # Only possible from an actual file -- parse_text() alone has no way
    # to know this, since it's not text printed on any page. See
    # metadata.py's fields_from_pdf_info() docstring for why this is
    # worth having at all.
    pdf_fields = metadata.fields_from_pdf_info(pdf_info)
    for key, value in pdf_fields.items():
        estimate.metadata.fields.setdefault(key, value)
    return estimate
