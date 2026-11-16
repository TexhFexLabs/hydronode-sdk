# LoRaWAN downlinks through an interface

A LoRaWAN device does not use this SDK: the network server (TTN, ChirpStack, Helium) posts its uplinks to
HydroNode. Bytes back to the device (a downlink) go the same way in reverse, through the **LoRaWAN interface**
the device's uplinks arrive on. This page shows the setup once and then one downlink from a script.

LoRaWAN is class A: a queued downlink leaves with the device's next uplink, not at once.

## 1. Set up the interface once

1. HydroNode › Operations › Interfaces. Your LoRaWAN interface is there (or **Add interface › LoRaWAN** for a
   webhook with its own token). Paste its webhook URL and the header `X-Webhook-Token` into TTN, ChirpStack or
   Helium.
2. Open the interface › **Downlinks**. Connect ChirpStack (server address and API token, **Save**) or get the
   downlink key from TTN, press **Test**, then switch it on in the step **Turn on**. It stays off until a test
   passed.

Downlink access belongs to that one interface. A device uses the interface its last uplink came through, so a key
entered for one interface never reaches devices of another. The owner sets it up, for an interface of a project
also members with Manage there.

## 2. Send a downlink

In the web app: open the sensor › **Send downlink** (fPort and hex bytes), or the device console's Commands tab:
`2 11` or `10 01 2C confirmed`.

From a script, with an API token with full access (Profile › API tokens, `hat_…`):

```bash
SENSOR_ID=...   # the sensor of the LoRaWAN device
TOKEN=hat_...

# Can a downlink go out right now? {"possible": true, "network": "CHIRPSTACK", "maxBytes": 51, ...}
curl -s "https://hydronode.tech/api/sensors/$SENSOR_ID/downlinks/status" -H "Authorization: Bearer $TOKEN"

# Queue fPort 2, one byte 0x11. 202 {"command": {...}}
curl -s -X POST "https://hydronode.tech/api/sensors/$SENSOR_ID/downlinks" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"fPort": 2, "payloadHex": "11", "confirmed": false, "label": "Clean SPS30"}'

# Follow it: Queued, Sent, Acknowledged or Failed (rows with "transport": "LORAWAN")
curl -s "https://hydronode.tech/api/sensors/$SENSOR_ID/commands" -H "Authorization: Bearer $TOKEN"
```

| Field | |
|---|---|
| `fPort` | 1 to 223 |
| `payloadHex` | 1 to 51 bytes; spaces, `:` and a leading `0x` are ignored |
| `confirmed` | optional, default `false`; `true` asks the device for an acknowledgement |
| `label` | optional, up to 64 characters, shown in the command history |

Answers: `409 DOWNLINK_NOT_POSSIBLE` with the reason (downlinks not turned on for the interface, or TTN has not
sent a downlink key yet), `409 QUEUE_FULL` (16 not yet sent), `429 RATE_LIMITED` (20 per minute and sensor). A
downlink not sent within 24 h turns expired.

Whoever may send commands to the sensor (Edit and up) may send downlinks.
