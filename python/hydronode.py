"""HydroNode device SDK for Python and MicroPython.

One file, no dependencies. Runs on CPython 3.8+ (Raspberry Pi, servers, gateways) and on
MicroPython (ESP32, ESP8266, Raspberry Pi Pico W, ...). Copy this file next to your script.

    from hydronode import HydroNode

    hydro = HydroNode("your-sensor-id", "your-secret")
    hydro.send_values({"TEMPERATURE": 21.5, "HUMIDITY": 48})

What it does for you:

* signs every request (HMAC-SHA256) exactly the way HydroNode checks it,
* formats values the way HydroNode reads them (two decimals, half up on the 32-bit float),
* sends one value, one round, or rounds you stored earlier with the time they were measured,
* takes commands from HydroNode, confirms them and calls your handlers.

The protocol behind it: PROTOCOL.md in https://github.com/TexhFexLabs/hydronode-sdk
"""

import json
import struct
import time

try:
    import hashlib
except ImportError:  # some MicroPython builds name it uhashlib
    import uhashlib as hashlib  # type: ignore

try:
    import binascii
except ImportError:
    import ubinascii as binascii  # type: ignore

__version__ = "1.0.0"

HOST = "hydronode.tech"
MAX_VALUES_PER_ROUND = 64
MAX_ROUNDS_PER_REQUEST = 100
MAX_VALUES_PER_ROUNDS_REQUEST = 512

# MicroPython on many boards counts time from 2000-01-01, CPython from 1970-01-01.
_EPOCH_OFFSET = 946684800 if time.gmtime(0)[0] == 2000 else 0

# Valid Unix time (any synced clock is past 2020-09-13).
_MIN_VALID_TIME = 1600000000


# --------------------------------------------------------------------------------------------
# The wire contract. Pure functions: use them on their own when you bring your own HTTP client.
# --------------------------------------------------------------------------------------------


def format_value(value):
    """The value with two decimals, as HydroNode reads it.

    HydroNode keeps values as 32-bit floats and formats them like Java's "%.2f": half up on the
    exact value of the float, minus sign kept (also for -0.0). Done with integers on the float's
    bits, so it is exact on CPython and on MicroPython with 32-bit floats alike.

    >>> format_value(21.125), format_value(0.005), format_value(-0.004)
    ('21.13', '0.00', '-0.00')
    """
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    negative = bits >> 31
    exponent = (bits >> 23) & 0xFF
    fraction = bits & 0x7FFFFF
    if exponent == 0xFF:
        raise ValueError("value must be a finite number")
    if exponent == 0:
        mantissa, shift = fraction, -149  # subnormal
    else:
        mantissa, shift = fraction | 0x800000, exponent - 150
    cents = mantissa * 100
    if shift >= 0:
        cents <<= shift
    elif shift < -60:
        cents = 0  # far below 0.005
    else:
        denominator = 1 << -shift
        cents, remainder = divmod(cents, denominator)
        if 2 * remainder >= denominator:
            cents += 1
    whole, part = divmod(cents, 100)
    return ("-" if negative else "") + str(whole) + "." + ("0" + str(part))[-2:]


def is_valid_type(name):
    """True for A-Z, 0-9 and _, starting with a letter, at most 64 characters (e.g. PM25)."""
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not ("A" <= name[0] <= "Z"):
        return False
    for c in name:
        if not ("A" <= c <= "Z" or "0" <= c <= "9" or c == "_"):
            return False
    return True


def _hmac_sha256(key, message):
    """HMAC (RFC 2104); MicroPython has no hmac module, so it is spelled out here."""
    block = 64
    if len(key) > block:
        key = hashlib.sha256(key).digest()
    key = key + b"\x00" * (block - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key))
    inner.update(message)
    outer = hashlib.sha256(bytes(b ^ 0x5C for b in key))
    outer.update(inner.digest())
    return outer.digest()


def sign(secret, message):
    """Base64(HMAC-SHA256(secret, message)): the X-Signature header."""
    if isinstance(secret, str):
        secret = secret.encode()
    if isinstance(message, str):
        message = message.encode()
    return binascii.b2a_base64(_hmac_sha256(secret, message)).decode().strip()


