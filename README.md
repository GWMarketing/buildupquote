# BuildUpQuote

Three pieces of the rebuild are done so far, in the order agreed on in
`architecture-decision.md`:

1. **The parsing engine** (`scope_parser/`) -- turns a carrier estimate
   PDF into clean, structured data.
2. **The editing workspace** (`app.py`) -- a Streamlit screen where you
   upload a PDF, see the parsed scope, and adjust trade, unit cost, and
   margin per line item until you have your price.
3. **The branded proposal export** (`proposal/`) -- turns your edited
   scope into a homeowner-facing PDF with your logo, business info, the
   claim details, and a signature block.

Since then, two more pieces have been added on top:

- A **sales tax rule selector** (`tax.py`) -- pick how the job is
  contracted (separated residential, lump-sum residential, commercial, or
  no tax) and the app charges tax correctly for that rule instead of
  leaving it out entirely. See "How sales tax works" below.
- **Claim context recognition** (`scope_parser/claim_flags.py`) -- the
  parser now also recognizes the claim-*process* realities from the
  "Claim Ledger" reference doc (percentage deductibles, mortgagees,
  Ordinance-or-Law coverage, the cosmetic damage exclusion, appraisal and
  public-adjuster documents, and which line items are code-driven) by
  reading the document's own text -- never a guess. See "How claim
  context recognition works" below.

Still to come: accounts/multi-tenant (Supabase), then polish.

## Running it

**Use Python 3.11 (or anything 3.10-3.12).** This was tested and confirmed
working on 3.11. A brand-new Python version like 3.14 will very likely
fail here -- pandas, Streamlit, and other packages usually take months
after a new Python release before they publish ready-to-install versions
for it, so `pip install` either errors out or tries to compile things
from source and fails partway through. Check what you've got with
`python3 --version`; if it's newer than 3.12, install 3.11 alongside it
(on Mac, `brew install python@3.11` is the easiest way) and create your
virtual environment with that specific version instead of plain
`python3`, e.g. `python3.11 -m venv venv`.

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

That opens the editing workspace in your browser. Fill in your business
info in the sidebar (name, address, phone, logo -- all optional except
name, which is what turns on the proposal PDF button), then upload one of
your sample PDFs. You should see the claim info, any warnings the parser
wants you to check, and an editable table of line items. As you edit,
two download buttons appear: a plain CSV, and (once you've named your
business) "Download Proposal PDF".

### Deploying it for a business partner (Streamlit Community Cloud)

