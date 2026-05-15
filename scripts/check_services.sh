#!/usr/bin/env sh
set -eu

check() {
  name="$1"
  url="$2"
  if curl -fsS --max-time 10 "$url" >/dev/null; then
    echo "OK   $name"
  else
    echo "FAIL $name"
  fi
}

check "n8n" "http://localhost:5782"
check "FreshRSS" "http://localhost:8181"
check "RSSHub" "http://localhost:1210"
check "changedetection.io" "http://localhost:15100"
check "Qdrant" "http://localhost:6433/healthz"
check "analysis-worker" "http://localhost:8190/health"