def _pairs(values):
    """[(type, value), ...] from a dict or a list of pairs, checked like the server does."""
    pairs = list(values.items()) if isinstance(values, dict) else [tuple(v) for v in values]
    if not 1 <= len(pairs) <= MAX_VALUES_PER_ROUND:
        raise ValueError("send 1 to 64 values")
    seen = set()
    for name, value in pairs:
        if not is_valid_type(name):
            raise ValueError("invalid type %r: use A-Z, 0-9 and _ like TEMPERATURE" % (name,))
        if name in seen:
            raise ValueError("type %s appears twice" % name)
        seen.add(name)
        format_value(value)  # raises for NaN and infinity
    return pairs


def value_payload(sensor_id, type_, value, timestamp):
    """Canonical text of one value: {"sensorId":..,"type":..,"value":..,"timestamp":..}."""
    return '{"sensorId":"%s","type":"%s","value":%s,"timestamp":%d}' % (
        sensor_id, type_, format_value(value), timestamp)


def round_payload(sensor_id, timestamp, values):
    """Canonical text of one round: {"sensorId":..,"timestamp":..,"values":[{"type":..,"value":..}]}."""
    items = ",".join('{"type":"%s","value":%s}' % (t, format_value(v)) for t, v in _pairs(values))
    return '{"sensorId":"%s","timestamp":%d,"values":[%s]}' % (sensor_id, timestamp, items)


def rounds_envelope(sensor_id, sent, signatures):
    """Canonical text of a stored-rounds request: send time and the round signatures in order."""
    return '{"sensorId":"%s","timestamp":%d,"rounds":[%s]}' % (
        sensor_id, sent, ",".join('"%s"' % s for s in signatures))


# --------------------------------------------------------------------------------------------
# Transport: http.client on CPython, a small socket client on MicroPython, or your own.
# --------------------------------------------------------------------------------------------


class Response:
    """What HydroNode answered. `status` 202 = accepted; `data` is the JSON body (or None)."""

    def __init__(self, status, text=""):
        self.status = status
        self.text = text or ""
        self.data = None
        if self.text[:1] == "{":
            try:
                self.data = json.loads(self.text)
            except ValueError:
                pass

    @property
    def ok(self):
        return 200 <= self.status < 300

    def _get(self, key, default):
        return self.data.get(key, default) if isinstance(self.data, dict) else default

    @property
    def accepted(self):
        return self._get("accepted", 0)

    @property
    def rejected(self):
        return self._get("rejected", [])

    @property
    def retry(self):
        return self._get("retry", [])

    @property
    def commands(self):
        return self._get("commands", [])

    def __repr__(self):
        return "Response(%d, %s)" % (self.status, self.text[:120])


def _cpython_transport(host, port, timeout, context):
    import http.client
    import ssl

    state = {"conn": None}
    ssl_context = context or ssl.create_default_context()

    def send(method, path, headers, body):
        # One kept-alive connection; a connection the server closed is opened again once.
        for attempt in (0, 1):
            if state["conn"] is None:
                state["conn"] = http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl_context)
            try:
                state["conn"].request(method, path, body=body, headers=headers)
                reply = state["conn"].getresponse()
                return reply.status, reply.read().decode("utf-8", "replace")
            except (http.client.HTTPException, OSError) as error:
                state["conn"].close()
                state["conn"] = None
                if attempt:
                    # One exception type for every network problem: callers catch OSError.
                    raise OSError("HydroNode request failed: %s" % error)
        return 0, ""

    return send


def _micropython_transport(host, port, timeout, ca):
    import socket
    import ssl

    def send(method, path, headers, body):
        address = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
        sock = socket.socket()
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            if hasattr(ssl, "SSLContext"):
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                if ca:
                    context.verify_mode = ssl.CERT_REQUIRED
                    context.load_verify_locations(cadata=ca)
                else:
                    context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=host)
            else:
                sock = ssl.wrap_socket(sock, server_hostname=host)
            data = body.encode() if isinstance(body, str) else body
            lines = ["%s %s HTTP/1.0" % (method, path), "Host: %s" % host, "Content-Length: %d" % len(data)]
            lines += ["%s: %s" % kv for kv in headers.items()]
            sock.write(("\r\n".join(lines) + "\r\n\r\n").encode() + data)
            raw = b""
            while True:
                chunk = sock.read(1024)
                if not chunk:
                    break
                raw += chunk
        finally:
            sock.close()
        head, _, payload = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        return status, payload.decode()

    return send


def _default_transport(host, port, timeout, ca, context):
    import sys

    if sys.implementation.name == "micropython":
        return _micropython_transport(host, port, timeout, ca)
    return _cpython_transport(host, port, timeout, context)