Push this folder to a GitHub repo, then in [Streamlit Community
Cloud](https://share.streamlit.io) choose **Create app -> Deploy a public
app from GitHub** and point it at that repo (main branch, `app.py`). The
cloud reads three files in this repo at deploy time:

- `requirements.txt` -- Python packages, including WeasyPrint for the
  proposal PDF. Nothing here needs a system installer.
- `packages.txt` -- the Linux libraries WeasyPrint loads at render time
  (Pango + Harfbuzz; glib, fontconfig and the rest come in as their
  dependencies). The cloud runs `apt-get install` on this list before
  installing the Python packages. Keep it to these package names only:
  the cloud image mixes bullseye and trixie apt repos, and explicitly
  naming `libglib2.0-0` there forces the bullseye glib, which conflicts
  with the trixie glib that pango now needs. Without this file the PDF
  button fails with "cannot load library libpango-1.0".
- `.streamlit/config.toml` -- colours and the upload limit, shipped with
  the app so your partner sees the same look you do.

No wkhtmltopdf, no system downloads, no Rosetta -- that whole class of
install problem doesn't exist on the cloud. Each push to GitHub
redeploys the app, so your partner always checks the latest version.

**Adding a line item the carrier missed** is already built in: scroll to
the bottom of the editable table and there's a blank "+" row waiting --
Streamlit's data editor supports adding rows natively (`num_rows=
"dynamic"`), so nothing extra needs to be turned on. Fill in Trade, Qty,
Unit, and Unit Cost on that row the same as any other line, and it
flows into your price, the CSV, and the proposal PDF exactly like a
parsed row would.

**Totals by trade** appears right under the full line-item list, in the
"Your price" section -- a small table showing each trade's subtotal,
highest first, so you can see where the money in a job is actually
coming from without adding up the flat list by eye. It's grouped the
same way the exported proposal PDF already groups its own line items
(`proposal/build.py`'s `group_line_items`), so this is effectively a
live preview of that document's section breakdown, not a separate
number that could drift out of sync with it.

**The proposal PDF renderer is WeasyPrint** -- pure pip, installed by
`pip install -r requirements.txt`, no separate system installer. If a
render ever fails, the app says so plainly instead of failing silently
-- the message will point at WeasyPrint and the venv, not at any
system-level program.

To run the automated tests instead of the app:

```bash
python3 -m unittest discover -s tests -v
```

You should see `Ran 305 tests ... OK`. If that number goes down, or `OK`
turns into failures, something regressed.

**A note on how this was tested.** The environment this was built in
couldn't reach PyPI, so Streamlit itself couldn't be installed or run
there -- `app.py` has been syntax-checked and its data logic (parsing a
PDF, building the table rows, computing prices, assembling a proposal) is
fully covered by `tests/test_app_logic.py`, `tests/test_pricing_and_trades.py`,
and `tests/test_proposal.py`, but the actual widgets and layout haven't
been seen running in a browser yet. Please run `streamlit run app.py`
yourself and tell me if anything looks or behaves wrong -- that's the one
part of this I couldn't verify directly. The proposal PDF *rendering*
itself (HTML -> WeasyPrint -> PDF) has been verified end-to-end by the
test suite, which renders a real proposal PDF and reads it back with
pdfplumber.

## What's in here

```
scope_parser/        the parsing engine -- no UI code at all
  extract.py          PDF -> text (the only file that touches a real PDF)
  noise_filter.py      strips CAD sketch/dimension clutter out of the text
  schema.py             reads each section's own column header instead of
                         assuming one fixed layout (carriers vary a lot)
  line_items.py         the main parser: turns text lines into LineItem
                         records, handling multi-line descriptions and
                         section titles along the way
  measurements.py        reference parsing disabled (2026-08-27) -- kept only
                          for the is_measurement_line/has_labelled_measurement
                          filters that keep plan-measurement text out of the
                          line items (see "Plan measurements that look like
                          priced rows" below)
  metadata.py            claim number, policy number, insured name,
                          property address, etc. -- plus, separately, the
                          PDF file's own hidden "who made this" info (see
                          "How the app knows which program wrote a PDF")
  totals.py              the safety net -- see "How this catches its own
                          mistakes" below
  codes.py                recognizes an IRC/IBC building-code citation --
                           shared by line_items.py and claim_flags.py
  claim_flags.py          recognizes claim-PROCESS realities (percentage
                           deductibles, mortgagees, Ordinance-or-Law,
                           the cosmetic exclusion, appraisal/PA documents)
                           -- see "How claim context recognition works"
  tokens.py, units.py     small shared helpers
  models.py               the plain data structures everything returns
  pipeline.py             the one function you actually call:
                           parse_pdf("some_estimate.pdf") -> ParsedEstimate

app.py                the editing workspace + proposal export (Streamlit)
trades.py              guesses a trade (Roofing, Siding, ...) per line item
                        from its description -- editable, just a starting point
pricing.py             the one calculation the app does: qty x unit cost x
                        (1 + margin%), kept separate so it's testable
tax.py                 sales tax on YOUR price (not the carrier's) -- picks
                        the right math for a separated / lump-sum / commercial
                        contract; see "How sales tax works" below

proposal/             the branded, homeowner-facing PDF export
  models.py             ProposalLineItem has NO field for depreciation, ACV,
                         age, or condition -- a structural guarantee those
                         numbers can't end up on the proposal, not just a
                         filter someone could forget to apply
  build.py               turns the app's edited table + claim metadata into
                          a ProposalData (grouped by trade, with subtotals)
  render.py               ProposalData -> HTML (Jinja2) -> PDF (WeasyPrint)
  templates/proposal.html.j2   the actual look of the document -- logo and
                                business info left, claim info box right,
                                itemized table by trade, a "Summary by
                                Trade" recap of every trade's subtotal
                                sitting with the other totals at the
                                bottom, terms, signatures

tests/
  test_pipeline.py            parsing engine regression suite (35 tests)
  test_pricing_and_trades.py   pricing math + trade-guessing (12 tests)
  test_app_logic.py            app.py's data-shaping helpers, incl. the
                                 per-trade totals breakdown, the manual
                                 "add a line item" row builder, the
                                 payment-breakdown math, and the
                                 export-filename builder (57 tests)
  test_proposal.py             proposal build + tax breakdown + real
                                 end-to-end PDF render, text round-trip,
                                 the Summary-by-Trade placement, the
                                 Texas deductible-waiver notice, and the
                                 Payment Schedule section (42 tests)
  test_tax.py                  sales-tax rule math (8 tests)
  test_claim_flags.py          claim-context recognition, incl. real-fixture
                                 regression (25 tests)
  test_metadata.py              PDF file-level "who made this" metadata,
                                 incl. real Xactimate Creator strings (16 tests)
  fixtures/                     three real sample estimates (as extracted
                                 text, not the original PDFs) plus one tiny
                                 synthetic PDF for testing PDF extraction
```

## Using it on a PDF without the UI

```python
from scope_parser import parse_pdf
from proposal import ContractorInfo, build_proposal, render_proposal_pdf

estimate = parse_pdf("path/to/some_estimate.pdf")

for item in estimate.line_items:
    print(item.number, item.description, item.quantity, item.unit, item.rcv)

print(estimate.warnings)            # anything the parser wants you to double-check
print(estimate.needs_review_items)  # specific line items it wasn't confident about

# Once you've got your edited rows (Trade/Description/Qty/Unit/Unit Cost/
# Margin %/Material/Include -- the same shape app.py's table produces):
contractor = ContractorInfo(name="Acme Roofing", phone="555-1234", logo_path="logo.png")
data = build_proposal(
    rows, contractor, estimate.metadata.fields, "08/23/2026",
    tax_rule="separated_residential", tax_rate_pct=8.25,  # see tax.py -- optional, defaults to no tax
)
render_proposal_pdf(data, "proposal.pdf")
```

## How this catches its own mistakes

Every carrier PDF prints its own subtotal after each batch of line items
("Totals: Roof1 ... 33,258.67"). The parser adds up the line items it
found since the *previous* such line and checks whether that sum matches
one of the printed numbers. If it doesn't, that section shows up in
`estimate.warnings` -- and in the editing workspace, in the "worth
double-checking" box at the top -- instead of silently shipping a wrong
number. When you feed it a new carrier's PDF for the first time, check
that box before you trust the output.

Similarly, if a line item's row doesn't match the column layout the
parser expected, it gets flagged `needs_review = True` with a reason,
rather than guessing at a dollar figure. Those show up as checked boxes
in the "Needs Review" column in the app. The box is yours to work with:
check a line that needs another look, or uncheck one you've verified
against the PDF, and it lands on -- or leaves -- the Review tab's
"Needs review" list. See `SafetyValveTest` in `test_pipeline.py` for
exactly what triggers this.

On the proposal side, the safety net is structural rather than a check
you could fail to run: `ProposalLineItem` (what actually reaches the PDF)
has no field to put a depreciation, ACV, age, or condition figure into,
so there's no way for those to leak onto a document a homeowner sees --
short of someone adding a new field to that class on purpose. There's
also a real end-to-end test (`test_no_insurance_only_figures_leak_onto_the_proposal`)
that renders an actual PDF and searches the extracted text for those terms.
The one insurance-related word the proposal *does* say on purpose is
"deductible" -- the terms section states plainly that it's the
homeowner's responsibility and isn't absorbed into your price, per the
project's legal notes. A separate test checks that word never appears
next to a dollar figure.

## What "needs testing on more PDFs" means in practice

This has been tuned and tested against three real estimate formats
(Allstate/National Catastrophe Team, a "Property Insurance Experts"
appraisal counter-estimate, and Travelers), which cover meaningfully
different column layouts. The next carrier PDF you feed it that isn't one
of these will probably produce a few `needs_review` items or a totals
warning the first time -- that's expected and is the system working as
designed, not a failure. When that happens, save the PDF and its warnings
so a fix can be tested against it without breaking the ones that already
work.

## Known, deliberate limitations (not bugs)

- Two of the three parser test fixtures are *trimmed excerpts* of longer
  real documents (kept short so the repo stays readable) -- their totals
  checks are pinned to a known, expected mismatch caused by that
  trimming. The third (Allstate) is the complete document and holds a
  strict zero-warnings standard.
- Section/room names attached to each line item (e.g. "Dwelling Roof",
  "Bathroom") are best-effort, for display grouping -- not used for the
  financial totals check, which works independently off the document's
  own printed subtotals.
- Depreciation/ACV/age/condition are parsed and kept (needed for the
  totals check and for an audit trail), and the "Insurance RCV" column
  in the editing workspace is shown for reference -- but per the project's
  legal notes, none of that is what the contractor charges, and the
  branded proposal export never includes those columns at all (see above).
- The trade guessed per line item (`trades.py`) is a starting-point
  suggestion based on keywords in the description, not something the
  parser extracted from the PDF -- always editable in the table.
- Adding a brand-new row in the editing table (for something the carrier
  missed) works via the built-in "+" row at the bottom of the table; it
  starts blank and needs Trade/Qty/Unit/Unit Cost filled in.
- Your business info (sidebar) lives only in the current browser session
  -- there's no login or saved profile yet, so it resets if the app
  restarts. That's what step 4 (accounts) adds.
- `claim_flags.py` is pattern-based and best-effort, same as the trade
  guesser and the "needs review" checks -- a flag not being set means
  "not found in this document's text," never "confirmed absent." See
  "How claim context recognition works" below for what that means in
  practice for each flag.

## How sales tax works

Added after digging into how this math actually works for a real Texas
job (see the "Claim Ledger" reference doc, and the project's "Known
parsing problems" doc, problem #4). This is a completely separate
calculation from anything the carrier's PDF printed -- it's based on
*your* price and *your* contract type, never on the carrier's own `tax`
column (which stays insurance-only, same treatment as depreciation/ACV).

In the "Sales tax" section of the editing workspace, pick how the job is
actually contracted:

- **Separated contract, residential** -- tax applies to materials only.
  Use the new "Material" checkbox column to mark any pure-labor line item
  so it's excluded.
- **Lump-sum contract, residential** -- you already paid tax on materials
  when you bought them; it is never itemized back to the client, so no
  tax line appears anywhere on the proposal at all.
- **Commercial / nonresidential** -- the whole price (materials and
  labor) is taxable.
- **No sales tax** -- for anything outside Texas, or a genuinely
  tax-exempt job. (This app only encodes Texas's residential/commercial
  rules; check your own state's requirements before relying on this for
  a job elsewhere.)

Nothing is taxed until you explicitly pick a rule -- the default is "No
sales tax," so an existing workflow doesn't change under you. The
"Insurance O&P" reference column works the same way as "Insurance RCV":
it shows what the carrier already priced into that line for overhead and
profit, purely so you can see it while setting your own margin -- it
never gets added into your price automatically, which is what avoids the
double-compounding trap the project's notes warn about (problem #5).

## How claim context recognition works

Added while digging into the claim *process* -- not just the RCV/ACV math,
but the stuff that decides how much of that math the contractor actually
sees a check for (see the "Claim Ledger" reference doc, sections 8-15).
`scope_parser/claim_flags.py` reads the document's own text -- the same
noise-filtered text `line_items.py` parses, after the boilerplate
"how to read your estimate" insert page has already been stripped out --
and looks for a fixed set of real signals. Nothing here is a guess or an
AI summary; every flag is either a specific phrase match or a calculation
against numbers actually printed on the document, and every flag that
fires adds a plain-language line to the "Claim context" panel at the top
of the editing workspace explaining what was found and why it matters,
with a pointer back to the relevant Claim Ledger section.

What it recognizes:

- **Percentage vs. flat deductible.** Most Texas wind/hail deductibles
  are printed as a dollar amount on the estimate with no indication
  they're actually a percentage of the dwelling limit. This finds the
  "Coverage / Deductible / Policy Limit" table (or falls back to the
  claim-summary "Less Deductible" line when there's no table), divides
  the two printed numbers, and checks whether the result lands on one of
  the common percentages (1%, 1.5%, 2%, 2.5%, 3%, 4%, 5%, 10%). If there's
  a deductible but no policy limit anywhere on the document, it says so
  honestly instead of guessing -- the same anti-guessing rule the
  "needs review" column already uses for line items.
- **Mortgagee / lienholder mentioned** -- "Mortgagee," "loss payee,"
  "ATIMA," "lienholder." Affects who the claim check gets made out to.
- **Ordinance-or-Law coverage mentioned** -- "Ordinance or Law,"
  "Increased Cost of Construction," "code upgrade coverage." This is the
  coverage bucket that's supposed to fund code-driven items (see below).
- **Cosmetic damage exclusion mentioned** -- "HO-145," "cosmetic damage
  exclusion." Matters most on a roof: if it applies, there may be no
  depreciation to recover on it regardless of how fast repairs happen.
- **What kind of document this is** -- whether the text repeatedly refers
  to itself as an appraisal (an independent appraiser's estimate, not the
  carrier's own adjuster), mentions a public adjuster, or reads like a
  supplement/reinspection rather than an original estimate. These change
  how you should treat the totals, not just what they say.
- **Code-driven line items.** Building on the existing per-line-item
  code-citation check (an IRC/IBC section number in a description or
  note), this rolls those up into a total: how many line items, and how
  much RCV, cite a specific code section. A second, document-wide count
  also catches code citations printed as scope language *before* the
  first numbered item (real carrier PDFs do this) -- those never attach
  to one specific line item, so the per-item total alone would miss them.
  A new "Code Cite" checkbox column in the editing table marks which
  individual rows triggered it.

Every one of these was checked against the real carrier fixtures in
`tests/fixtures/` before being trusted, not just against invented
examples -- e.g. the Travelers fixture's actual printed numbers
($5,300.00 deductible / $265,000.00 dwelling limit) really do compute to
exactly 2%, and the Williams1 fixture really is written as a
self-described appraisal.

Two honest limits worth knowing: absence of a flag means the specific
phrase or number pattern wasn't found in *this* document's text, not that
the thing is confirmed absent -- a mortgagee can exist on a claim without
the word ever appearing in an Xactimate-style repair estimate, which
usually isn't where that information lives. And when a deductible is
found with no policy limit to compare it against, always check the
declarations page yourself rather than trusting a percentage the app
can't actually confirm.

## How the app knows which program wrote a PDF

Every PDF carries a bit of hidden file-level information that has
nothing to do with anything printed on a page -- what macOS's "Get Info"
window, or Adobe's Document Properties panel, shows you: who/what
created the file, and when. `scope_parser/metadata.py`'s
`fields_from_pdf_info()` reads that straight out of the file (via
pdfplumber's `.metadata`) and it turns out Xactimate's own PDF export
stamps its exact name and version into that field -- confirmed on
Glenn's real PDFs, which carry `Creator: Xactimate 24.4.1001.1` and
`Creator: Xactimate 24.6.1000.2` respectively. That shows up as a small
caption under the claim info once a PDF is loaded: "Written in Xactimate
24.4.1001.1 · file created 2024-07-22 18:32:17".

This came out of a genuinely useful catch: it's a far more reliable way
to tell "which program wrote this" than anything guessable from the
page text itself (column headers, section-total phrasing, and so on --
see the "Beyond Xactimate" reference doc). When BuildUpQuote eventually
needs to support a second carrier format, checking this field first,
before any column-schema guessing even starts, is the obvious place to
begin -- though it's not wired up as a format switch yet, since only one
format is supported today regardless of what this field says.

One honest limit: this only works when the PDF-writing program actually
bothers to stamp a Creator string, and only when whatever service later
touches the file (compression, a scanner, a "print to PDF" step) doesn't
overwrite it with its own tool's name instead. Absence of a clear
`source_program` here means "not stated in this copy of the file," same
anti-guessing rule as everything else in this app -- not proof the
document didn't come from Xactimate.

## Adding a line item the carrier missed, and where trade subtotals live

Two related changes, both about a job with several trades on it.

**Adding a line item.** There are no add-item buttons or panels anymore --
the way you add a line is the same everywhere: click the "+" row at the
bottom of any editable table (Scope, Review's "Added by you", or Pricing)
and type Description/Trade/Qty/Unit/Unit cost/Margin into it.
`app._editor_row_to_manual()` shapes what you typed into the exact same
columns `_rows_from_estimate()` produces from a parsed PDF, so the line
flows through pricing, trade totals, and the proposal export identically
to a carrier line -- it's just missing the three insurance-reference
columns (Insurance RCV, Insurance O&P, Code Cite), since there's no
carrier line behind it to reference. That missing RCV is exactly what
makes it the counter-offer: the proposal PDF's Payment Schedule reads a
line with no carrier RCV as a SUPPLEMENT -- the contractor's own price
beyond what the carrier priced. The line gets an "A#" label and lands on
the Review tab's "Added by you" list.

**Trade subtotals at the bottom.** The proposal PDF used to print a
"Subtotal — Roofing" (etc.) row right after each trade's own items,
scattered through the document -- on a job with many trades, or one
long enough to span several pages, those add-up-to-nothing-obvious rows
were easy to lose track of. They're now collected into one "Summary by
Trade" table that sits right above Subtotal/Sales Tax/Total Contract
Price, in the same place the "Totals by trade" table already shows them
on-screen while you're editing (`app._trade_totals()`). A job with only
one trade skips the summary entirely, since it would just repeat the
grand total.

## Large files: what to actually expect

A 70MB, 167-page claim doesn't need to be shrunk before uploading --
Streamlit's own upload limit defaults to 200MB, well above that, and
nothing in this app lowers it. If a big file looks stuck with a blank
screen and no spinner, that's a real bug (please report it), but a
sketch-heavy claim genuinely does take real time to read, and it used to
give zero feedback while doing so, which looked identical to "frozen."

Measured directly: a 167-page synthetic PDF (2,100 line items, roughly 1
sketch-style page in 6, matching the pattern in Glenn's real sample PDFs)
took about 26 seconds just for `pdfplumber` to pull the text out --
almost all of it was that library building an object for every line
segment on the sketch pages (measured at ~5x slower per page than a
plain text page). The app's own line-item parsing after that point is
fast even at this scale: well under a second for over 2,000 lines. A real
70MB/167-page file is very likely heavier than this synthetic test in
photos and per-page content, so a few minutes of genuine processing time
before anything shows up is plausible and not a sign of anything broken.

The fix that shipped: the file uploader now shows a spinner with an
explicit "this can take a few minutes, don't refresh" message once a
file is over 15MB, instead of leaving the page looking blank while
`parse_pdf()` runs. If a big file still never finishes after several
minutes and the spinner disappears with no error message and no results,
that *is* worth reporting -- check the Terminal window it's running in
first, since a real Python error there (rather than a silent hang) means
something else broke.

If pages-with-a-lot-of-content parsing time ever becomes a real
bottleneck rather than just a perception one, the likely next step is
swapping `pdfplumber` for `PyMuPDF` (fitz) in `extract.py` -- it's
reported to be significantly faster at raw text extraction for exactly
this reason, but wasn't installable in the sandbox this was built in (no
PyPI access there). Worth trying on a machine with normal internet
access if it comes to that.

## The Texas deductible-waiver notice

Texas Business & Commerce Code Sec. 27.02 requires a contract of $1,000+,
where the seller expects to be paid from property insurance proceeds, to
carry an EXACT notice (not a paraphrase) in at least 12-point boldfaced
type, and prohibits a seller from waiving, absorbing, or helping a
homeowner avoid their deductible -- a Class B misdemeanor if violated.
Nearly every real restoration job clears $1,000, so this now prints
automatically on the proposal (see `TX_DEDUCTIBLE_NOTICE` in
`proposal/models.py`) whenever `total_price >= 1000`, in its own
distinct box at 12pt bold -- not folded into the general terms
paragraph, since the statute's own formatting requirement has to survive
independent of whatever the rest of the document's type scale does.

This corrects a citation the project's own legal notes doc had wrong
twice: first as "Texas Insurance Code § 707.002 / HB 2102," then
self-corrected to "Insurance Code § 27.02" -- both wrong on the code
*title*. Verified directly against the statute text (not just a
paraphrase or a blog post) 2026-08-24: § 27.02 is **Business and
Commerce Code**, and it's the section that actually binds a contractor
and requires this notice. Insurance Code Ch. 707 is a real, separate,
correctly-cited provision -- it obligates the *insured* to pay their own
deductible, and both get cited together on the Payment Schedule's
deductible line (see below) since they're the two halves of the same
rule, not competing citations for one.

## The carrier's own bottom line

The top of the Pricing tab shows a small reference panel, straight off the
document's own summary page: the carrier's Line Item Total, its Overhead +
Profit percentage (already baked into its own Replacement Cost Value), and
that Replacement Cost Value itself. This never changes "Your Price" --
it's there so you can see the document's own math next to yours and know
at a glance whether they're in the same neighborhood.

A "Match the carrier's X% markup" button sets your margin slider to that
same percentage on every row, as a starting point -- exactly the same as
typing that number into the slider yourself and clicking "Apply to every
row," just without having to read it off the PDF and do the arithmetic
first.

If the document also prints a deductible, the panel adds one line: what
the carrier's own "Net Claim" (or "Net Estimate," depending on the
program) reads once that deductible is subtracted. That is what insurance
actually pays out -- not the cost of the job -- and your price above it
should stay at the full repair cost regardless. This is a different
number from the "Payment Schedule" section further down the tab, which is
about how *your* total gets paid out in stages; this one is purely what
the carrier itself printed, for comparison.

Not every document has all of this. A document with more than one
coverage (Dwelling, Other Structures...) only shows the Line Item Total
for the coverage its Summary block is scoped to -- the panel says which
one. A document that never prints a document-level Overhead/Profit line
at all (Allstate's own fixture is one -- see below) simply doesn't show
that metric. Nothing here is ever computed or guessed; every figure in
this panel is copied straight off a line the document itself printed. See
`scope_parser/carrier_summary.py`.

## The Texas code checklist, and the "⚖️ Code Additions" tab

Glenn pasted in a reference guide of Texas construction code requirements
on 2026-08-25 (saved verbatim as the project doc
`claude/texas-building-codes-reference.md`), asking for a way to flag
which ones aren't showing up in a given PDF, and an easy way to add one
in -- as its own priced item, not folded into the carrier's scope, since
this is something the contractor has to add **by law**, not a choice.
That's `code_checklist.py` plus its own tab, **⚖️ Code Additions**, between
Review and Pricing.

29 items across four categories -- Roofing/Flashings/Envelope,
Framing/Drywall/Fire Separation, Electrical/Mechanical/Plumbing/Energy,
and Workplace Safety (OSHA, included per Glenn's call: fall
protection/scaffolding is sometimes its own billed line on a steep-slope
job, not just an absorbed overhead cost). Each is checked against every
description currently in your scope table -- carrier lines, lines you
added, and code additions already added, whether or not they're
currently checked "Include" -- by plain keyword matching (e.g. "drip
edge" for IRC R905.2.8.5). A ✅ means a line mentioning it is already in
your scope; a ⬜ with an "Add" button means none is, yet.

**This is a reminder, not a finding.** A ⬜ never means the code
requirement isn't being met on the actual job -- it only means nothing in
this proposal's *wording* matches yet, and plenty of these items don't
apply to every claim at all (there's no garage on this job, no dryer work
in scope, the roof isn't low-slope...). Same anti-guessing rule as
`needs_review` and `claim_flags.py` everywhere else in this project:
absence of a match is never treated as a confirmed fact about the world,
only about what this proposal currently says. It's on the contractor to
decide which items actually apply to a given job.

**Add opens a form, right under that item.** No popup -- clicking "Add"
expands a small form in place with two independent halves:

- **A priced line for the item itself** (quantity, unit, unit cost,
  margin -- same fields as the "Add a line item the carrier missed"
  form). Nothing is ever pre-filled with a cost; that's still yours to
  set.
- **Labor**, as its own separate line: workers, hours, and a rate per
  hour (e.g. 8 workers, 30 hrs, $10/hr). There's no "per contractor"
  default rate yet -- Glenn's own framing was that this becomes
  something set per user once accounts exist -- so it's typed in each
  time for now. Submitting the form doesn't close it: clicking "Add"
  again with a new crew or a new day's hours adds a second labor line
  for the same item, which is how a job needing more than one crew or
  more than one day gets each one billed separately rather than
  averaged into one number.

Every code-required line -- material or labor -- carries the code item's
own plain-English requirement text as its Review Note, so the reason for
the line and what the code actually requires travels with it wherever
that row is shown.

These rows are labeled `L1`, `L2`... (never `A1`, `A2`, which is reserved
for a line you typed in yourself on the Review tab) and live in their own
"Added by law" list at the bottom of the Code Additions tab -- kept
separate from the carrier's scope and from your own additions, but priced
and exported exactly like any other line, so the total contract price and
the proposal PDF both include them.

Each category's expander only auto-opens if a trade already in your scope
suggests it's relevant (e.g. Roofing items open automatically once
there's a Roofing line in the scope) -- otherwise it's collapsed by
default so 29 items don't dump onto the screen on every claim. A small
"Which codes apply in Texas" reference note above the checklist covers
the parts of Glenn's source guide that aren't checklist items at all --
which code a city has adopted, and the coastal windstorm-zone rule.

One correction worth flagging: Glenn's pasted reference guide lists "Tex.
Ins. Code § 707.002 (HB 2102)" as the deductible-waiver law. That's the
exact citation this project's own notes had wrong in an earlier round --
see "The Texas deductible-waiver notice" above. The real deductible-waiver
law is **Business & Commerce Code § 27.02**, already enforced elsewhere
(it prints automatically on every proposal PDF over $1,000). It's
deliberately left OUT of this checklist rather than repeated with the
wrong citation -- `tests/test_code_checklist.py` pins that "707.002"
never appears in the checklist's reference text.

**Bigger tabs.** Glenn's other ask this round (2026-08-25): "make it so
the menu items are bigger and easier to see." The five tabs across the
top (`ui.py`'s `.stTabs` styling) are now taller, larger type, and bolder
than Streamlit's default -- they're the app's main menu, so they're the
first thing meant to be easy to read at a glance.

## Payment Schedule

The total contract price isn't paid all at once, and showing it as one
number hides that from a homeowner. Spec here came from a 15-year
contractor, confirmed against data the parser already captures per line
(`depreciation`/`depreciation_recoverable` in `scope_parser/models.py`).
Both the on-screen "Payment breakdown" section and the exported
proposal's "Payment Schedule" split the total into up to four real
stages, in the order they typically arrive:

1. **Deductible** -- owed directly to the contractor by the homeowner,
   in full, by Texas law (see above). Never paid by insurance.
2. **Due on the first insurance check** -- everything left over. Always
   computed as a REMAINDER (total minus the other three parts), never
   independently off the carrier's own ACV figure -- confirmed with
   Glenn this is what keeps the four parts summing exactly to the total
   shown elsewhere on the document, with no unexplained gap.
3. **Recoverable depreciation** -- the insurance company's OWN fixed
   figure, summed off the carrier's parsed line items (currently-included
   rows only), paid on the second check once repairs are complete and
   proof is submitted. Deliberately NOT scaled by the contractor's
   margin -- confirmed with Glenn this reflects what insurance actually
   pays out, not a cut of the job.
4. **Supplements** -- currently-included rows with no carrier line behind
   them, at the contractor's own price. Identified by "Insurance RCV"
   being blank (every parsed carrier row has a value there, even $0.00 --
   only a hand-added row, via the "Add a line item" form or the Scope
   table's own raw "+", leaves it empty). Per industry norm, paid on
   completion once approved.

The proposal's Payment Schedule always shows parts 1 and 2; parts 3 and 4
only appear when nonzero, matching how a real job is usually described
as having "2 to 3 parts," not always all 4. If the deductible, recoverable
depreciation, and supplements together exceed the total price -- an edge
case worth actually seeing rather than hiding -- the on-screen version
shows a red warning with the exact overage instead of a silently-wrong
negative number.

## Export file names

Both download buttons (CSV and the proposal PDF) used to always save as
`scope.csv` / `proposal.pdf`, which turns a folder of downloaded jobs into
"proposal.pdf", "proposal (1).pdf", "proposal (2).pdf"... indistinguishable
without opening each one. Per Glenn's request, both now build a file name
out of whatever claim info is on hand, in this order: contractor name,
insurance company, claim number -- e.g.
`Acme_Roofing_State_Farm_0761262757.pdf`. Special characters (like the
"&" in "State Farm & Co.") are stripped rather than left to turn into
percent-encoded junk, and any piece that's missing (claim number not
parsed yet, no insurance company found) is just skipped rather than
baking in a literal "--" or crashing -- if every piece is missing, it
falls back to the old plain name (`scope.csv` / `proposal.pdf`). See
`_slugify()` and `_export_filename()` in `app.py`.

## Reading estimates from programs other than Xactimate

Xactimate writes roughly 80% of the estimates in this business. The other
20% come from Symbility/Cotality, Simsol, contractor CRMs and a long tail
of one-off tools -- and until now this app could only read the first
group. It can now read all of them, without any of that work touching the
Xactimate path that was hardened against three real carrier PDFs.

The design in one line: **which program wrote a file, and what kind of
document it is, are two separate questions -- and the second one decides
what the app is allowed to do with the numbers.**

### Rule sheets, not `if` statements (`scope_parser/profiles.py`)

Every format-specific decision the parser makes now lives in one place, as
data: the unit vocabulary, the column header words, the row-start pattern,
the subtotal wording, the page furniture, the boilerplate page markers.
The engine reads a sheet; a sheet never reads the engine.

The alternative -- writing `if program == "cotality":` inside the six
modules that used to hold these values -- would have threaded the
Xactimate path with branches that only run for other formats, and every
future change would have to reason about all of them at once. A rule sheet
cannot do that; it has no ability to change behaviour for anyone but
itself.

The Xactimate sheet was built by MOVING those constants, not retyping
them. Same values, same code, same output -- proven, not assumed, by the
golden-snapshot lock described under "How this catches its own mistakes".

`profiles.SYMBILITY_DRAFT` exists but is deliberately NOT in the registry.
It was written from published format descriptions rather than from a real
PDF parsed here, so it can NAME a Symbility document without being trusted
to read one. It moves into the registry when a genuine Symbility estimate
is checked in as a fixture with its own snapshot.

### Which program wrote this? (`scope_parser/fingerprint.py`)

The PDF's own metadata is the strongest single signal -- Xactimate stamps
its exact name and version into the file -- but it cannot be the only
vote. A real Liberty Mutual estimate priced off Cotality carries
`Producer: Microsoft: Print To PDF`: the estimating program's name never
reached the file. Any document that has been printed and re-saved, pulled
from a claims portal, merged with a photo report, or scanned loses that
stamp, while the printed page keeps every clue it ever had.

So seven signals are scored together, metadata heaviest: the
Creator/Producer string, a program name printed on the page, the
price-list index, the column-header vocabulary, the subtotal phrasing, the
item-numbering style, and the unit vocabulary. Highest score above a floor
wins; nothing clears it and the generic reader takes over. Every signal
that fired is recorded and shown in the app under "How we worked out what
this file is" -- a routing decision nobody can explain is the same failure
as a parsed number nobody can trace.

If the metadata and the page disagree about which program wrote a file,
the metadata's answer is kept, confidence drops, and the app says so. Two
signals disagreeing is information, not a tie to break quietly.

**The price-list index also names the state.** `TXHO8X_AUG24` prices work
in Texas; `CTHA7X_OCT11` prices it in Connecticut. This app's tax rules and
the deductible notice on the exported proposal are both Texas law, applied
unconditionally -- so on a non-Texas estimate the app now warns, on screen,
before anything gets printed onto a contract.

### The generic reader (`generic_reader.py`, `generic_columns.py`)

For a program nobody has taught it about, the parser can't rely on column
names it recognises. So it doesn't read the columns -- it solves for them.

`tokens.find_qty_and_unit()` was already completely format-blind: every
estimating program prints a quantity next to a unit. Everything left of
that anchor is description; everything right of it is columns of figures
whose names we don't know. Then, for each section:

    quantity x price  +  every numeric column between price and total  =  total

Find the two column POSITIONS where that holds for every row in the
section. One row can balance by coincidence; twenty cannot. The extra term
matters because several carriers print tax and overhead-and-profit columns
that are added into the printed line total -- and it is exactly why your
margin is applied to the unit price rather than to that total.

Three things deliberately produce "flag it" rather than a guess: every
quantity on the page being 1.00 (price and total become indistinguishable),
nothing balancing at all, and rows priced at 0.00 (which satisfy
everything, so they are excluded from the vote rather than allowed to
decide it). A quantity-of-1 row can never outvote a row that actually
proves the relationship -- without that rule, on the real appraiser
fixture the replacement-cost column was being read as the unit price,
because replacement cost equals actual cash value whenever depreciation is
zero.

**How well it works**: told nothing whatsoever about Xactimate, the generic
reader reproduces the header-driven parse on all three real fixtures --
27/27 items on Allstate, 19/19 on Travelers (including its tax and O&P
columns), and 26/27 on the appraiser estimate. The one exception is a row
with no printed quantity, which it flags instead of inventing one. Those
comparisons are the tests in `tests/test_formats.py`, not a claim.

It is a separate scanner from `line_items.py` on purpose. The two use
genuinely different strategies for the one thing they can't share, and
keeping them apart means work on the generic reader can never disturb the
Xactimate path. Everything they could share, they do: the anchor, noise
filtering, measurements, the totals check, claim flags and metadata are
all common code.

### What kind of document is this? (`scope_parser/doc_type.py`)

Five kinds turn up in this business, and two of them are hazardous:

**The money bug -- a contractor's own proposal.** Someone will upload last
year's proposal, a competitor's, or their own. The generic reader's
arithmetic succeeds perfectly on it, because quantity x price = total
holds. It just holds on a price that already contains a contractor's
markup. The parse looks clean and the number is quietly wrong, with margin
charged twice. No carrier prints a "Markup %" column, so that column is
the tell -- and a document carrying it now loads with margin locked at 0%,
with the reason on screen.

**The trust bug -- a settlement statement of loss.** It has no line items
because it was never a scope; it shows what the carrier is paying, not
what work is in the job. Left alone it landed on "couldn't recognise this
layout", which reads as a broken app when nothing is broken. It now names
what the document actually is and says to ask the adjuster for the scope
document instead.

The other three -- carrier adjuster scope, independent appraisal report,
and supplement/counter-quote package -- are recognised and labelled too. A
supplement package puts two scopes side by side with a delta column, so
the app says which question it needs answered rather than reporting the
file as unreadable.

### How much to trust this parse (`scope_parser/confidence.py`)

Every upload gets exactly one of four verdicts, shown at the top of the
workspace. There is no silent parse.

- **Recognised** -- a known format, everything parsed, every printed
  subtotal reconciles.
- **Read generically** -- parsed and reconciled, with any flagged rows
  called out to check first.
- **Needs your eyes** -- the figures don't agree with the document's own
  subtotals. The wording differs depending on whether the format is known
  (so the arithmetic is what disagrees) or unknown (so the columns may be
  being read wrongly).
- **Not a scope** -- see the settlement statement above.

The evidence behind all of this is the document's own printed subtotals,
checked by `totals.py`. That check is completely format-blind, which is
what makes a confidence score meaningful for a format nobody has ever
taught us.

Rows the parser wasn't sure about are listed in a "lines to check first"
panel at the top, but they stay in document order in the Scope table --
deliberately, so a contractor can compare the table against the PDF side
by side without the rows having been shuffled.

### Two real documents that used to fail (2026-08-25)

Both were sent in because the app couldn't read them. Both are now
permanent fixtures with their own golden snapshots.

**A contractor's Xactimate export** (`tests/fixtures/contractor_doyle.txt`).
Same program as the carrier estimates, completely different printed
layout: `DESCRIPTION QTY RESET REMOVE REPLACE *TOTAL` -- three action cost
columns instead of one price column, no UNIT header at all, and a line
total that already includes sales tax.

It used to parse 131 items with the money scrambled: unit prices read as
line totals and line totals read as prices. That is worse than failing,
because it looked like it had worked. Two things make this layout
different from every other one here:

- *Empty cost cells don't print.* A row with no RESET cost extracts as two
  numbers plus the total, not three plus the total, so the columns cannot
  be mapped by position. What can be relied on is that the last number is
  the line total and the ones before it are costs -- and their **sum** is
  the row's real unit price. An R&R line costing $1.77/SF to tear out and
  $2.96/SF to install is a $4.73/SF line.
- *The total is tax-inclusive* ("* Price is inclusive of sales tax paid at
  point of purchase"), so quantity x cost never equals the printed total
  on a row carrying materials. The gap is recorded as tax rather than
  folded into the price, so your margin still applies to the cost of the
  work. A row whose gap is negative, or larger than any plausible tax
  rate, is flagged instead.

It now reads all 131 line items with nothing flagged, every section
subtotal reconciles, and the total comes to $57,976.93 -- the figure the
document prints for itself.

**A Liberty Mutual claim priced off Cotality**
(`tests/fixtures/symbility_libertymutual.txt`). This one produced exactly
one phantom line item, and the reason is a single ordering difference:
Symbility prints `quantity, unit price, unit` -- "6.00 $1.63 LF" -- so the
quantity is followed by a price, not by a unit, and the anchor the whole
parser is built on never fired. `tokens.find_anchor()` now knows both
orderings and takes whichever appears first.

Three more things about that document were worth fixing properly:

- **Bracketed ordered quantities.** "22.21 (22.33)" means bundle rounding
  was applied and the line is priced on the bracketed figure. Pricing off
  the measured 22.21 leaves that line $12.96 short -- and losing the line
  entirely, which is what happened, left the roof subtotal out by exactly
  $2,606.01. The measured quantity is kept in the line's notes rather
  than discarded.
- **Plan measurements that look like priced rows.** "Roof area: 2,799.23
  SF Squares: 28.0 SQ Soffit: 690.70 SF" has a number, a unit and more
  numbers after it, so it read as a $690.70 line item -- which was exactly
  the remaining gap in that section. A row carrying a `Label:` before its
  figures is never a priced row, and that is now a veto.
- **Different label wording.** `CLAIM NO.`, `Policy No.`, `Type of Claim`,
  `Loss address` -- and in small caps, where the parser was matching case
  sensitively. A claim number was coming out as
  "060929297 INSURED: Tammy Cobb", because the value ran on through a
  label nothing recognised. Labels are matched case-insensitively now, and
  several fields exist in that list purely as BOUNDARIES so a value stops
  where the next one starts. The loss address and the insured's mailing
  address are kept apart, because the proposal is written about the
  damaged property.

It now reads 21 line items with one flagged (a minimum-charge adjustment
that genuinely doesn't multiply out), and every printed subtotal
reconciles -- the roof plan to $17,811.00 exactly.

That document's file metadata says `Producer: Microsoft: Print To PDF`,
with no estimating program named anywhere in the file. It is identified as
Symbility/Cotality entirely from the printed page, which is the case that
made metadata-only routing the wrong design.

### The missing Overhead/Profit summary block (2026-08-25)

Sending the same two documents back through a second time surfaced a
different gap, not in either parser but in what the app ever showed on
screen. Every carrier estimate prints a "Summary" block once, after all
the line items -- Xactimate calls it "Summary for All Items"; Symbility
prints an unlabelled version of the same ladder at the end of its own
totals page. Nothing in this codebase ever read it. The app's total was
always just the sum of the line items, which is right for the *scope* but
wrong for the *claim* whenever the document adds a document-level
Overhead/Profit percentage on top.

On the Doyle contractor export, that gap was $11,595.74 -- the parser's
own $57,976.93 in line items was correct, but the document's own summary
page adds 10% Overhead + 10% Profit on top of that, landing on
$69,572.67. Nothing before this fix ever showed that second number
anywhere.

`scope_parser/carrier_summary.py` reads that block now (`Line Item Total`,
`Overhead`, `Profit`, `Replacement Cost Value`, `Net Claim`, and the
depreciation/deductible/ACV ladder around it, whichever ones the document
actually prints), and the Pricing tab shows it as a **reference-only**
panel with a "Match the carrier's X% markup" button that pre-fills your
own margin slider -- see [The carrier's own bottom
line](#the-carriers-own-bottom-line) below.

The one real trap here: a document with more than one coverage (Dwelling,
Other Structures, Personal Property...) prints a **separate** Summary
block per coverage, each headed "Summary for `<coverage>`". The Allstate
fixture's `Line Item Total 14,410.37` is Dwelling only; the document's
actual total across every coverage is $16,556.23. Comparing our own
whole-document parsed sum against a single coverage's total would flag a
correct parse as broken, which is exactly backwards -- so
`carrier_summary.py` records which coverage a block is scoped to
(`coverage_label`), and the app only cross-checks its own sum against the
document when there's no coverage split to account for.

## The workspace: four tabs, colour, and a search box

Rebuilt 2026-08-25. The screen used to be one long scroll with a
fourteen-column table in the middle of it. It is now four tabs, and the
tables show what you actually edit.

### Colour lives in two files

`.streamlit/config.toml` sets the five values Streamlit uses for its own
widgets -- buttons, sliders, checkboxes, tab underlines, focus rings.
Nothing else can reach those. `ui.py`'s `PALETTE` repeats the same five so
the custom CSS matches, and adds the status colours Streamlit has no
concept of (good / warning / bad / info / "added by you"). **Change one,
change the other**, or the app goes half-repainted.

The CSS styles only `data-testid` hooks, which Streamlit keeps stable
across versions, rather than its internal class names, which it doesn't.
Anything that stops matching after an upgrade quietly does nothing -- the
app keeps working, it just looks plainer. None of it is load-bearing.

### The four tabs

**📋 Scope** -- every line the carrier priced. Eight columns by default:
`#`, Include, Description, Qty, Unit, Unit Cost, Margin %, Trade. The
Section, Material, Insurance RCV, Insurance O&P, Code Cite and review
columns are one "Show reference columns" toggle away. They are still
there; they are just not in your way while you price a job.

**🔎 Review** -- the two kinds of line that aren't plain carrier rows,
each in its own table: **what you added** and **what the parser flagged**.
This is also where the "Add a line item the carrier missed" form lives,
since that is where added lines end up. Everything on this tab is priced
and exported exactly like a carrier line -- splitting the screen does not
split the money, and there is a test that says so.

**💵 Pricing** -- contract type and sales tax, the totals, your price line
by line, totals by trade, and the four payment stages.

**📤 Export** -- name the file, then download.

### The `#` column

Every table starts with the carrier's own printed line number. That is
what makes it possible to put this screen beside the PDF and check a
figure in a couple of seconds -- which is the whole job. Lines you add
have no carrier number, so they are labelled `A1`, `A2`... and can never
be mistaken for one. The next `A` number is worked out from the labels
already in use rather than from a row count, so deleting an added line
can't make the next one collide.

### Search

Every table has one. One box, no syntax: it matches any column, so a
description, a trade, or a quantity read straight off the PDF all find the
line. Several words must all appear somewhere in the row, in any order --
"walls paint" finds "Paint the walls" the same as "paint walls" does.
A filtered table says how much it is hiding ("Showing 4 of 131 lines").

Edits made while a search is active are written back to the master table
**by index**, touching only the rows on screen. That is the one piece of
this worth knowing about: replacing the table wholesale, which is what the
obvious implementation does, would delete every row the current filter
happens not to be showing.

### Naming your downloads

The Export tab has a file-name box. It starts from your business name, the
carrier and the claim number -- the same automatic name as before -- and
you can type over it with whatever you'd rather find in your downloads
folder. Both files use it. Characters your computer refuses (`/ \ : * ? "
< > |`) are stripped, a duplicate extension is not doubled, and an empty
box falls back to `scope.csv` / `proposal.pdf` rather than producing a
file called `.pdf`. Uploading a new claim clears the box back to the new
default.

### What can't be tested here

`app.py` still needs a real browser to judge. What the suite does cover
without one: every decision `ui.py` makes (`tests/test_ui.py`), the row
numbering and the Review-tab masks (`tests/test_app_logic.py`), and the
structure of `app.py` itself (`tests/test_app_structure.py`) -- which
reads the file's own scope table and fails on any name a function reads
that nothing defines. That last one exists because a crash shipped: a
helper was inserted mid-`main()`, which silently truncated it, and every
test still passed.

## Not done yet

A real Symbility/Cotality rule sheet. The draft in `profiles.py` is built
from published format descriptions, not from a real PDF parsed here, so it
identifies those documents but hands them to the generic reader. It gets
promoted once a genuine Symbility estimate is checked in as a fixture with
its own golden snapshot.

A format report: when an unrecognised file is uploaded, log its
fingerprint only -- creator string, header line, subtotal phrasing, and
nothing from the claim itself -- so the next format to build is chosen
from real demand rather than guesswork.

Accounts/multi-tenant (Supabase), so a business's info and past proposals
are saved rather than living only in the current browser session -- next,
per `architecture-decision.md`. After that: general polish, and revisiting
whether Streamlit is still the right fit for the UI long-term.
