#!/usr/bin/env bash
# BuildUpQuote -- one-shot setup for a fresh Ubuntu/Debian Hostinger VPS.
#
#   bash setup.sh [/opt/buildupquote] [https://github.com/GWMarketing/buildupquote.git]
#
# Installs system packages (Python, git, WeasyPrint's native libraries),
# clones the repo, creates the virtualenv, and smoke-tests the imports.
# After it finishes, see deploy/README.md for running it + the proxy.
set -euo pipefail

INSTALL_DIR="${1:-/opt/buildupquote}"
REPO_URL="${2:-https://github.com/GWMarketing/buildupquote.git}"

echo "==> system packages (Python, git, WeasyPrint native libs)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git \
  libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core

echo "==> app directory: ${INSTALL_DIR}"
sudo mkdir -p "${INSTALL_DIR}"
sudo chown "$(whoami)" "${INSTALL_DIR}"

echo "==> cloning the repo"
if [ -d "${INSTALL_DIR}/.git" ]; then
  echo "already cloned -- pulling instead"
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

echo "==> Python virtualenv + dependencies"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo "==> smoke test (imports only -- no streamlit needed)"
./venv/bin/python -c "import fastapi_app; print('fastapi_app imports OK')"

echo
echo "Done. Run the app now with:"
echo "  cd ${INSTALL_DIR} && ./venv/bin/uvicorn fastapi_app:app --host 0.0.0.0 --port 8000"
echo "Or install the service + reverse proxy -- see deploy/README.md"
