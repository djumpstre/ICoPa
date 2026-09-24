#!/usr/bin/env sh

set -eu

GUI_DIR="/workspace/icopa_gui"

if [ ! -d "$GUI_DIR" ]; then
  GUI_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../icopa_gui" && pwd)"
fi

if [ ! -d "$GUI_DIR" ]; then
  echo "[gui_start_npm] Could not locate icopa_gui directory." >&2
  exit 1
fi

cd "$GUI_DIR"

if [ ! -d node_modules ]; then
  echo "[gui_start_npm] node_modules not found. Running npm install..."
  npm install
fi

echo "[gui_start_npm] Starting GUI dev server at http://0.0.0.0:5173"
exec npm run dev -- --host 0.0.0.0 --port 5173
