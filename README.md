<h1 align="center">HydroNode SDK</h1>

<p align="center">
  Send signed sensor readings to <a href="https://hydronode.tech/"><strong>HydroNode</strong></a> from anything that speaks HTTPS.<br>
  Python, MicroPython, JavaScript, Go and plain shell. One file per language, no dependencies.
</p>

<p align="center">
  <a href="https://github.com/TexhFexLabs/hydronode-sdk/actions/workflows/test.yml"><img src="https://github.com/TexhFexLabs/hydronode-sdk/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <a href="PROTOCOL.md"><img src="https://img.shields.io/badge/protocol-documented-2ea44f.svg" alt="Protocol"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="PROTOCOL.md">Protocol</a> ·
  <a href="#test-vectors">Test vectors</a> ·
  <a href="#examples">Examples</a>
</p>

---

[HydroNode](https://hydronode.tech/) is a free platform for sensor data: charts, anomaly detection, AI
analysis, alerts, Home Assistant, iOS and Android apps. Devices send readings with a signature
(HMAC-SHA256) instead of a password. This repository makes that signature trivial everywhere:
a Raspberry Pi, a Pico W with MicroPython, a Node.js gateway, a Go service, a cron job with curl.

On an ESP32 or ESP8266 with Arduino, use the [HydroNode Arduino library](https://github.com/TexhFexLabs/HydroNode-Library)
(WiFi, Ethernet, LTE-M, NB-IoT). With ESPHome, use the [ESPHome component](https://github.com/TexhFexLabs/hydronode-esphome).

## Quick start

1. Create a sensor in [HydroNode](https://hydronode.tech/) and copy **Sensor ID** and **Secret** from
   *Settings → Connection*.
2. Pick your language, copy one file, send:

| You have | Copy | Needs |
|---|---|---|
| Raspberry Pi, Linux, macOS, Windows | [`python/hydronode.py`](python/hydronode.py) | Python 3.8+ |
| ESP32, ESP8266, Pico W with MicroPython | [`python/hydronode.py`](python/hydronode.py) + [`hydronode_roots.py`](python/hydronode_roots.py) | MicroPython 1.20+ |
| Node.js, Deno, Bun | [`node/hydronode.mjs`](node/hydronode.mjs) | Node 18+ |
| Go | `go get github.com/TexhFexLabs/hydronode-sdk/go` | Go 1.21+ |
| A shell | [`shell/hydronode.sh`](shell/hydronode.sh) | bash, curl, openssl |

**Python / MicroPython**

```python
from hydronode import HydroNode

hydro = HydroNode("your-sensor-id", "your-secret")
hydro.send_values({"TEMPERATURE": 21.5, "HUMIDITY": 48})
```

**JavaScript**

```js
import { HydroNode } from './hydronode.mjs';

const hydro = new HydroNode({ sensorId: 'your-sensor-id', secret: 'your-secret' });
await hydro.sendValues({ TEMPERATURE: 21.5, HUMIDITY: 48 });
```

**Go**

```go
client := hydronode.New("your-sensor-id", "your-secret")
client.SendValues(ctx, hydronode.Values{{"TEMPERATURE", 21.5}, {"HUMIDITY", 48}})
```

**Shell**

```bash
SENSOR_ID=... SECRET=... ./hydronode.sh send TEMPERATURE=21.5 HUMIDITY=48
```

`202` means stored. Open the sensor in HydroNode and press <kbd>`</kbd>: the device console shows
every request as it arrives, with the signature check and how far your clock is off.

## What you get

| | Python | MicroPython | JS | Go | Shell |
|---|:-:|:-:|:-:|:-:|:-:|
| Send one round (all values, one request) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Send one value | ✓ | ✓ | ✓ | ✓ | |
| Store readings, send later with their time | ✓ | ✓ | ✓ | ✓ | |
| Commands from HydroNode (typed, confirmed before they run) | ✓ | ✓ | ✓ | ✓ | |
| Values formatted exactly like the server (any float) | ✓ | ✓ | ✓ | ✓ | two decimals |
| Server certificate checked | system store | with `hydronode_roots` | system store | system store | curl |
| Pure signing functions for your own HTTP client | ✓ | ✓ | ✓ | ✓ | ✓ |

**Store now, send later.** `record()` keeps a reading with the time it was measured, `flush()`
sends what is stored in one request (up to 100 rounds, 7 days back). Use it to never lose a reading
when the network is gone, or to send once an hour from a battery device. A round sent twice is
stored once, and HydroNode learns the sending rhythm, so an hourly device is not shown as late.

```python
hydro.record({"TEMPERATURE": 12.3})   # no network
hydro.flush()                         # all stored rounds, each with its own time
```

**Commands.** In HydroNode you send a command with a name, a type and a value (`pump`, `UINT32`,
`4000`). It arrives with the answer to your next reading; the SDK confirms or declines it (HydroNode
shows which) and then calls your handler:

```python
hydro.on("pump", lambda ms: run_pump(min(ms, 10000)), "UINT32")
```

**Switches and choices.** HydroNode shows every value you send as a data point. Read it as on/off or as
a choice in the web app and bind a command to it: the app and Home Assistant then show a switch or a
picker. Register the command with its type and send back the state the hardware really took:

```python
hydro.on("heater_mode", set_mode, "UINT32")   # a choice arrives as the option index
hydro.on("pump", set_pump, "BOOL")            # on/off
hydro.send_values({"HEATER_MODE": mode, "PUMP": 1 if pump_on else 0})
```

The same works in JavaScript (`hydro.on("pump", fn, "BOOL")`) and Go (`client.On("pump", "BOOL", fn)`).
HydroNode shows a change as *Pending* until the device confirms the command or reports a matching
value. Handlers run inside the send call, so report the new state with the next send, not from inside
the handler. Full example: [`python/examples/choice_and_state.py`](python/examples/choice_and_state.py).

## How the signature works

```
X-Sensor-Id:  <sensor id>
X-Timestamp:  <Unix seconds, within ±2 minutes of the server>
X-Signature:  Base64( HMAC-SHA256( secret, canonical payload + X-Timestamp ) )
```

The canonical payload is the JSON body with keys in a fixed order, no spaces and values with
exactly two decimals, for example
`{"sensorId":"…","timestamp":1730638800,"values":[{"type":"TEMPERATURE","value":21.50}]}`.
The details that are easy to get wrong (how `21.125` is rounded, `-0.00`, stored rounds, command
answers) are all in **[PROTOCOL.md](PROTOCOL.md)**, together with every endpoint, answer and limit.

## Test vectors

[`vectors/signature-vectors.json`](vectors/signature-vectors.json) is the shared truth: value texts,
canonical payloads and signatures for every request type, with a test secret. The HydroNode backend,
the Arduino library and every SDK here run against this file in their tests. Writing your own
client (Rust, C#, Lua, a PLC)? Make it pass the file and it will work against HydroNode.

The file is generated by [`tools/generate_vectors.py`](tools/generate_vectors.py) and checked by the
backend's `SignatureVectorsTest`; do not edit it by hand.

## Examples

| Example | What it shows |
|---|---|
| [`python/examples/send_once.py`](python/examples/send_once.py) | The smallest complete program |
| [`python/examples/raspberry_pi_offline_safe.py`](python/examples/raspberry_pi_offline_safe.py) | Raspberry Pi loop that never loses a reading (buffer file) |
| [`python/examples/commands.py`](python/examples/commands.py) | Pump, lamp and text commands from HydroNode |
| [`python/examples/choice_and_state.py`](python/examples/choice_and_state.py) | A heater mode and a pump set from HydroNode, reporting their state |
| [`python/examples/micropython_esp32.py`](python/examples/micropython_esp32.py) | MicroPython on ESP32 / Pico W: WiFi, NTP, verified TLS |
| [`python/examples/micropython_collect_hourly.py`](python/examples/micropython_collect_hourly.py) | Battery: measure every 6 min, send 10 readings once an hour, deep sleep |
| [`node/examples/send.mjs`](node/examples/send.mjs) | One reading from Node.js |
| [`node/examples/gateway.mjs`](node/examples/gateway.mjs) | Forward readings from anywhere, buffered to a file, with commands |
| [`go/examples/send/main.go`](go/examples/send/main.go) | One reading and a command handler in Go |
| [`shell/hydronode.sh`](shell/hydronode.sh) `dry-run` | Shows what would be signed, to debug a 401 |

## Running the tests

```bash
python3 python/test_hydronode.py          # CPython
micropython python/test_hydronode.py      # MicroPython unix port
node --test node/test.mjs
(cd go && go test ./...)
bash shell/test.sh
```

CI runs all of them on every push ([`.github/workflows/test.yml`](.github/workflows/test.yml)).

## Without signing: JSON push

The SDK speaks the signed device protocol. If a script, Node-RED, Telegraf or a weather station can only send JSON to a
URL, use a **JSON push interface** instead (HydroNode › Operations › Interfaces › New › JSON push). It gives one URL with
its own token (`hpi_…`); no signature, no timestamp:

```bash
curl -X POST "https://hydronode.tech/api/ingest/v1/push/hpi_…" \
  -H 'Content-Type: application/json' \
  -d '{"station":"north","temp":12.5,"wind":3.1}'
```

- Several devices on one URL: set an **id field** on the interface (here `station`). Each value of it becomes its own
  discovered device ("north", "south"); the id field itself is never a value.
- New devices and fields show up as discovered in your project; **Add** turns them into a sensor with data points.
- Older push URLs of a single sensor (`hpt_…`) keep working unchanged.
- Same path takes the token as header too: `X-Push-Token: hpi_…` or `Authorization: Bearer hpi_…` on
  `POST /api/ingest/v1/push`.

## Related

- [HydroNode](https://hydronode.tech/) · [Docs](https://hydronode.tech/docs/) · [Sensor types](https://hydronode.tech/docs/guide/sensor-types/)
- [Arduino library](https://github.com/TexhFexLabs/HydroNode-Library): ESP32, ESP8266 and any Arduino Client, WiFi, Ethernet, LTE-M, NB-IoT
- [ESPHome component](https://github.com/TexhFexLabs/hydronode-esphome)
- [Home Assistant integration](https://github.com/TexhFexLabs/hydronode-homeassistant)

## License

MIT, see [LICENSE](LICENSE). Made by **TexhFexLabs**. Questions: support@hydronode.tech
