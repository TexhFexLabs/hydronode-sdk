#!/usr/bin/env bash
# hydronode.sh — send signed readings to HydroNode with nothing but bash, curl and openssl.
#
# Send one round (all values in one request):
#   SENSOR_ID=... SECRET=... ./hydronode.sh send TEMPERATURE=21.5 HUMIDITY=48
#
# Show what would be signed, without sending (to debug a "401 Invalid signature"):
#   SENSOR_ID=... SECRET=... ./hydronode.sh dry-run TEMPERATURE=21.5
#
# Sign any text (for your own client):
#   SECRET=... ./hydronode.sh sign '<canonical payload><timestamp>'
#
# Or source it and call the functions: hn_value, hn_round_payload, hn_sign, hn_send.
#
# Values: give them with at most two decimals (21.5, 21.25, -3, 1013.25). HydroNode reads them
# back to exactly the same text, so the signature always matches. More decimals are refused:
# round them yourself first (printf "%.2f" is fine for that).
#
# Optional: HYDRONODE_URL (default https://hydronode.tech), HYDRONODE_FIRMWARE (X-Firmware),
#           HYDRONODE_NETWORK (net= for the fleet view: wifi, eth, lte-m ...).

HYDRONODE_URL="${HYDRONODE_URL:-https://hydronode.tech}"

# 21.5 -> 21.50, -3 -> -3.00. Refuses anything that is not a number with up to two decimals.
hn_value() {
  local v="$1"
  if [[ ! "$v" =~ ^-?[0-9]+(\.[0-9]{1,2})?$ ]]; then
    echo "hydronode: '$v' is not a number with at most two decimals" >&2
    return 1
  fi
  local sign="" whole frac
  [[ "$v" == -* ]] && { sign="-"; v="${v#-}"; }
  whole="${v%%.*}"
  frac=""
  [[ "$v" == *.* ]] && frac="${v#*.}"
  frac="${frac}00"
  whole=$((10#$whole))
  printf '%s%s.%s' "$sign" "$whole" "${frac:0:2}"
}

# A-Z, 0-9 and _, starting with a letter, at most 64 characters.
hn_valid_type() {
  [[ "$1" =~ ^[A-Z][A-Z0-9_]{0,63}$ ]]
}

# Canonical text of one round: hn_round_payload <sensorId> <timestamp> TYPE=VALUE ...
hn_round_payload() {
  local sensor="$1" ts="$2" items="" pair type value seen=" "
  shift 2
  [[ $# -ge 1 && $# -le 64 ]] || { echo "hydronode: send 1 to 64 values" >&2; return 1; }
  for pair in "$@"; do
    type="${pair%%=*}"
    hn_valid_type "$type" || { echo "hydronode: invalid type '$type' (use A-Z, 0-9 and _)" >&2; return 1; }
    [[ "$seen" == *" $type "* ]] && { echo "hydronode: type $type appears twice" >&2; return 1; }
    seen="$seen$type "
    value="$(hn_value "${pair#*=}")" || return 1
    [[ -n "$items" ]] && items="$items,"
    items="$items{\"type\":\"$type\",\"value\":$value}"
  done
  printf '{"sensorId":"%s","timestamp":%s,"values":[%s]}' "$sensor" "$ts" "$items"
}

# Base64(HMAC-SHA256(secret, message)): hn_sign <secret> <message>
hn_sign() {
  printf '%s' "$2" | openssl dgst -sha256 -hmac "$1" -binary | openssl base64 -A
}

# POST a signed body: hn_send <path> <body> <timestamp> <signature>
hn_send() {
  local headers=(-H "Content-Type: application/json" -H "X-Sensor-Id: $SENSOR_ID"
    -H "X-Timestamp: $3" -H "X-Signature: $4" -H "X-Firmware: ${HYDRONODE_FIRMWARE:-hydronode-sh/1.0.0}")
  [[ -n "$HYDRONODE_NETWORK" ]] && headers+=(-H "X-Device-Status: net=$HYDRONODE_NETWORK")
  curl -sS --max-time 15 -w '\nHTTP %{http_code}\n' "${headers[@]}" --data-binary "$2" "$HYDRONODE_URL$1"
}

hn_main() {
  local command="$1"
  shift || true
  case "$command" in
    send|dry-run)
      [[ -n "$SENSOR_ID" && -n "$SECRET" ]] || { echo "set SENSOR_ID and SECRET" >&2; return 2; }
      local ts payload signature
      ts="$(date +%s)"
      payload="$(hn_round_payload "$SENSOR_ID" "$ts" "$@")" || return 1
      signature="$(hn_sign "$SECRET" "$payload$ts")"
      if [[ "$command" == dry-run ]]; then
        printf 'POST %s/api/webhook/sensor-values\nX-Timestamp: %s\nX-Signature: %s\n\n%s\n\nsigned text: %s%s\n' \
          "$HYDRONODE_URL" "$ts" "$signature" "$payload" "$payload" "$ts"
      else
        hn_send /api/webhook/sensor-values "$payload" "$ts" "$signature"
      fi
      ;;
    sign)
      [[ -n "$SECRET" ]] || { echo "set SECRET" >&2; return 2; }
      hn_sign "$SECRET" "$1"
      echo
      ;;
    *)
      sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      return 2
      ;;
  esac
}

# Run only when executed, not when sourced.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  hn_main "$@"
fi