# --------------------------------------------------------------------------------------------
# The client.
# --------------------------------------------------------------------------------------------

_INT_RANGES = {
    "INT32": (-(2 ** 31), 2 ** 31 - 1),
    "UINT32": (0, 2 ** 32 - 1),
    "INT64": (-(2 ** 63), 2 ** 63 - 1),
    "UINT64": (0, 2 ** 64 - 1),
}


class HydroNode:
    """Sends signed readings to HydroNode and takes commands back.

    sensor_id, secret  from HydroNode: sensor settings, Connection
    host, port         the HydroNode server (default hydronode.tech:443)
    timeout            seconds per request
    clock              function returning Unix seconds (default time.time(), fixed for MicroPython)
    ca                 MicroPython only: root certificates (bytes) to check the server with;
                       see hydronode_roots.py. CPython checks against the system store.
    ssl_context        CPython only: your own ssl.SSLContext
    transport          your own function (method, path, headers, body) -> (status, text), e.g.
                       for a modem with an AT HTTP stack. Replaces the built-in HTTPS client.
    firmware           X-Firmware, e.g. "pi-gateway/1.2.0 linux" (optional, shows in the fleet view)
    network            net= for the fleet view: wifi, eth, lte-m, nb-iot, lte, 2g, cellular
    debug              print every request and answer
    """

    def __init__(self, sensor_id, secret, host=HOST, port=443, timeout=15, clock=None, ca=None,
                 ssl_context=None, transport=None, firmware=None, network=None, debug=False):
        self.sensor_id = sensor_id
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.clock = clock or (lambda: int(time.time()) + _EPOCH_OFFSET)
        self.firmware = firmware or ("hydronode-py/" + __version__)
        self.network = network
        self.debug = debug
        self.buffer = []  # stored rounds: [measured_at, [[type, value], ...]]
        self._handlers = {}
        self._transport = transport or _default_transport(host, port, timeout, ca, ssl_context)

    # ---- time ----

    def now(self):
        """Unix seconds. Raises when the clock is not set (HydroNode would reject the signature)."""
        now = int(self.clock())
        if now < _MIN_VALID_TIME:
            raise RuntimeError("clock not set: sync it first (ntptime.settime() on MicroPython)")
        return now

    # ---- sending ----

    def send_value(self, type_, value):
        """One value, measured now. Prefer send_values() for several values."""
        if not is_valid_type(type_):
            raise ValueError("invalid type %r: use A-Z, 0-9 and _ like TEMPERATURE" % (type_,))
        now = self.now()
        # The body is the canonical text itself: what is signed is what is sent.
        payload = value_payload(self.sensor_id, type_, value, now)
        return self._handle(self._post("/api/webhook/sensor-value", payload, now, sign(self.secret, payload + str(now))))

    def send_values(self, values, measured_at=None):
        """One round: all values in one request with one timestamp.

        values       {"TEMPERATURE": 21.5, "HUMIDITY": 48} or [("TEMPERATURE", 21.5), ...]
        measured_at  Unix seconds (default now). Must be within 2 minutes of now; older rounds go
                     through record() + flush().
        """
        timestamp = int(measured_at) if measured_at is not None else self.now()
        payload = round_payload(self.sensor_id, timestamp, values)
        # The body is the canonical text itself: what is signed is what is sent.
        return self._handle(self._post("/api/webhook/sensor-values", payload, timestamp,
                                       sign(self.secret, payload + str(timestamp))))

    def send_rounds(self, rounds):
        """Rounds measured earlier, each with its own time (up to 7 days back).

        rounds  [(measured_at, values), ...], oldest first, at most 100 rounds / 512 values.
        Returns the answer; its `retry` lists the indexes to send again later (429, 503).
        """
        if not 1 <= len(rounds) <= MAX_ROUNDS_PER_REQUEST:
            raise ValueError("send 1 to 100 rounds")
        sent = self.now()
        parts, signatures, count = [], [], 0
        for measured_at, values in rounds:
            pairs = _pairs(values)
            count += len(pairs)
            canonical = round_payload(self.sensor_id, int(measured_at), pairs)
            signature = sign(self.secret, canonical + str(int(measured_at)))
            signatures.append(signature)
            items = ",".join('{"type":"%s","value":%s}' % (t, format_value(v)) for t, v in pairs)
            parts.append('{"timestamp":%d,"signature":"%s","values":[%s]}' % (int(measured_at), signature, items))
        if count > MAX_VALUES_PER_ROUNDS_REQUEST:
            raise ValueError("at most 512 values per request")
        body = '{"sensorId":"%s","timestamp":%d,"rounds":[%s]}' % (self.sensor_id, sent, ",".join(parts))
        envelope = rounds_envelope(self.sensor_id, sent, signatures)
        return self._handle(self._post("/api/webhook/sensor-rounds", body, sent, sign(self.secret, envelope + str(sent))))

    # ---- buffer: store now, send later ----

    def record(self, values, measured_at=None):
        """Stores one round without sending it (no network). Sent by flush()."""
        timestamp = int(measured_at) if measured_at is not None else self.now()
        self.buffer.append([timestamp, [list(p) for p in _pairs(values)]])

    def flush(self, per_request=12):
        """Sends the stored rounds, oldest first. Rounds HydroNode took or refused for good leave
        the buffer, rounds it could not take now stay. Returns the last answer (None if empty).
        A failed connection raises; the rounds stay."""
        answer = None
        while self.buffer:
            chunk = self.buffer[:per_request]
            answer = self.send_rounds([(t, [tuple(p) for p in v]) for t, v in chunk])
            if answer.status == 400:
                del self.buffer[:len(chunk)]  # broken for good; sending again cannot help
                continue
            if not answer.ok:
                break
            keep = [chunk[i] for i in answer.retry if 0 <= i < len(chunk)]
            self.buffer[:len(chunk)] = keep
            if keep:
                break
        return answer

    def save_buffer(self, path):
        """Writes the stored rounds to a JSON file (survives a restart or a power cut)."""
        with open(path, "w") as f:
            json.dump(self.buffer, f)

    def load_buffer(self, path):
        """Reads rounds saved by save_buffer(); a missing or broken file leaves the buffer empty."""
        try:
            with open(path) as f:
                data = json.load(f)
            self.buffer = [r for r in data if isinstance(r, list) and len(r) == 2]
        except (OSError, ValueError):
            self.buffer = []

    # ---- commands ----

    def on(self, command, handler, type_=None):
        """Runs `handler(value)` for a command from HydroNode.

        type_  BOOL, INT32, UINT32, INT64, UINT64 or STRING: only commands of that type run, all
               others are declined with TYPE_MISMATCH. None takes any type.
        """
        self._handlers.setdefault(command, []).append((type_, handler))

    def ack(self, accepted, declined=()):
        """Confirms command IDs and declines others ([(id, reason), ...]). Called for you."""
        payload = json.dumps({"sensorId": self.sensor_id, "commandIds": list(accepted),
                              "declined": [{"id": i, "reason": r} for i, r in declined]})
        now = self.now()
        return self._post("/api/webhook/sensor-command-ack", payload, now, sign(self.secret, payload + str(now)))

    def _pick(self, command):
        name, wire, value = command.get("command"), command.get("type"), command.get("value")
        handlers = self._handlers.get(name)
        if not handlers:
            return None, "NO_HANDLER"
        for type_, handler in handlers:
            if type_ is None or wire is None or type_ == wire:
                if type_ is not None and not _fits(type_, value):
                    return None, "INVALID_VALUE" if wire else "TYPE_MISMATCH"
                return handler, None
        return None, "TYPE_MISMATCH"

    def _handle(self, response):
        """Answers the commands of a response (before running them), then runs the accepted ones."""
        commands = response.commands if response.ok else []
        run, accepted, declined = [], [], []
        for command in commands:
            handler, reason = self._pick(command)
            if handler:
                accepted.append(command["id"])
                run.append((handler, command.get("value")))
            else:
                declined.append((command["id"], reason))
        if accepted or declined:
            self.ack(accepted, declined)
        for handler, value in run:
            handler(value)
        return response

    # ---- HTTP ----

    def _post(self, path, body, timestamp, signature):
        headers = {
            "Content-Type": "application/json",
            "X-Sensor-Id": self.sensor_id,
            "X-Timestamp": str(timestamp),
            "X-Signature": signature,
            "X-Firmware": self.firmware,
        }
        if self.network:
            headers["X-Device-Status"] = "net=" + self.network
        status, text = self._transport("POST", path, headers, body)
        if self.debug:
            print("[HydroNode]", path, status, text[:200])
        return Response(status, text)


def _fits(type_, value):
    if type_ == "BOOL":
        return isinstance(value, bool)
    if type_ == "STRING":
        return isinstance(value, str)
    if type_ in _INT_RANGES:
        low, high = _INT_RANGES[type_]
        return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    return False
