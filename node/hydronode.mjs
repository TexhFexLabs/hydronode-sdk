// HydroNode device SDK for JavaScript: Node.js 18+, Deno, Bun. One file, no dependencies.
//
//   import { HydroNode } from './hydronode.mjs';
//
//   const hydro = new HydroNode({ sensorId: '...', secret: '...' });
//   await hydro.sendValues({ TEMPERATURE: 21.5, HUMIDITY: 48 });
//
// Signs every request the way HydroNode checks it (HMAC-SHA256), formats values the way HydroNode
// reads them, sends one value, one round or stored rounds, and answers commands.
// Protocol: PROTOCOL.md in https://github.com/TexhFexLabs/hydronode-sdk

import { createHmac } from 'node:crypto';

export const VERSION = '1.0.0';
export const MAX_VALUES_PER_ROUND = 64;
export const MAX_ROUNDS_PER_REQUEST = 100;
export const MAX_VALUES_PER_ROUNDS_REQUEST = 512;

// ---------------------------------------------------------------------------------------------
// The wire contract. Pure functions: use them on their own with your own HTTP client.
// ---------------------------------------------------------------------------------------------

/**
 * The value with two decimals, as HydroNode reads it: the value as a 32-bit float, rounded half
 * up on its exact value (like Java's "%.2f"), minus sign kept also for -0.0.
 *
 *   formatValue(21.125) === '21.13'; formatValue(0.005) === '0.00'; formatValue(-0.004) === '-0.00'
 */
export function formatValue(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TypeError('value must be a finite number');
  }
  const f32 = Math.fround(value);
  if (!Number.isFinite(f32)) throw new RangeError('value does not fit a 32-bit float');
  const view = new DataView(new ArrayBuffer(4));
  view.setFloat32(0, f32);
  const bits = view.getUint32(0);
  const negative = bits >>> 31 === 1;
  const exponent = (bits >>> 23) & 0xff;
  const fraction = bits & 0x7fffff;
  const mantissa = BigInt(exponent === 0 ? fraction : fraction | 0x800000);
  const shift = exponent === 0 ? -149 : exponent - 150;
  let cents = mantissa * 100n;
  if (shift >= 0) {
    cents <<= BigInt(shift);
  } else if (shift < -60) {
    cents = 0n; // far below 0.005
  } else {
    const denominator = 1n << BigInt(-shift);
    const remainder = cents % denominator;
    cents /= denominator;
    if (2n * remainder >= denominator) cents += 1n;
  }
  const part = (cents % 100n).toString().padStart(2, '0');
  return `${negative ? '-' : ''}${cents / 100n}.${part}`;
}

/** True for A-Z, 0-9 and _, starting with a letter, at most 64 characters (e.g. PM25). */
export function isValidType(name) {
  return typeof name === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(name);
}

/** Base64(HMAC-SHA256(secret, message)): the X-Signature header. */
export function sign(secret, message) {
  return createHmac('sha256', secret).update(message, 'utf8').digest('base64');
}

/** [[type, value], ...] from an object, a Map or a list of pairs, checked like the server does. */
function pairs(values) {
  const list = values instanceof Map ? [...values] : Array.isArray(values) ? values : Object.entries(values);
  if (list.length < 1 || list.length > MAX_VALUES_PER_ROUND) throw new RangeError('send 1 to 64 values');
  const seen = new Set();
  for (const [type, value] of list) {
    if (!isValidType(type)) throw new TypeError(`invalid type ${JSON.stringify(type)}: use A-Z, 0-9 and _ like TEMPERATURE`);
    if (seen.has(type)) throw new TypeError(`type ${type} appears twice`);
    seen.add(type);
    formatValue(value);
  }
  return list;
}

/** Canonical text of one value. */
export function valuePayload(sensorId, type, value, timestamp) {
  return `{"sensorId":"${sensorId}","type":"${type}","value":${formatValue(value)},"timestamp":${timestamp}}`;
}

/** Canonical text of one round. */
export function roundPayload(sensorId, timestamp, values) {
  const items = pairs(values).map(([t, v]) => `{"type":"${t}","value":${formatValue(v)}}`).join(',');
  return `{"sensorId":"${sensorId}","timestamp":${timestamp},"values":[${items}]}`;
}

/** Canonical text of a stored-rounds request: send time and the round signatures in order. */
export function roundsEnvelope(sensorId, sent, signatures) {
  return `{"sensorId":"${sensorId}","timestamp":${sent},"rounds":[${signatures.map((s) => `"${s}"`).join(',')}]}`;
}

