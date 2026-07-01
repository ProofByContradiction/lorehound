#!/usr/bin/env bash
# Lorehound deploy bootstrap — idempotent. Safe to re-run after every `git pull`.
#
# Sets up (or updates) the Python venv, installs runtime deps, and installs +
# (re)starts the systemd service so the bot runs always-on and restarts on crash
# or reboot. Designed for an Oracle Cloud Always Free ARM VM (Ubuntu), but works
# on any systemd Linux host.
#
# Usage (from anywhere):
#   ./deploy/setup.sh
#
# It does NOT touch your secrets: you must create .env yourself (see DEPLOY.md).
set -euo pipefail

# --- locate the repo (this script lives in <repo>/deploy) ------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_USER="$(id -un)"
VENV="$REPO_DIR/.venv"
PY="${PYTHON:-python3}"

echo "==> Lorehound deploy"
echo "    repo:   $REPO_DIR"
echo "    user:   $RUN_USER"
echo "    python: $($PY --version 2>&1)"

# --- 1. system python + venv module ----------------------------------------
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: $PY not found. On Ubuntu: sudo apt-get install -y python3 python3-venv" >&2
  exit 1
fi
if ! "$PY" -m venv --help >/dev/null 2>&1; then
  echo "ERROR: the venv module is missing. On Ubuntu: sudo apt-get install -y python3-venv" >&2
  exit 1
fi

# --- 2. venv + runtime deps ------------------------------------------------
if [[ ! -d "$VENV" ]]; then
  echo "==> Creating virtualenv at $VENV"
  "$PY" -m venv "$VENV"
fi
echo "==> Installing runtime dependencies"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"

# --- 3. .env sanity check --------------------------------------------------
if [[ ! -f "$REPO_DIR/.env" ]]; then
  echo "WARNING: $REPO_DIR/.env not found."
  echo "         Copy .env.example to .env and fill in DISCORD_TOKEN (+ Drive creds)."
  echo "         The service will crash-loop until .env has a valid DISCORD_TOKEN."
fi

# --- 4. systemd service ----------------------------------------------------
UNIT_SRC="$SCRIPT_DIR/lorehound.service"
UNIT_DST="/etc/systemd/system/lorehound.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "==> No systemd here — skipping service install."
  echo "    Run the bot manually with:  $VENV/bin/python bot.py"
  exit 0
fi

echo "==> Installing systemd unit to $UNIT_DST (needs sudo)"
tmp_unit="$(mktemp)"
sed -e "s#__USER__#$RUN_USER#g" -e "s#__WORKDIR__#$REPO_DIR#g" "$UNIT_SRC" > "$tmp_unit"
sudo cp "$tmp_unit" "$UNIT_DST"
rm -f "$tmp_unit"

sudo systemctl daemon-reload
sudo systemctl enable lorehound.service >/dev/null
sudo systemctl restart lorehound.service

echo
echo "==> Done. Lorehound is installed and (re)started."
echo "    status:  sudo systemctl status lorehound"
echo "    logs:    journalctl -u lorehound -f"
echo "    restart: sudo systemctl restart lorehound"
