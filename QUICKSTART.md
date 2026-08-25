# BuildUpQuote — starting it up

One page. Everything you need to get the app on screen, and what to do
when it doesn't.

---

## Every time: two commands

Open Terminal, then:

```bash
cd ~/Desktop/buildupquote
source venv/bin/activate
streamlit run app.py
```

Your browser opens by itself at **http://localhost:8501**. If it doesn't,
open that address yourself.

You'll know the virtual environment is on because your prompt grows a
`(venv)` at the front. If it isn't there, `streamlit` won't be found.

**To stop it:** click back in the Terminal window and press `Ctrl + C`.
Closing the browser tab does not stop it — the app keeps running until
you stop it in Terminal.

**To leave the virtual environment:** `deactivate`, or just close the
Terminal window.

---

## Yes — it's still the same local web app

Nothing about how you run it has changed. `streamlit run app.py` starts a
small web server on your own machine and serves the workspace to your
browser at `localhost:8501`. Nothing leaves your computer; no PDF is
uploaded anywhere.

This is one program, not the old setup. The earlier attempts ran a
separate parsing server alongside a hand-written HTML page and had to be
started in two pieces. This one is a single Python app — `app.py` draws
the screen and calls the parser directly.

---

## First time on a new machine

```bash
cd ~/Desktop/buildupquote
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

**Use Python 3.11** (anything 3.10–3.12 is fine). Check with
`python3 --version`. If yours is newer than 3.12, pandas and Streamlit
probably don't have installable builds for it yet — install 3.11
alongside it with `brew install python@3.11` and use `python3.11` in the
`venv` command above.

**One extra install for the proposal PDF:** wkhtmltopdf. `pip install`
won't get it. Homebrew's cask for it is gone, so download the installer
from [wkhtmltopdf's GitHub releases](https://github.com/wkhtmltopdf/packaging/releases)
and double-click it. On Apple Silicon, macOS will offer to install
Rosetta the first time — let it. Without wkhtmltopdf everything works
except the "Download Proposal PDF" button, and the app says so plainly
rather than failing quietly.

---

## Checking nothing is broken

```bash
cd ~/Desktop/buildupquote
python3 -m unittest discover -s tests
```

Expect `Ran 214 tests ... OK`.

If that number drops or `OK` turns into failures, something regressed —
worth knowing before you send a proposal out. To see which test:

```bash
python3 -m unittest discover -s tests -v
```

A failure in `test_golden` specifically means the parser's output on the
three real sample claims changed. That's the tripwire; if you didn't mean
to change anything, don't touch `tools/refresh_golden.py` — the code is
what needs fixing.

---

## When it won't start

| What you see | What it means | Fix |
|---|---|---|
| `command not found: streamlit` | Virtual environment isn't active | `source venv/bin/activate` — look for `(venv)` in your prompt |
| `no such file or directory: venv/bin/activate` | You're in the wrong folder, or venv was never created | `cd ~/Desktop/buildupquote`, then the first-time setup above |
| `Port 8501 is already in use` | It's already running in another Terminal window | Use that window, or `Ctrl + C` there first |
| `ModuleNotFoundError: No module named 'pandas'` | Dependencies not installed in this venv | `pip install -r requirements.txt` |
| Browser shows nothing / connection refused | Server isn't up yet, or stopped | Check Terminal for an error; re-run `streamlit run app.py` |
| `wkhtmltopdf` error on PDF download | Not installed | See the first-time section above |
| App loads but the page looks stale | Streamlit cached an old run | Press `R` in the browser, or `Ctrl + C` and start again |

---

## Using it

1. **Sidebar first** — your business name, address, phone, licence, logo.
   Only the name is required; it's what turns on the proposal PDF button.
2. **Upload a carrier PDF.** A big claim (150+ pages) can genuinely take a
   few minutes to read — the spinner says so. Don't refresh.
3. **Read the banner at the top.** Every upload gets one, and it tells you
   how much to trust what follows:
   - green — a format we know, everything reconciles
   - blue — read with the general reader, and it adds up
   - amber — needs your eyes, go line by line
   - amber — not a scope at all (e.g. a settlement statement)
4. **Work through the four tabs:**
   - **📋 Scope** — every line the carrier priced. Eight columns by
     default; flip "Show reference columns" for the carrier's own RCV,
     O&P, section and the sales-tax material flag.
   - **🔎 Review** — what you added, and what the parser flagged, each in
     its own table. The "Add a line item the carrier missed" form is here.
   - **💵 Pricing** — contract type and sales tax, your totals, totals by
     trade, and the four payment stages.
   - **📤 Export** — name the file, then download.
5. **The `#` on the left of every table** is the carrier's own line
   number, so you can check a row against the PDF in seconds. Lines you
   added show as `A1`, `A2`…
6. **Every table has a search box.** One box, no syntax — type a
   description, a trade, or a number off the PDF.
7. **Name your downloads.** The Export tab starts from your business, the
   carrier and the claim number; type over it with anything you like.

## Where things live

```
~/Desktop/buildupquote/
├── app.py                 the screen you interact with
├── scope_parser/          reads carrier PDFs (no UI code in here)
├── proposal/              builds the branded proposal PDF
├── tests/                 214 tests
│   ├── fixtures/          the three real sample claims
│   └── golden/            frozen parser output — the regression tripwire
├── tools/                 refresh_golden.py, run on purpose only
├── requirements.txt
├── README.md              the full explanation of how everything works
└── QUICKSTART.md          this page
```

Nothing you do in the app writes to these files. Your edits live in the
browser session and disappear when you close it — saving jobs between
sessions is the accounts work that's still to come.
