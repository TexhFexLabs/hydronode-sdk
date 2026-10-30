"""Tests of the Python SDK: the shared signature vectors, and the client against a fake server.

    python3 python/test_hydronode.py          (CPython)
    micropython python/test_hydronode.py      (MicroPython unix port)

No test framework: the same file runs on CPython and MicroPython.
"""

import json
import sys

HERE = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
sys.path.insert(0, HERE)
import hydronode  # noqa: E402

VECTORS = HERE + "/../vectors/signature-vectors.json"

failures = 0
checks = 0


def check(condition, label):
    global failures, checks
    checks += 1
    if not condition:
        failures += 1
        print("FAIL:", label)


def float_from_bits(text):
    import struct

    return struct.unpack("<f", struct.pack("<I", int(text, 16)))[0]


def test_vectors():
    with open(VECTORS) as f:
        doc = json.load(f)
    secret, sensor = doc["secret"], doc["sensorId"]

    for v in doc["values"]:
        got = hydronode.format_value(float_from_bits(v["float32"]))
        check(got == v["text"], "value %s: %s" % (v["input"], got))

    for c in doc["single"]:
        canonical = hydronode.value_payload(sensor, c["type"], c["value"], c["timestamp"])
        check(canonical == c["canonical"], "single " + c["name"])
        check(hydronode.sign(secret, canonical + str(c["timestamp"])) == c["signature"], "single sig " + c["name"])

    def check_round(c):
        values = [(v["type"], v["value"]) for v in c["values"]]
        canonical = hydronode.round_payload(sensor, c["timestamp"], values)
        check(canonical == c["canonical"], "round %d" % c["timestamp"])
        check(hydronode.sign(secret, canonical + str(c["timestamp"])) == c["signature"], "round sig")

    for c in doc["round"]:
        check_round(c)
    for c in doc["rounds"]:
        for r in c["rounds"]:
            check_round(r)
        envelope = hydronode.rounds_envelope(sensor, c["sent"], [r["signature"] for r in c["rounds"]])
        check(envelope == c["envelope"], "envelope " + c["name"])
        check(hydronode.sign(secret, envelope + str(c["sent"])) == c["signature"], "envelope sig")

    for c in doc["raw"]:
        check(hydronode.sign(secret, c["payload"] + str(c["timestamp"])) == c["signature"], "raw " + c["name"])

    # Long keys are hashed first (RFC 2104); RFC 4231 test case 6.
    check(hydronode.sign(b"\xaa" * 131, "Test Using Larger Than Block-Size Key - Hash Key First")
          == "YOQxWR7gtn8Niiaqy/W3f44LxiE3KMUUBUYEDw7jf1Q=", "long key")


def test_rules():
    for bad in ("temperature", "1TEMP", "PM2.5", "", "A" * 65):
        check(not hydronode.is_valid_type(bad), "invalid type " + bad)
    check(hydronode.is_valid_type("PM25"), "PM25")
    for bad in (float("nan"), float("inf")):
        try:
            hydronode.format_value(bad)
            check(False, "non-finite value accepted")
        except ValueError:
            check(True, "non-finite value refused")
    try:
        hydronode.round_payload("s", 1, [("T", 1), ("T", 2)])
        check(False, "duplicate type accepted")
    except ValueError:
        check(True, "duplicate type refused")


class FakeServer:
    """Answers like HydroNode and checks every signature with the secret."""

    def __init__(self, secret):
        self.secret = secret
        self.requests = []
        self.answers = []

    def __call__(self, method, path, headers, body):
        self.requests.append((path, headers, body))
        message = body if path != "/api/webhook/sensor-rounds" else self._envelope(body)
        if hydronode.sign(self.secret, message + headers["X-Timestamp"]) != headers["X-Signature"]:
            return 401, "Invalid signature"
        if self.answers:
            return self.answers.pop(0)
        return 202, '{"accepted":1,"commands":[]}'

    @staticmethod
    def _envelope(body):
        data = json.loads(body)
        return hydronode.rounds_envelope(data["sensorId"], data["timestamp"], [r["signature"] for r in data["rounds"]])


NOW = 1730642400


def client(server):
    return hydronode.HydroNode("sensor-1", "secret", transport=server, clock=lambda: NOW)


