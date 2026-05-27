#!/usr/bin/env bash
set -euo pipefail

# Smoke tests for Domoriks HTTP API endpoints.
# Requires Home Assistant running with Domoriks integration loaded.
#
# Environment variables:
#   HA_URL       Base URL of Home Assistant (default: http://homeassistant.local:8123)
#   HA_TOKEN     Home Assistant long-lived access token (required)
#   MODULE_ID    Slave/module ID for detection tests (default: 64)
#   ENTRY_ID     Optional Domoriks config entry ID when multiple entries exist
#   RAW_FRAME    Optional valid RTU frame hex for raw test (including CRC)
#
# Example:
#   HA_TOKEN="<token>" MODULE_ID=64 bash tests/http_api_curl_tests.sh

HA_URL="${HA_URL:-http://homeassistant.local:8123}"
HA_TOKEN="${HA_TOKEN:-}"
MODULE_ID="${MODULE_ID:-64}"
ENTRY_ID="${ENTRY_ID:-}"
RAW_FRAME="${RAW_FRAME:-}"

if [[ -z "$HA_TOKEN" ]]; then
  echo "ERROR: HA_TOKEN is required"
  exit 1
fi

if [[ -n "$ENTRY_ID" ]]; then
  ENTRY_JSON=",\"entry_id\":\"${ENTRY_ID}\""
else
  ENTRY_JSON=""
fi

PASS=0
FAIL=0

api_post() {
  local name="$1"
  local endpoint="$2"
  local payload="$3"
  local expected_status="$4"

  local response body status
  response=$(curl -sS -X POST "${HA_URL}${endpoint}" \
    -H "Authorization: Bearer ${HA_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$payload" \
    -w '\n%{http_code}')

  body=$(echo "$response" | sed '$d')
  status=$(echo "$response" | tail -n 1)

  if [[ "$status" == "$expected_status" ]]; then
    echo "PASS: ${name} (HTTP ${status})"
    PASS=$((PASS + 1))
  else
    echo "FAIL: ${name} (expected HTTP ${expected_status}, got ${status})"
    echo "Body: ${body}"
    FAIL=$((FAIL + 1))
  fi
}

api_post_one_of() {
  local name="$1"
  local endpoint="$2"
  local payload="$3"
  shift 3

  local response body status ok
  response=$(curl -sS -X POST "${HA_URL}${endpoint}" \
    -H "Authorization: Bearer ${HA_TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$payload" \
    -w '\n%{http_code}')

  body=$(echo "$response" | sed '$d')
  status=$(echo "$response" | tail -n 1)
  ok=0

  for expected in "$@"; do
    if [[ "$status" == "$expected" ]]; then
      ok=1
      break
    fi
  done

  if [[ "$ok" -eq 1 ]]; then
    echo "PASS: ${name} (HTTP ${status})"
    PASS=$((PASS + 1))
  else
    echo "FAIL: ${name} (expected one of: $*, got ${status})"
    echo "Body: ${body}"
    FAIL=$((FAIL + 1))
  fi
}

echo "Running Domoriks HTTP API smoke tests against ${HA_URL}"

detect_single_payload="{\"slave\":${MODULE_ID}${ENTRY_JSON}}"
api_post "detect single slave" "/api/domoriks/detect" "$detect_single_payload" "200"

detect_range_payload="{\"start_slave\":${MODULE_ID},\"end_slave\":$((MODULE_ID + 2))${ENTRY_JSON}}"
api_post "detect slave range" "/api/domoriks/detect" "$detect_range_payload" "200"

# Invalid CRC test (intentionally bad last byte).
invalid_crc_payload="{\"frame\":\"40050000ff008ec5\"${ENTRY_JSON}}"
api_post "raw frame with invalid CRC is rejected" "/api/domoriks/raw" "$invalid_crc_payload" "400"

if [[ -n "$RAW_FRAME" ]]; then
  valid_raw_payload="{\"frame\":\"${RAW_FRAME}\",\"timeout\":2.0${ENTRY_JSON}}"
  # Depending on bus/target state this can succeed, timeout, or be bad request.
  api_post_one_of "raw frame send with user-supplied frame" "/api/domoriks/raw" "$valid_raw_payload" "200" "504" "400"
else
  echo "SKIP: raw frame send test (set RAW_FRAME to run)"
fi

echo

echo "Summary: PASS=${PASS} FAIL=${FAIL}"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
