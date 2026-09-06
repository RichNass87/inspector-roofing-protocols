#!/usr/bin/env bash
# Scans the staged diff (or a file given as $1) for identifiers that must never be published.
# Fails closed: non-zero on any hit, and non-zero when there is nothing to scan.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="${TMPDIR:-/tmp}/colten-gate"
mkdir -p "$GATE"
STAGED="$GATE/staged.txt"

if [ "${1:-}" != "" ]; then
  cp "$1" "$STAGED"
else
  git -C "$ROOT" diff --cached > "$STAGED"
fi

if [ ! -s "$STAGED" ]; then
  echo "privacy-gate: FAIL (nothing staged to scan)"
  exit 1
fi

python3 - "$STAGED" <<'PY'
import re, sys
pats = {
    "home path":        r"/Users/(?!Shared/)[^/\s\"']+",
    "account token":    r"m4studio[0-9]*",
    "team identifier":  r"\bDEVELOPMENT_TEAM\b|\bTEAMID\b",
    "provisioning":     r"\.mobileprovision\b",
    "licence key":      r"\b[A-Z0-9]{4}(-[A-Z0-9]{4}){3,}\b",
    "cert fingerprint": r"(?i)(fingerprint|SHA-?1|Authority=)[^\n]{0,40}\b[0-9a-f]{40}\b",
}
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
SELF = "scripts/privacy-gate.sh"  # the gate's own pattern table is not published data
hits = []
current = None
for line in text.splitlines():
    if line.startswith("+++ "):
        current = line[4:].strip()
        continue
    if not line.startswith("+"):
        continue
    if current is not None and current.endswith(SELF):
        continue
    for key, pat in pats.items():
        for m in re.finditer(pat, line):
            hits.append((key, m.group(0)))
if hits:
    print(f"privacy-gate: FAIL ({len(hits)} hit(s) in added lines)")
    for key, val in hits[:40]:
        print(f"  {key}: {val}")
    sys.exit(1)
print(f"privacy-gate: PASS (scanned {len(text)} bytes of staged diff)")
PY
