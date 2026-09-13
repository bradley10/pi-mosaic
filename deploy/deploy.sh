#!/bin/sh -e
# Deploys the working tree to the Raspberry Pi and (re)starts the
# mbta-tracker systemd service. See README "Deploying to a Raspberry Pi".
#
# Usage: ./deploy/deploy.sh [pi-host]
#   PI_HOST env var, or the first argument, overrides the default host.

PI_HOST="${1:-${PI_HOST:-raspberrypi}}"
PI_USER=pi
PI_DIR=/home/pi/mbta-tracker
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
  sudo cp ${PI_DIR}/deploy/mbta-tracker.service /etc/systemd/system/mbta-tracker.service
  sudo systemctl daemon-reload
  sudo systemctl enable mbta-tracker
  sudo systemctl restart mbta-tracker
"

echo "==> Status"
ssh "${PI_USER}@${PI_HOST}" "sudo systemctl --no-pager status mbta-tracker"
