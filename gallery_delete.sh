#!/bin/sh
# gallery_delete launcher (repo root).
# Actual script lives in _tools/gallery_delete/; this wrapper just forwards to it.
# Repo root is auto-detected by the py script, so this works from any cwd.
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python
exec "$PY" "$DIR/_tools/gallery_delete/gallery_delete.py" "$@"
