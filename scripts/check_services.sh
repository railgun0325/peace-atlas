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

check "n8n" "http://localhost:5678"
check "FreshRSS" "http://localhost:8081"
check "RSSHub" "http://localhost:1200"
check "changedetection.io" "http://localhost:5000"
check "Qdrant" "http://localhost:6333/healthz"
check "analysis-worker" "http://localhost:8090/health"
