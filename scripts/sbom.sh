#!/usr/bin/env bash
#
# Produces a software bill of materials: every dependency, at the exact version shipped, in
# CycloneDX format.
#
# Sift is a self-hosted app that faces the internet and bundles third-party tools, so "what is
# actually in this build" has to be answerable without guessing. Two things need it:
#
#   - Vulnerability response. When a CVE lands against some transitive package, the only question
#     that matters is whether this release contains it and at what version. Grepping a lockfile at
#     that moment is how you get the answer wrong.
#   - Licence compliance. Sift is AGPL-3.0 and ships GPL tools. A transitive dependency under an
#     incompatible licence is a real risk, and the SBOM is what makes it visible.
#
# The Python side comes from the lockfile, so it is exact rather than "whatever pip resolved today".
# The tools the installer bundles are not Python packages; scripts/vendor_manifest.json pins each one.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

OUT="${1:-sbom.cdx.json}"
# A virtual environment puts its interpreter in `bin/` on POSIX and `Scripts/` with an `.exe` on
# Windows. Named as one or the other this could not start at all on the other platform, and a shell
# reports that as exit 127 ("command not found"), which reads like the script being missing
# rather than the interpreter it names.
if [ -z "${PY:-}" ]; then
  if [ -x .venv/bin/python ]; then
    PY=.venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then
    PY=.venv/Scripts/python.exe
  else
    echo "no interpreter in .venv. Run: uv sync --extra dev" >&2
    exit 1
  fi
fi

if [ ! -f uv.lock ]; then
  echo "no uv.lock. Run: uv lock" >&2
  exit 1
fi

echo "sbom: generating from the lockfile"
"$PY" -m cyclonedx_py environment "$PY" \
  --output-format json \
  --output-file "$OUT" \
  --output-reproducible

echo "sbom: $OUT"
"$PY" - "$OUT" <<'EOF'
import json, sys
doc = json.load(open(sys.argv[1]))
components = doc.get("components", [])
print(f"  {len(components)} components")
print(f"  spec: CycloneDX {doc.get('specVersion')}")
EOF
