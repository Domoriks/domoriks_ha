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
#   RAW_FRAME    RTU frame hex for raw test (default: read first 4 coils from slave 64)
#
# Example:
#   HA_TOKEN="<token>" MODULE_ID=64 bash tests/http_api_curl_tests.sh

# Auto-load ../.env when present.
# Supports PowerShell-style lines like:
#   $env:HA_URL = "http://..."
#   $env:HA_TOKEN = "..."
ENV_FILE="${ENV_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line; do
    if [[ "$line" =~ ^\$env:([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*"(.*)"[[:space:]]*$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      export "$key=$val"
    fi
  done < "$ENV_FILE"
fi

HA_URL="${HA_URL:-http://homeassistant.local:8123}"
HA_TOKEN="${HA_TOKEN:-}"
MODULE_ID="${MODULE_ID:-64}"
ENTRY_ID="${ENTRY_ID:-}"
RAW_FRAME="${RAW_FRAME:-40010000000432d8}"

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

if [[ -t 1 ]]; then
  C_RESET='\033[0m'
  C_BOLD='\033[1m'
  C_GREEN='\033[32m'
  C_RED='\033[31m'
  C_YELLOW='\033[33m'
  C_BLUE='\033[34m'
  C_CYAN='\033[36m'
else
  C_RESET=''
  C_BOLD=''
  C_GREEN=''
  C_RED=''
  C_YELLOW=''
  C_BLUE=''
  C_CYAN=''
fi

print_json() {
  local json="$1"
  if command -v jq >/dev/null 2>&1; then
    echo "$json" | jq . 2>/dev/null || echo "$json"
  else
    echo "$json"
  fi
}

print_exchange() {
  local name="$1"
  local endpoint="$2"
  local payload="$3"
  local status="$4"
  local body="$5"

  echo -e "${C_CYAN}${C_BOLD}--- ${name} ---${C_RESET}"
  echo -e "${C_BLUE}POST ${endpoint}${C_RESET}"
  echo -e "${C_BLUE}Request JSON:${C_RESET}"
  print_json "$payload"
  echo -e "${C_BLUE}Response status:${C_RESET} ${status}"
  echo -e "${C_BLUE}Response JSON:${C_RESET}"
  print_json "$body"
}

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

  print_exchange "$name" "$endpoint" "$payload" "$status" "$body"

  if [[ "$status" == "$expected_status" ]]; then
    echo -e "${C_GREEN}PASS:${C_RESET} ${name} (HTTP ${status})"
    PASS=$((PASS + 1))
  else
    echo -e "${C_RED}FAIL:${C_RESET} ${name} (expected HTTP ${expected_status}, got ${status})"
    FAIL=$((FAIL + 1))
  fi
  echo
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

  print_exchange "$name" "$endpoint" "$payload" "$status" "$body"

  for expected in "$@"; do
    if [[ "$status" == "$expected" ]]; then
      ok=1
      break
    fi
  done

  if [[ "$ok" -eq 1 ]]; then
    echo -e "${C_GREEN}PASS:${C_RESET} ${name} (HTTP ${status})"
    PASS=$((PASS + 1))
  else
    echo -e "${C_RED}FAIL:${C_RESET} ${name} (expected one of: $*, got ${status})"
    FAIL=$((FAIL + 1))
  fi
  echo
}

echo -e "${C_BOLD}Running Domoriks HTTP API smoke tests against ${HA_URL}${C_RESET}"

detect_single_payload="{\"slave\":${MODULE_ID}${ENTRY_JSON}}"
api_post "detect single slave" "/api/domoriks/detect" "$detect_single_payload" "200"

detect_range_payload="{\"start_slave\":${MODULE_ID},\"end_slave\":$((MODULE_ID + 2))${ENTRY_JSON}}"
api_post "detect slave range" "/api/domoriks/detect" "$detect_range_payload" "200"

# Invalid CRC test (intentionally bad last byte).
invalid_crc_payload="{\"frame\":\"40050000ff008ec5\"${ENTRY_JSON}}"
api_post "raw frame with invalid CRC is rejected" "/api/domoriks/raw" "$invalid_crc_payload" "400"

valid_raw_payload="{\"frame\":\"${RAW_FRAME}\",\"timeout\":2.0${ENTRY_JSON}}"
# Depending on bus/target state this can succeed, timeout, or be bad request.
api_post_one_of "raw frame send (${RAW_FRAME})" "/api/domoriks/raw" "$valid_raw_payload" "200" "504" "400"

echo

echo -e "${C_BOLD}Summary:${C_RESET} PASS=${PASS} FAIL=${FAIL}"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
