# Deploying BuildUpQuote to your Hostinger VPS

Everything here assumes an Ubuntu/Debian VPS (the standard Hostinger image).
The app is the FastAPI deployment (`fastapi_app.py`) -- **no Streamlit is
installed or needed on the server**.

## Docker option (fastest, if the VPS has Docker)

The repo ships a `Dockerfile` + `docker-compose.yml`. From the checkout:

```bash
cd /opt/buildupquote
sudo docker compose up -d --build
```

That builds the image (WeasyPrint's system libs are baked in), runs gunicorn
with 4 workers on port 8000 (bound to localhost -- safe behind a proxy), and
restarts on boot. It works, `curl http://127.0.0.1:8000/api/meta` inside the
server confirms it. To update after a `git pull`: `sudo docker compose up -d --build`
again (or `restart` if only the code changed).

*Requires Docker: `curl -fsSL https://get.docker.com | sh` and add your user
to the `docker` group (`sudo usermod -aG docker $USER`, re-login).*

The rest of this file is the non-Docker path (venv + systemd + proxy).


## 0. See it working on your laptop first (optional but smart)

```bash
cd /Users/glenn/Desktop/buildupquote
venv/bin/python3 -m uvicorn fastapi_app:app --host 127.0.0.1 --port 8000
```
Open `http://127.0.0.1:8000/`, upload a PDF, export a proposal. If it works
here, the code is fine and only the VPS plumbing is left.

## 1. One-shot setup (clone + deps + system libs)

Copy `setup.sh` to the VPS (or clone the repo there and run it):

```bash
bash setup.sh /opt/buildupquote https://github.com/GWMarketing/buildupquote.git
```

This installs Python/git + WeasyPrint's native libraries (Pango etc.),
clones the repo, builds the virtualenv, installs `requirements.txt`, and
smoke-tests the imports.

## 2. First run (manual, to confirm before making it permanent)

```bash
cd /opt/buildupquote
./venv/bin/uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
```

From your laptop:
- `curl http://<VPS_IP>:8000/api/meta` should return the tax-rules JSON.
- Open `http://<VPS_IP>:8000/` and run the full flow (upload PDF, edit,
  export proposal PDF + CSV).

If the page won't load, the Hostinger firewall is blocking port 8000 --
open it in hPanel, or skip straight to Step 4 and use ports 80/443.

## 3. Keep it running (systemd)

```bash
sudo cp deploy/buildupquote.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now buildupquote
sudo systemctl status buildupquote        # should say active (running)
```

## 4. Public URL with HTTPS (reverse proxy)

Pick one. **Caddy is the simplest** (auto-HTTPS, no upload-size limit):

```bash
sudo apt install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
# edit /etc/caddy/Caddyfile: replace yourdomain.com with your real domain
sudo systemctl enable --now caddy
```

**Or nginx** (needs an upload-size bump for big carrier PDFs -- already in
the provided config):

```bash
sudo apt install -y nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/buildupquote
# edit the config: replace yourdomain.com with your real domain
sudo ln -s /etc/nginx/sites-available/buildupquote /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# TLS: sudo apt install -y certbot python3-certbot-nginx && sudo certbot --nginx -d yourdomain.com
```

Open ports 80 and 443 in the Hostinger firewall (hPanel).

## Done? Verify like a user

Open `https://yourdomain/`, upload one of your real carrier PDFs, edit a
line, and download both the branded proposal PDF and the CSV.

## The API (in case you want it from your own code)

| Endpoint | Purpose |
|---|---|
| `GET /` | the web app |
| `GET /api/meta` | tax rules + trade options |
| `POST /api/parse` | multipart PDF upload -> parsed rows + claim info |
| `POST /api/totals` | JSON `{rows, tax_rule, tax_rate_pct}` -> live totals |
| `POST /api/csv` | JSON rows -> scope CSV |
| `POST /api/proposal` | JSON rows + business + tax -> branded proposal PDF |
