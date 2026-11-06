#!/usr/bin/env bash
# CoreData launcher for Mac/Linux: run `./coredata.sh` (or double-click if your file manager allows).
# First run sets everything up automatically.
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "Setting up CoreData for the first time. This takes a minute..."
  python3 -m venv .venv || { echo "Python 3.11+ is needed: https://www.python.org/downloads/"; exit 1; }
fi

if ! cmp -s requirements.txt .venv/.requirements.installed; then
  echo "Installing Python packages..."
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
  cp requirements.txt .venv/.requirements.installed
fi

exec .venv/bin/python -m scripts.wizard
