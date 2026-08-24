#!/usr/bin/env bash
# Wake the deployed agent up, then measure it.
#
# App Service puts a container to sleep after ~20 minutes without traffic when
# `Always On` is off, so the FIRST call pays the whole cold start: process restart
# plus the lifespan, which probes the embeddings model and indexes the FAQ. That is
# ~45 s, and a plain `curl` gives up long before.
#
# `--retry-all-errors` retries on HTTP error codes too, not just network failures,
# which is what absorbs the wake-up without watching it by hand. The warm calls that
# follow measure the latency that actually matters — the one a customer would see.
#
# Usage:
#   scripts/wake.sh                       # uses $AGENT_API_URL, else localhost
#   scripts/wake.sh https://my-app.net    # explicit target
set -uo pipefail

BASE="${1:-${AGENT_API_URL:-http://localhost:8000}}"
BASE="${BASE%/}"

printf '→ %s\n' "$BASE"

printf '\n  Réveil — /ready (jusqu'"'"'à ~90 s à froid)\n'
curl -s -o /dev/null \
  --retry 6 --retry-delay 10 --retry-all-errors --max-time 90 \
  -w '    ready   HTTP %{http_code} — %{time_total}s\n' \
  "$BASE/ready"

printf '\n  Trois mesures à chaud — /health\n'
for _ in 1 2 3; do
  curl -s -o /dev/null --max-time 30 \
    -w '    health  HTTP %{http_code} — %{time_total}s\n' \
    "$BASE/health"
done

printf '\n  200 partout = agent réveillé et prêt.\n'
printf '  Un 503 après le réveil = le conteneur ne démarre pas : voir le Flux de journal.\n'
