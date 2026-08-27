# BuildUpQuote — starting it up

One page. Everything you need to get the app on screen, and what to do
when it doesn't.

---

## Every time: two commands

Open Terminal, then:

```bash
cd ~/Desktop/buildupquote
source venv/bin/activate
uvicorn fastapi_app:app --port 8000
```

Then open **http://localhost:8000** in your browser.

You'll know the virtual environment is on because your prompt grows a
`(venv)` at the front. If it isn't there, `uvicorn` won't be found.

**To stop it:** click back in the Terminal window and press `Ctrl + C`.
Closing the browser tab does not stop it — the app keeps running until
you stop it in Terminal.

**To leave the virtual environment:** `deactivate`, or just close the
Terminal window.

---

## Yes — it's the web app

`uvicorn fastapi_app:app` starts a small web server on your own machine
and serves the workspace to your browser at `localhost:8000`. Nothing
leaves your computer; no PDF is uploaded anywhere. The same file is what
runs on the Hostinger VPS, behind Caddy/nginx.

This is one program: `fastapi_app.py` serves both the API endpoints and
the page itself (`web/index.html`).

---

## First time on a new machine

```bash
cd ~/Desktop/buildupquote
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn fastapi_app:app --port 8000
```

**Use Python 3.11** (anything 3.10–3.12 is fine). Check with
`python3 --version`. If yours is newer than 3.12, pandas probably
doesn't have an installable build for it yet — install 3.11 alongside it
with `brew install python@3.11` and use `python3.11` in the `venv`
command above.

**No extra install for the proposal PDF.** The renderer is WeasyPrint,
which comes with `pip install -r requirements.txt` — nothing to
download, no system installer.

---

## Checking nothing is broken

```bash
cd ~/Desktop/buildupquote
python3 -m unittest discover -s tests
```

Expect `Ran 332 tests ... OK`.

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
| `command not found: uvicorn` | Virtual environment isn't active | `source venv/bin/activate` — look for `(venv)` in your prompt |
| `no such file or directory: venv/bin/activate` | You're in the wrong folder, or venv was never created | `cd ~/Desktop/buildupquote`, then the first-time setup above |
| `Port 8000 is already in use` | Already running in another Terminal window | Use that window, or `Ctrl + C` there first |
| `ModuleNotFoundError: No module named 'pandas'` | Dependencies not installed in this venv | `pip install -r requirements.txt` |
| Browser shows nothing / connection refused | Server isn't up yet, or stopped | Check Terminal for an error; re-run `uvicorn fastapi_app:app --port 8000` |
| "Couldn't build the proposal PDF" | WeasyPrint not installed in the venv this app runs in | `pip install -r requirements.txt`, restart the app |
| "cannot load library libpango-1.0" on the VPS | WeasyPrint's Linux system libraries missing | `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core` (the Docker image already has them) |

---

## Using it

1. **Business info first** — name, address, phone, licence, logo. Only
   the name is required; it's what turns on the proposal PDF button.
2. **Upload a carrier PDF.** A big claim (150+ pages) can genuinely take a
   few minutes to read — the status line says so. Don't refresh.
3. **Read the claim panel.** Every upload gets one, and it tells you how
   much to trust what follows: the parsed claim fields, any warnings, and
   the deductible (edit it in the Export panel if the PDF didn't print it).
4. **Work the scope table.** Every line the carrier priced, editable in
   place:
   - `On` — include or exclude the line.
   - `#` — the carrier's own line number, so you can check a row against
     the PDF in seconds. Lines you added show as `A1`, `A2`…
   - Trade, Description, Qty, Unit, Unit Cost, Margin %, Your Price.
   - **Needs Review** — check a line you still want to look at; the badge
     above the table counts them.
   - `＋ Add a line item` — append a row for anything the carrier missed.
5. **Totals.** Update live as you edit — the bar along the bottom shows
   lines, subtotal, tax, and the total; the breakdown under the table
   shows your price by trade.
6. **Export.** Pick the contract type and tax rate up top, set the
   deductible if needed, then name your downloads. The name starts from
   your business, the carrier and the claim number; type over it with
   anything you like.

## Where things live

```
~/Desktop/buildupquote/
├── fastapi_app.py         the server (API + serves the page)
├── web/index.html         the page itself — one self-contained file
├── workspace.py           the pure row/totals/edit logic (no framework)
├── scope_parser/          reads carrier PDFs (no UI code in here)
├── proposal/              builds the branded proposal PDF
├── deploy/                VPS setup: systemd, Caddy, nginx, one-shot script
├── Dockerfile, docker-compose.yml   container build for the VPS
├── tests/                 332 tests
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
