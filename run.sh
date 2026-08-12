#!/usr/bin/env bash
# Prodlysis one-click launcher
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install -q -r requirements.txt 2>/dev/null || \
  .venv/bin/pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -q -r requirements.txt

echo "Starting Prodlysis at http://127.0.0.1:5000"
exec .venv/bin/python app.py
