#!/usr/bin/env sh
set -eu

if [ "${1:-}" ]; then
  START_DATE="$1"
else
  START_DATE="$(date -u -d '7 days ago' +%F 2>/dev/null || date -v-7d +%F)"
fi

if [ "${2:-}" ]; then
  END_DATE="$2"
else
  END_DATE="$(date -u +%F)"
fi

curl -fsS -X POST "http://localhost:8090/analyze/weekly" \
  -H "Content-Type: application/json" \
  -d "{\"start_date\":\"$START_DATE\",\"end_date\":\"$END_DATE\"}"
