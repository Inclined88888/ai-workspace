#!/usr/bin/env bash
# Build the Workbench image and enforce what every PR must keep true (item 111):
#   * image size <= MAX_IMAGE_MB (R6)
#   * idle RAM <= MAX_IDLE_MIB: median of 6 samples after IDLE_S seconds idle (a single sample can catch the
#     healthcheck's own python process and read 20+ MiB high)
#   * the access log works: requests produce access lines, and no "TypeError" or "Logging error" appears
#     (the PR #1 regression: redaction broke uvicorn's access formatter and every request line was lost)
#   * planted secrets never reach the log (R7)
# Usage: scripts/ci/check-image.sh            (from the repo root; DOCKERFILE and BUILD_FLAGS can be overridden)
set -euo pipefail
TAG=${TAG:-workbench:ci}
MAX_IMAGE_MB=${MAX_IMAGE_MB:-400}
MAX_IDLE_MIB=${MAX_IDLE_MIB:-150}
IDLE_S=${IDLE_S:-60}
PORT=${PORT:-18000}
NAME=workbench-ci-$$
SECRET_KEY="sk-proj-CIPLANTEDabcdefghijklmnopqr"
SECRET_MAIL="ci-planted@example.com"
fail=0
report() { echo "$1"; [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] && echo "$1" >> "$GITHUB_STEP_SUMMARY"; return 0; }
bad() { report "FAIL: $1"; fail=1; }

# shellcheck disable=SC2086
docker build ${BUILD_FLAGS:-} -t "$TAG" -f "${DOCKERFILE:-workbench/Dockerfile}" workbench
# Size = sum of the image's layers, unpacked. Not `docker image inspect .Size`: with Docker's containerd image store
# that adds the compressed blobs on top (measured: 282 MB of layers + 85 MB of blobs read as 367.8 MB), while the classic
# store reports layers only, so the same image would pass on one engine and fail on another.
mb=$(docker history --no-trunc --format '{{.Size}}' "$TAG" | awk '{v=$1; u=v; gsub(/[0-9.]/,"",u); gsub(/[A-Za-z]/,"",v);
  f=(u=="GB")?1e9:(u=="MB")?1e6:(u=="kB")?1e3:1; s+=v*f} END {printf "%d", s/1e6}')
report "Image size: ${mb} MB of layers (limit ${MAX_IMAGE_MB} MB; inspect reports $(( $(docker image inspect -f '{{.Size}}' "$TAG") / 1000000 )) MB on this engine)"
(( mb <= MAX_IMAGE_MB )) || bad "image is ${mb} MB, over ${MAX_IMAGE_MB} MB"

secret=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
docker run -d --memory=1g --memory-swap=1g --cpus=1 --pids-limit=256 --name "$NAME" -p "127.0.0.1:${PORT}:8000" -e APP_SECRET="$secret" -e TOOLS_API_KEY="$secret" "$TAG" >/dev/null
# Refuse an apparent pass when the daemon did not enforce the requested cap.
actual_limit=$(docker exec "$NAME" sh -c 'cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes')
[[ "$actual_limit" == 1073741824 ]] || { bad "memory cap not enforced ($actual_limit)"; docker rm -f "$NAME" >/dev/null; exit 1; }
cookie_jar=$(mktemp)
trap 'rm -f "$cookie_jar"; docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || { docker logs "$NAME"; bad "container never became healthy"; exit 1; }

# One request per access-log shape: 200, 404, 401, 403, a POST, and one carrying secrets in the query string
base="http://127.0.0.1:${PORT}"
curl -s -o /dev/null "$base/api/status?api_key=${SECRET_KEY}&email=${SECRET_MAIL}"
curl -s -o /dev/null "$base/api/no-such-route"
curl -s -o /dev/null "$base/api/memories"
curl -s -o /dev/null -X POST "$base/api/tasks" -H 'Content-Type: application/json' -d '{"title":"x"}'
curl -s -o /dev/null -X POST "$base/api/login" -H 'Content-Type: application/json' -H 'X-Workbench: 1' -d "{\"password\":\"${SECRET_KEY}\"}"
expected_lines=6  # the five above plus at least one healthz

# Exercise authenticated writes, readback and persistence under the 1 GiB hard cap.
curl -fsS -c "$cookie_jar" -X POST "$base/api/setup" -H 'X-Workbench: 1' -H 'Content-Type: application/json' \
  -d "{\"password\":\"$secret\"}" >/dev/null
curl -fsS -b "$cookie_jar" -X POST "$base/api/tasks" -H 'X-Workbench: 1' -H 'Content-Type: application/json' \
  -d '{"title":"one-gib-persistence-marker"}' >/dev/null
curl -fsS -b "$cookie_jar" "$base/api/tasks" | grep -q one-gib-persistence-marker || bad "task readback failed"
docker restart "$NAME" >/dev/null
for _ in $(seq 1 60); do curl -fsS "$base/healthz" >/dev/null 2>&1 && break; sleep 1; done
curl -fsS -b "$cookie_jar" "$base/api/tasks" | grep -q one-gib-persistence-marker || bad "task did not survive restart"
[[ "$(docker inspect -f '{{.State.OOMKilled}}' "$NAME")" == false ]] || bad "container was OOM killed"
report "1 GiB container cap: authenticated task write/read and restart persistence passed (one CPU, no swap allowance)"
sleep "$IDLE_S"
samples=()
for _ in $(seq 1 6); do
  samples+=("$(docker stats --no-stream --format '{{.MemUsage}}' "$NAME" | awk '{v=$1; u=v; gsub(/[0-9.]/,"",u); gsub(/[A-Za-z]/,"",v);
    f=(u=="GiB")?1024:(u=="KiB"||u=="kB")?1/1024:1; printf "%.2f", v*f}')")
  sleep 5
done
median=$(printf '%s\n' "${samples[@]}" | sort -n | awk '{a[NR]=$1} END {print (NR%2)?a[(NR+1)/2]:(a[NR/2]+a[NR/2+1])/2}')
report "Idle RAM after ${IDLE_S} s: median ${median} MiB of samples ${samples[*]} (limit ${MAX_IDLE_MIB} MiB)"
awk -v m="$median" -v l="$MAX_IDLE_MIB" 'BEGIN {exit !(m <= l)}' || bad "idle RAM ${median} MiB is over ${MAX_IDLE_MIB} MiB"

logs=$(docker logs "$NAME" 2>&1)
access=$(grep -c 'HTTP/1.1"' <<<"$logs" || true)
report "Access log lines: ${access} (expected at least ${expected_lines})"
(( access >= expected_lines )) || bad "only ${access} access log lines; the access log formatter is broken or requests are not logged"
if grep -nE 'TypeError|Logging error' <<<"$logs"; then bad "TypeError or Logging error in the container log"; fi
if grep -nF -e "$SECRET_KEY" -e "$SECRET_MAIL" <<<"$logs"; then bad "a planted secret reached the log"; fi
(( fail == 0 )) && report "Image checks passed" || { echo "--- container log ---"; echo "$logs"; }
exit "$fail"