// ---------------------------------------------------------------------------------------------
// The client.
// ---------------------------------------------------------------------------------------------

const INT_RANGES = {
  INT32: [-(2n ** 31n), 2n ** 31n - 1n],
  UINT32: [0n, 2n ** 32n - 1n],
  INT64: [-(2n ** 63n), 2n ** 63n - 1n],
  UINT64: [0n, 2n ** 64n - 1n],
};

function fits(type, value) {
  if (type === 'BOOL') return typeof value === 'boolean';
  if (type === 'STRING') return typeof value === 'string';
  const range = INT_RANGES[type];
  if (!range || typeof value !== 'number' || !Number.isInteger(value)) return false;
  const v = BigInt(value);
  return v >= range[0] && v <= range[1];
}

/** What HydroNode answered: `status` 202 = accepted, `data` the JSON body or null. */
export class HydroNodeResponse {
  constructor(status, text) {
    this.status = status;
    this.text = text ?? '';
    this.data = null;
    if (this.text.startsWith('{')) {
      try {
        this.data = JSON.parse(this.text);
      } catch {
        // plain-text error answers stay in `text`
      }
    }
  }
  get ok() { return this.status >= 200 && this.status < 300; }
  get accepted() { return this.data?.accepted ?? 0; }
  get rejected() { return this.data?.rejected ?? []; }
  get retry() { return this.data?.retry ?? []; }
  get commands() { return this.data?.commands ?? []; }
}

/**
 * Sends signed readings to HydroNode and takes commands back.
 *
 * options.sensorId, options.secret  from HydroNode: sensor settings, Connection
 * options.baseUrl                   default https://hydronode.tech
 * options.clock                     () => Unix seconds (default Date.now())
 * options.fetch                     your own fetch (proxy, test server, a modem bridge ...)
 * options.firmware                  X-Firmware for the fleet view, e.g. "gateway/1.2.0 linux"
 * options.network                   net= for the fleet view: wifi, eth, lte-m, nb-iot, lte ...
 * options.timeoutMs                 per request (default 15000)
 */
export class HydroNode {
  constructor({ sensorId, secret, baseUrl = 'https://hydronode.tech', clock, fetch: fetchFn, firmware, network, timeoutMs = 15000, debug = false }) {
    if (!sensorId || !secret) throw new TypeError('sensorId and secret are required');
    this.sensorId = sensorId;
    this.secret = secret;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.clock = clock ?? (() => Math.floor(Date.now() / 1000));
    this.fetch = fetchFn ?? globalThis.fetch;
    this.firmware = firmware ?? `hydronode-js/${VERSION}`;
    this.network = network;
    this.timeoutMs = timeoutMs;
    this.debug = debug;
    /** Stored rounds: [[measuredAt, [[type, value], ...]], ...] — plain JSON, save it if you like. */
    this.buffer = [];
    this.handlers = new Map();
  }

  /** Unix seconds; throws while the clock is not set. */
  now() {
    const now = Math.floor(this.clock());
    if (now < 1600000000) throw new Error('clock not set: HydroNode would reject the signature');
    return now;
  }

  /** One value, measured now. Prefer sendValues() for several values. */
  async sendValue(type, value) {
    if (!isValidType(type)) throw new TypeError(`invalid type ${JSON.stringify(type)}`);
    const now = this.now();
    const payload = valuePayload(this.sensorId, type, value, now);
    return this.#handle(await this.#post('/api/webhook/sensor-value', payload, now, sign(this.secret, payload + now)));
  }

  /**
   * One round: all values in one request with one timestamp.
   * @param values {TEMPERATURE: 21.5} | [['TEMPERATURE', 21.5]] | Map
   * @param measuredAt Unix seconds (default now), within 2 minutes; older rounds: record() + flush()
   */
  async sendValues(values, measuredAt) {
    const ts = measuredAt === undefined ? this.now() : Math.floor(measuredAt);
    const payload = roundPayload(this.sensorId, ts, values);
    return this.#handle(await this.#post('/api/webhook/sensor-values', payload, ts, sign(this.secret, payload + ts)));
  }

