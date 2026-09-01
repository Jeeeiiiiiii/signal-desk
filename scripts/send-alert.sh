#!/usr/bin/env bash
# Send a signed alert to the ingest Lambda, the way a monitoring system would.
#
#   ./send-alert.sh <function-url> [severity] [alertname] [host]
set -euo pipefail

URL="${1:?usage: send-alert.sh <function-url> [severity] [alertname] [host]}"
SEVERITY="${2:-critical}"
ALERTNAME="${3:-HighErrorRate}"
HOST="${4:-web-01}"
SECRET="${WEBHOOK_SECRET:-local-dev-secret-change-me}"

BODY=$(cat <<JSON
{"alertname":"${ALERTNAME}","severity":"${SEVERITY}","instance":"${HOST}","status":"firing","description":"Error rate above threshold for 5m"}
JSON
)

SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -sS -X POST "$URL" \
  -H "Content-Type: application/json" \
  -H "X-Signal-Source: prometheus" \
  -H "X-Signal-Signature: sha256=${SIG}" \
  -d "$BODY"
echo
