#!/usr/bin/env bash
# Grainwell setup - project-level dependencies only.
#
# Grainwell has no runtime dependencies (standard library only), so this script
# has nothing it MUST install. It upgrades the packaging tools, honours
# requirements.txt, and - when a package index is reachable - puts the optional
# `grainwell` console script on PATH. It never installs system packages, never
# asks a question and always exits without starting anything long-running.
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "error: $PY not found; Grainwell needs CPython 3.9+ (see INSTALL.md)." >&2
    exit 1
fi

# 1. Tooling first: old pip silently fails editable installs of modern layouts.
if "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 \
        || echo "note: no package index reachable for the pip upgrade; continuing (Grainwell needs no packages)." >&2

    # 2. Declared dependencies from the manifest (empty by design for Grainwell).
    "$PY" -m pip install -r requirements.txt >/dev/null \
        || { echo "error: could not install requirements.txt." >&2; exit 1; }

    # 3. Optional convenience: the `grainwell` console script on PATH.
    "$PY" -m pip install -e . >/dev/null 2>&1 \
        || echo "note: console script not installed; run the tool as: python3 grainwell.py" >&2
else
    echo "note: pip is unavailable; run the tool as: python3 grainwell.py" >&2
fi

# 4. There is no database, migration or seed step: every number is computed from
#    the flags on the command line, and nothing is written to disk.

# 5. Cheap self-check so a broken checkout fails here, not at first use.
"$PY" -c "import grainwell" || { echo "error: grainwell.py did not import." >&2; exit 1; }

echo "setup complete - run: python3 grainwell.py --help"
