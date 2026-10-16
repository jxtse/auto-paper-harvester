#!/usr/bin/env bash
# Build a single-file executable for the current OS into dist/.
# Usage: packaging/build.sh   (needs uv; Windows: run the same commands in Git Bash)
set -euo pipefail
cd "$(dirname "$0")/.."
uv venv -q .build-venv
PY=.build-venv/bin/python; [ -x "$PY" ] || PY=.build-venv/Scripts/python.exe
uv pip install -q -p "$PY" . "pyinstaller>=6.10"
"$PY" -m PyInstaller --noconfirm --clean --onefile --console \
  --name auto-paper-harvester \
  --collect-data auto_paper_download \
  --distpath dist --workpath build/pyinstaller --specpath build \
  packaging/app.py
ls -la dist/
