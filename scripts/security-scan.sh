#!/usr/bin/env bash
# Security gate. See SECURITY_TOOLING.md.
#   scripts/security-scan.sh staged   secrets in the staged diff (pre-commit hook)
#   scripts/security-scan.sh skills [dir...]  scan skill folders before trusting them
#   scripts/security-scan.sh full     secrets in git history, shipped skills, dependencies (CI)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
mode="${1:-full}"
SKILLGUARD="t2i-skillguard==0.1.0"

need() { command -v "$1" >/dev/null || { echo "security-scan: $1 not installed (see SECURITY_TOOLING.md)" >&2; exit 2; }; }

secrets_staged() {
  need gitleaks
  gitleaks git --pre-commit --staged --redact --no-banner .
}

secrets_history() {
  need gitleaks
  gitleaks git --redact --no-banner .
}

skills() {
  local sg=(t2i-skillguard)
  command -v t2i-skillguard >/dev/null || { need uvx; sg=(uvx --from "$SKILLGUARD" t2i-skillguard); }
  # Shipped skill packs only. tests/fixtures/skills holds deliberately malicious samples.
  local dirs=("$@"); [ ${#dirs[@]} -gt 0 ] || dirs=(workbench/backend/app/packs/bundles/*/skills)
  local out; out="$("${sg[@]}" scan -f json -s HIGH "${dirs[@]}" 2>/dev/null || true)"
  python3 -c '
import json,sys
raw=sys.stdin.read(); d=json.loads(raw[raw.index("{"):])
bad=[(s["name"],v["id"],v["title"]) for s in d.get("skills",[]) for v in s.get("vulnerabilities",[]) if v["severity"] in ("HIGH","CRITICAL")]
for b in bad: print("skill finding:",*b)
print("skills:", d.get("total_skills",0), "with findings,", len(bad), "HIGH/CRITICAL")
sys.exit(1 if bad else 0)' <<<"$out"
}

deps() {
  need osv-scanner
  local report; report="$(mktemp)"
  osv-scanner scan source -r --format json . >"$report" 2>/dev/null || true
  python3 - "$report" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); count={}; crit=[]
for r in d.get("results",[]):
    for p in r["packages"]:
        for g in p.get("groups",[]):
            try: s=float(g.get("max_severity") or "")
            except ValueError: s=-1
            b="CRITICAL" if s>=9 else "HIGH" if s>=7 else "MEDIUM" if s>=4 else "LOW" if s>=0 else "UNKNOWN"
            count[b]=count.get(b,0)+1
            if b=="CRITICAL": crit.append(f'{p["package"]["name"]} {p["package"]["version"]} {g["ids"][0]} ({r["source"]["path"]})')
print("dependencies:", ", ".join(f"{k} {v}" for k,v in sorted(count.items())) or "clean")
for c in crit: print("CRITICAL:", c)
sys.exit(1 if crit else 0)
PY
  rm -f "$report"
}

case "$mode" in
  staged) secrets_staged ;;
  skills) shift; skills "$@" ;;
  full) rc=0; secrets_history || rc=1; skills || rc=1; deps || rc=1; exit $rc ;;
  *) echo "usage: $0 staged|skills [dir...]|full" >&2; exit 2 ;;
esac
