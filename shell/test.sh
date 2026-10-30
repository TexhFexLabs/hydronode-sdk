#!/usr/bin/env bash
# Tests of hydronode.sh against the shared signature vectors. Needs python3 only to read the JSON.
#   bash shell/test.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/hydronode.sh"
VECTORS="$HERE/../vectors/signature-vectors.json"
failed=0
checks=0

expect() {  # expect <label> <got> <want>
  checks=$((checks + 1))
  if [[ "$2" != "$3" ]]; then
    failed=$((failed + 1))
    printf 'FAIL %s\n  got  %s\n  want %s\n' "$1" "$2" "$3"
  fi
}

# Value texts: what the shell accepts is passed through with two decimals.
expect "21.5" "$(hn_value 21.5)" "21.50"
expect "-3" "$(hn_value -3)" "-3.00"
expect "0.05" "$(hn_value 0.05)" "0.05"
expect "007.1" "$(hn_value 007.1)" "7.10"
hn_value 21.125 2>/dev/null && { failed=$((failed + 1)); echo "FAIL 21.125 accepted"; }
hn_value abc 2>/dev/null && { failed=$((failed + 1)); echo "FAIL abc accepted"; }
hn_round_payload s 1 temperature=1 2>/dev/null && { failed=$((failed + 1)); echo "FAIL lower case type"; }
hn_round_payload s 1 T=1 T=2 2>/dev/null && { failed=$((failed + 1)); echo "FAIL duplicate type"; }

# Rounds of the shared table: the value texts come from the expected canonical text.
secret="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['secret'])" "$VECTORS")"
sensor="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['sensorId'])" "$VECTORS")"
while IFS=$'\t' read -r ts pairs canonical signature; do
  # shellcheck disable=SC2086
  got="$(hn_round_payload "$sensor" "$ts" $pairs)"
  expect "round $ts" "$got" "$canonical"
  expect "round signature $ts" "$(hn_sign "$secret" "$got$ts")" "$signature"
done < <(python3 - "$VECTORS" <<'PY'
import json, re, sys
doc = json.load(open(sys.argv[1]))
rounds = doc["round"] + [r for c in doc["rounds"] for r in c["rounds"]]
for r in rounds:
    texts = re.findall(r'"type":"([A-Z0-9_]+)","value":(-?[0-9.]+)', r["canonical"])
    print("%d\t%s\t%s\t%s" % (r["timestamp"], " ".join("%s=%s" % t for t in texts), r["canonical"], r["signature"]))
PY
)

while IFS=$'\t' read -r message signature; do
  expect "raw signature" "$(hn_sign "$secret" "$message")" "$signature"
done < <(python3 - "$VECTORS" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
for c in doc["raw"]:
    print("%s%d\t%s" % (c["payload"], c["timestamp"], c["signature"]))
for c in doc["rounds"]:
    print("%s%d\t%s" % (c["envelope"], c["sent"], c["signature"]))
PY
)

echo "shell: $checks checks, $failed failed"
exit $((failed > 0))