def test_client_sends_signed_requests():
    server = FakeServer("secret")
    hydro = client(server)
    # A list keeps the order on every Python; a dict does too on CPython (not on MicroPython,
    # which is fine: the body is built in the same order as the signed text).
    check(hydro.send_values([("TEMPERATURE", 21.125), ("HUMIDITY", 48)]).status == 202, "round accepted")
    check(hydro.send_values({"TEMPERATURE": 21.125, "HUMIDITY": 48}).status == 202, "dict accepted")
    path, headers, body = server.requests[-2]
    check(path == "/api/webhook/sensor-values", "round path")
    check(body == '{"sensorId":"sensor-1","timestamp":%d,"values":[{"type":"TEMPERATURE","value":21.13},'
          '{"type":"HUMIDITY","value":48.00}]}' % NOW, "round body is the canonical text")
    check(hydro.send_value("CO2", 612).status == 202, "single accepted")
    check(server.requests[-1][0] == "/api/webhook/sensor-value", "single path")


def test_commands_are_acked_before_they_run():
    server = FakeServer("secret")
    hydro = client(server)
    order = []
    hydro.on("pump", lambda ms: order.append(("pump", ms, len(server.requests))), "UINT32")
    hydro.on("lamp", lambda on: order.append(("lamp", on)), "BOOL")
    server.answers.append((202, json.dumps({"accepted": 1, "commands": [
        {"id": "c1", "command": "pump", "type": "UINT32", "value": 4000},
        {"id": "c2", "command": "lamp", "type": "UINT32", "value": 1},
        {"id": "c3", "command": "fan", "type": "BOOL", "value": True},
        {"id": "c4", "command": "pump", "type": "UINT32", "value": -1},
    ]})))
    hydro.send_values({"TEMPERATURE": 20})
    ack_path, _, ack_body = server.requests[1]
    check(ack_path == "/api/webhook/sensor-command-ack", "ack sent")
    ack = json.loads(ack_body)
    check(ack["commandIds"] == ["c1"], "pump confirmed")
    reasons = dict((d["id"], d["reason"]) for d in ack["declined"])
    check(reasons == {"c2": "TYPE_MISMATCH", "c3": "NO_HANDLER", "c4": "INVALID_VALUE"}, "declines %r" % reasons)
    check(order == [("pump", 4000, 2)], "pump ran after the ack: %r" % order)


def test_buffer_keeps_what_should_be_retried():
    server = FakeServer("secret")
    hydro = client(server)
    for i in range(5):
        hydro.record({"TEMPERATURE": 20 + i}, measured_at=NOW - 3600 + i * 600)
    server.answers.append((202, '{"accepted":3,"rounds":3,"rejected":[{"round":1,"status":429}],"retry":[1],"commands":[]}'))
    hydro.flush(per_request=4)
    check(len(hydro.buffer) == 2, "kept the retry round and the unsent one: %r" % hydro.buffer)
    check(hydro.buffer[0][0] == NOW - 3600 + 600, "the retried round stays first")
    body = json.loads(server.requests[0][2])
    check(len(body["rounds"]) == 4 and body["timestamp"] == NOW, "four rounds, sent now")
    check(body["rounds"][0]["timestamp"] == NOW - 3600, "measuring time kept")
    hydro.flush()
    check(hydro.buffer == [], "rest sent")
    server.answers.append((503, '{"retry":[0]}'))
    hydro.record({"TEMPERATURE": 1}, measured_at=NOW - 60)
    hydro.flush()
    check(len(hydro.buffer) == 1, "503 keeps everything")


def test_clock_must_be_set():
    hydro = hydronode.HydroNode("s", "k", transport=FakeServer("k"), clock=lambda: 12)
    try:
        hydro.send_values({"T": 1})
        check(False, "unset clock accepted")
    except RuntimeError:
        check(True, "unset clock refused")


for test in (test_vectors, test_rules, test_client_sends_signed_requests, test_commands_are_acked_before_they_run,
             test_buffer_keeps_what_should_be_retried, test_clock_must_be_set):
    test()
print("python sdk: %d checks, %d failed" % (checks, failures))
sys.exit(1 if failures else 0)