  /**
   * Rounds measured earlier, each with its own time (up to 7 days back).
   * @param rounds [[measuredAt, values], ...], oldest first, at most 100 rounds / 512 values
   */
  async sendRounds(rounds) {
    if (rounds.length < 1 || rounds.length > MAX_ROUNDS_PER_REQUEST) throw new RangeError('send 1 to 100 rounds');
    const sent = this.now();
    const signatures = [];
    let count = 0;
    const parts = rounds.map(([measuredAt, values]) => {
      const ts = Math.floor(measuredAt);
      const list = pairs(values);
      count += list.length;
      const signature = sign(this.secret, roundPayload(this.sensorId, ts, list) + ts);
      signatures.push(signature);
      const items = list.map(([t, v]) => `{"type":"${t}","value":${formatValue(v)}}`).join(',');
      return `{"timestamp":${ts},"signature":"${signature}","values":[${items}]}`;
    });
    if (count > MAX_VALUES_PER_ROUNDS_REQUEST) throw new RangeError('at most 512 values per request');
    const body = `{"sensorId":"${this.sensorId}","timestamp":${sent},"rounds":[${parts.join(',')}]}`;
    const envelope = roundsEnvelope(this.sensorId, sent, signatures);
    return this.#handle(await this.#post('/api/webhook/sensor-rounds', body, sent, sign(this.secret, envelope + sent)));
  }

  /** Stores one round without sending it. Sent by flush(). */
  record(values, measuredAt) {
    const ts = measuredAt === undefined ? this.now() : Math.floor(measuredAt);
    this.buffer.push([ts, pairs(values).map(([t, v]) => [t, v])]);
  }

  /**
   * Sends the stored rounds, oldest first. Rounds HydroNode took or refused for good leave the
   * buffer, rounds it could not take now stay. Returns the last answer (null when empty). A
   * network error rejects; the rounds stay.
   */
  async flush(perRequest = 12) {
    let answer = null;
    while (this.buffer.length > 0) {
      const chunk = this.buffer.slice(0, perRequest);
      answer = await this.sendRounds(chunk);
      if (answer.status === 400) {
        this.buffer.splice(0, chunk.length); // broken for good
        continue;
      }
      if (!answer.ok) break;
      const keep = answer.retry.filter((i) => i >= 0 && i < chunk.length).map((i) => chunk[i]);
      this.buffer.splice(0, chunk.length, ...keep);
      if (keep.length > 0) break;
    }
    return answer;
  }

  /**
   * Runs `handler(value)` for a command from HydroNode. `type`: BOOL, INT32, UINT32, INT64, UINT64
   * or STRING; commands of other types are declined with TYPE_MISMATCH. Without type: any.
   */
  on(command, handler, type) {
    if (!this.handlers.has(command)) this.handlers.set(command, []);
    this.handlers.get(command).push({ type, handler });
    return this;
  }

  /** Confirms command IDs and declines others ([{id, reason}]). Called for you. */
  async ack(accepted, declined = []) {
    const payload = JSON.stringify({ sensorId: this.sensorId, commandIds: accepted, declined });
    const now = this.now();
    return this.#post('/api/webhook/sensor-command-ack', payload, now, sign(this.secret, payload + now));
  }

  #pick({ command, type: wire, value }) {
    const list = this.handlers.get(command);
    if (!list?.length) return [null, 'NO_HANDLER'];
    for (const { type, handler } of list) {
      if (!type || !wire || type === wire) {
        if (type && !fits(type, value)) return [null, wire ? 'INVALID_VALUE' : 'TYPE_MISMATCH'];
        return [handler, null];
      }
    }
    return [null, 'TYPE_MISMATCH'];
  }

  async #handle(response) {
    const run = [];
    const accepted = [];
    const declined = [];
    for (const command of response.ok ? response.commands : []) {
      const [handler, reason] = this.#pick(command);
      if (handler) {
        accepted.push(command.id);
        run.push([handler, command.value]);
      } else {
        declined.push({ id: command.id, reason });
      }
    }
    // Answer first: a slow handler must not push the answer outside the 2-minute window.
    if (accepted.length || declined.length) await this.ack(accepted, declined);
    for (const [handler, value] of run) await handler(value);
    return response;
  }

  async #post(path, body, timestamp, signature) {
    const headers = {
      'Content-Type': 'application/json',
      'X-Sensor-Id': this.sensorId,
      'X-Timestamp': String(timestamp),
      'X-Signature': signature,
      'X-Firmware': this.firmware,
    };
    if (this.network) headers['X-Device-Status'] = `net=${this.network}`;
    const reply = await this.fetch(this.baseUrl + path, {
      method: 'POST',
      headers,
      body,
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    const text = await reply.text();
    if (this.debug) console.log('[HydroNode]', path, reply.status, text.slice(0, 200));
    return new HydroNodeResponse(reply.status, text);
  }
}
