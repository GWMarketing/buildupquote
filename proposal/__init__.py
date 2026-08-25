"""Branded proposal export -- step 3.

    from proposal import build_proposal, render_proposal_pdf, ContractorInfo

    contractor = ContractorInfo(name="Acme Roofing", phone="555-1234", ...)
    data = build_proposal(rows, contractor, estimate.metadata.fields, "2026-08-23")
    render_proposal_pdf(data, "proposal.pdf")

`rows` is the same list-of-dicts shape app.py's editing table produces
(Trade, Description, Qty, Unit, Unit Cost, Margin %, Include).
"""
from .build import build_proposal, claim_info_from_metadata, group_line_items
from .models import ClaimInfo, ContractorInfo, ProposalData, ProposalLineItem, TradeGroup
from .render import render_proposal_html, render_proposal_pdf

__all__ = [
    "build_proposal",
    "claim_info_from_metadata",
    "group_line_items",
    "ClaimInfo",
    "ContractorInfo",
    "ProposalData",
    "ProposalLineItem",
    "TradeGroup",
    "render_proposal_html",
    "render_proposal_pdf",
]
