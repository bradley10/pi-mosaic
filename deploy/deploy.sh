#!/bin/sh -e
# Deploys the working tree to the Raspberry Pi and (re)starts the
# pi-mosaic systemd service. See README "Deploying to a Raspberry Pi".
#
# Usage: ./deploy/deploy.sh [pi-host]
#   PI_HOST env var, or the first argument, overrides the default host.

PI_HOST="${1:-${PI_HOST:-raspberrypi}}"
PI_USER=pi
PI_DIR=/home/pi/pi-mosaic
REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

echo "==> Syncing code to ${PI_USER}@${PI_HOST}:${PI_DIR}"
rsync -az --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude '.ruff_cache' \
  --exclude '.pytest_cache' \
  --exclude '.vscode' \
  --exclude '.DS_Store' \
  --exclude '*.egg-info' \
  "${REPO_ROOT}/" "${PI_USER}@${PI_HOST}:${PI_DIR}/"

echo "==> Installing dependencies (Pi stays on its system Python 3.9)"
ssh "${PI_USER}@${PI_HOST}" "
  set -e
  cd ${PI_DIR}
  if [ ! -x venv/bin/python ]; then
    python3 -m venv --system-site-packages venv
  fi
  ./venv/bin/pip install --quiet -r requirements-pi.txt
"

echo "==> Installing systemd service"
ssh "${PI_USER}@${PI_HOST}" "
  set -e
  if systemctl is-active --quiet mbta-tracker 2>/dev/null; then
    sudo systemctl stop mbta-tracker || true
  fi
  if systemctl is-enabled --quiet mbta-tracker 2>/dev/null; then
    sudo systemctl disable mbta-tracker || true
  fi
  if [ -f /etc/systemd/system/mbta-tracker.service ]; then
    sudo rm -f /etc/systemd/system/mbta-tracker.service
  fi

  sudo cp ${PI_DIR}/deploy/pi-mosaic.service /etc/systemd/system/pi-mosaic.service
  sudo systemctl daemon-reload
  sudo systemctl enable pi-mosaic
  sudo systemctl restart pi-mosaic
"

echo "==> Status"
ssh "${PI_USER}@${PI_HOST}" "sudo systemctl --no-pager status pi-mosaic"
