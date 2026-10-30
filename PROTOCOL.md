# The HydroNode device protocol

Everything a device needs to send readings to HydroNode and take commands back, without any
library. If your platform can do HTTPS and HMAC-SHA256, it can talk to HydroNode. The SDKs in
this repository, the [Arduino library](https://github.com/TexhFexLabs/HydroNode-Library) and the
[ESPHome component](https://github.com/TexhFexLabs/hydronode-esphome) all implement exactly this
page, and all of them are tested against the same table: [`vectors/signature-vectors.json`](vectors/signature-vectors.json).

- [1. Credentials](#1-credentials)
- [2. Signing a request](#2-signing-a-request)
- [3. Endpoints](#3-endpoints)
- [4. Answers](#4-answers)
- [5. Commands](#5-commands)
- [6. Optional device headers](#6-optional-device-headers)
- [7. Limits](#7-limits)
- [8. Checklist for a new implementation](#8-checklist-for-a-new-implementation)

## 1. Credentials

Create a sensor in the [HydroNode web app](https://hydronode.tech/) (or the iOS/Android app). Under
**Settings → Connection** you find:

| | Example | Where it goes |
|---|---|---|
| Sensor ID | `550e8400-e29b-41d4-a716-446655440000` | `X-Sensor-Id` header and `sensorId` in the body |
| Secret | 32+ characters | only into the HMAC, **never** sent |

Host: `hydronode.tech`, HTTPS on port 443, TLS 1.2 or newer. The legacy host
`hydronode.texhfexlabs.de` keeps working for devices already in the field.

## 2. Signing a request

Every request carries three headers:

```
X-Sensor-Id: <sensor id>
X-Timestamp: <Unix seconds, when the request is sent>
X-Signature: Base64( HMAC-SHA256( secret, message ) )
```

- `message` = **canonical payload + X-Timestamp** (the decimal number appended as text).
- Base64 is the standard alphabet with `=` padding (44 characters).
- HydroNode accepts `X-Timestamp` within **±2 minutes** of its clock. Sync your clock (NTP, GPS,
  modem time). A request outside the window gets `401 Invalid timestamp`: sync and send again.

### The canonical payload

HydroNode does not sign the raw body you send; it **rebuilds** a canonical text from the fields
it parsed and checks the signature against that. You must build the same text:

- keys in exactly this order, no whitespace, no other keys,
- `type` is `[A-Z][A-Z0-9_]{0,63}` (e.g. `TEMPERATURE`, `PM25`, `SOIL_MOISTURE`),
- **values with exactly two decimals** (rule below).

| Request | Canonical payload |
|---|---|
| one value | `{"sensorId":"<id>","type":"<TYPE>","value":21.50,"timestamp":<ts>}` |
| one round | `{"sensorId":"<id>","timestamp":<ts>,"values":[{"type":"<A>","value":21.50},{"type":"<B>","value":48.00}]}` |
| stored rounds (envelope) | `{"sensorId":"<id>","timestamp":<sent>,"rounds":["<round signature>","<round signature>"]}` |
| command ack, OTA ack | the raw JSON body exactly as sent |
| signed download (GET) | path with query, e.g. `/api/device-ota/v1/image?job=8a1b` |

### Values: two decimals, the way HydroNode reads them

HydroNode stores values as 32-bit floats and formats them like Java's
`String.format(Locale.ROOT, "%.2f", (double) value)`:

1. take the value as a **32-bit float** (`21.125` stays `21.125`, `41.43` becomes `41.43000030517578`),
2. round its **exact** value to two decimals, **half up** (away from zero on a tie),
3. keep the minus sign for negative values, also for `-0.0` and for values that round to zero.

| Value | Text | Why |
|---|---|---|
| `21.5` | `21.50` | always two decimals |
| `21.125` | `21.13` | exact tie in binary: half up |
| `-21.125` | `-21.13` | away from zero |
| `0.005` | `0.00` | the float is 0.004999999888… |
| `2.675` | `2.67` | the float is 2.67499995… |
| `-0.004` | `-0.00` | sign kept |
| `99.995` | `100.00` | the float is 99.99500274… |

Easiest correct way: **send the value as the exact text you signed**. HydroNode parses `21.13`
back to the float 21.13 and formats it to `21.13` again, so text and signature always agree as long
as the text has two decimals and fits a 32-bit float (any value up to ±100 000 with two decimals).
`printf("%.2f")` alone is not enough: C libraries round an exact tie like `21.125` to even
(`21.12`). Every SDK here has a `format_value` / `formatValue` / `FormatValue` that follows the rule.

### Worked example

Secret `k3Y-for-test-vectors_only_0123456789abcdef`, sensor `550e8400-e29b-41d4-a716-446655440000`,
`TEMPERATURE` = 21.5 at 1730638800:

```
canonical  {"sensorId":"550e8400-e29b-41d4-a716-446655440000","type":"TEMPERATURE","value":21.50,"timestamp":1730638800}
message    canonical + "1730638800"
signature  (see vectors/signature-vectors.json, "single"[0])
```

```bash
printf '%s' "$canonical$ts" | openssl dgst -sha256 -hmac "$secret" -binary | base64
```

## 3. Endpoints

All `POST` with `Content-Type: application/json`.

### `POST /api/webhook/sensor-values` — one round, measured now (recommended)

```json
{"sensorId":"550e8400-...","timestamp":1730638800,"values":[{"type":"TEMPERATURE","value":21.50},{"type":"HUMIDITY","value":48.00}]}
```

- `timestamp` is when the values were measured and must equal `X-Timestamp` (so: within ±2 min).
- 1 to 64 values, each type once. All values share one request: one TLS round trip.

### `POST /api/webhook/sensor-value` — one value

```json
{"sensorId":"550e8400-...","type":"TEMPERATURE","value":21.50,"timestamp":1730638800}
```

The oldest endpoint, kept for old devices. Prefer `sensor-values`.

### `POST /api/webhook/sensor-rounds` — stored rounds, sent later

For readings measured earlier: a cellular board that sends once an hour, or a device that kept
its readings while offline. Each round keeps the time it was measured (up to 7 days back).

```json
{"sensorId":"550e8400-...","timestamp":1730642400,"rounds":[
  {"timestamp":1730638800,"signature":"<round signature>","values":[{"type":"TEMPERATURE","value":21.50}]},
  {"timestamp":1730639160,"signature":"<round signature>","values":[{"type":"TEMPERATURE","value":21.25}]}]}
```

- Each round is signed **on its own** exactly like a `sensor-values` round, with its own
  `timestamp` (canonical "one round" + round timestamp).
- `X-Signature` signs the **envelope**: send time and the list of round signatures in order.
- `X-Timestamp` and the body `timestamp` are the **send** time (±2 min).
- 1 to 100 rounds, at most 512 values in all, oldest first.
- A round sent twice (answer lost) is stored once.

### `POST /api/webhook/sensor-command-ack` — confirm commands

See [5. Commands](#5-commands). The signature covers the raw body as sent.

## 4. Answers

| Status | Meaning | What to do |
|---|---|---|
| `202` | Accepted | Read the body for commands |
| `400` | Malformed (bad type name, NaN, duplicate type, too many values) | Fix the request; sending it again does not help |
| `401 Invalid timestamp` | Clock off by more than 2 minutes | Sync the clock, send again once |
| `401 Invalid signature` | Wrong secret, or canonical text differs | Compare with the test vectors; the HydroNode device console shows what it expected |
| `409` | (stored rounds) the same request was taken a moment ago | Nothing |
| `429` | Too fast: one value per sensor **and type** every 10 s (stored rounds: value budget) | Slow down, retry later |
| `503` | Server could not store right now (`Retry-After: 10`) | Retry; nothing was stored |

`202` bodies:

```json
// sensor-value
{"commands":[...]}
// sensor-values
{"accepted":2,"rejected":[{"type":"PRESSURE","status":429}],"commands":[...]}
// sensor-rounds
{"accepted":6,"rounds":3,"rejected":[{"round":1,"status":422}],"retry":[2],"commands":[...]}
```

For stored rounds: drop every round you sent except the indexes in `retry` (429/503, send them
later). `rejected` with `422` (too old or in the future) or `401` (round signature wrong) will
never go in.

Further keys (`ota`, `config`, `settings`) are for the universal HydroNode firmware and the
Arduino library; ignore keys you do not know.

## 5. Commands

In HydroNode you send a command to your device: a **name**, a **type** and a **value**. It waits
on the server and arrives in the answer to your next value request (at most 8 per answer, expires
after 24 h):

```json
{"commands":[{"id":"3f1c8a5e-...","command":"pump","type":"UINT32","value":4000}]}
```

| `type` | JSON value | Range |
|---|---|---|
| `BOOL` | `true` / `false` | |
| `INT32` | number | −2147483648 … 2147483647 |
| `UINT32` | number | 0 … 4294967295 |
| `INT64` | number | 64-bit signed |
| `UINT64` | number | 64-bit unsigned |
| `STRING` | string | up to ~500 characters |

Commands from old app versions have no `type`. Decide per command: run it, or decline it with a
reason HydroNode shows (`NO_HANDLER`, `TYPE_MISMATCH`, `INVALID_VALUE`, `OTHER`). **Answer before
running anything** (a slow pump must not push the answer outside the 2-minute window):

```
POST /api/webhook/sensor-command-ack
{"sensorId":"550e8400-...","commandIds":["3f1c8a5e-..."],"declined":[{"id":"...","reason":"NO_HANDLER"}]}
```

`X-Signature` = Base64(HMAC-SHA256(secret, raw body + X-Timestamp)). Every command is delivered at
most once. Values come over the network: clamp them to what your hardware can safely do.

## 6. Optional device headers

Not needed, but they make the device show up nicely in the HydroNode fleet view:

```
X-Firmware: my-gateway/1.2.0 linux
X-Device-Status: uptime=3600;rssi=-67;net=wifi
```

`net` is one of `wifi`, `eth`, `lte-m`, `nb-iot`, `lte`, `2g`, `cellular`. `rssi` in dBm.
Unknown keys are ignored, a broken header is ignored as a whole and never costs a reading.

## 7. Limits

| Limit | Value |
|---|---|
| Clock window | ±2 minutes on `X-Timestamp` |
| Live values | one per sensor and type every 10 s (small burst allowed) |
| Values per round | 64 |
| Stored rounds | 100 per request, 512 values, 7 days back, budget 2000 values refilling 2/s |
| Sensors per account | 20, free |
| Answer size | up to ~8 KB (8 commands) |

## 8. Checklist for a new implementation

1. Format values with the rule in §2 and compare with `vectors/signature-vectors.json` → `values`.
2. Build the canonical payloads and compare with `single`, `round`, `rounds` → `canonical`, `envelope`.
3. Sign and compare with every `signature` in the file (also `raw`).
4. Send one value to `sensor-values`, expect `202`. Open the sensor in HydroNode and press
   <kbd>`</kbd>: the device console shows each request, the signature check and the clock offset.
5. Queue a command in HydroNode, answer it with `sensor-command-ack`, see it marked *Confirmed*.
