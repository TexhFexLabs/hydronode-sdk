// Tests of the JavaScript SDK: the shared signature vectors and the client against a fake server.
//   node --test node/test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { HydroNode, formatValue, isValidType, roundPayload, roundsEnvelope, sign, valuePayload } from './hydronode.mjs';

const doc = JSON.parse(readFileSync(new URL('../vectors/signature-vectors.json', import.meta.url)));
const { secret, sensorId } = doc;
const fromBits = (hex) => {
  const view = new DataView(new ArrayBuffer(4));
  view.setUint32(0, parseInt(hex, 16));
  return view.getFloat32(0);
};

test('values are formatted like the shared table', () => {
  for (const v of doc.values) assert.equal(formatValue(fromBits(v.float32)), v.text, `value ${v.input}`);
  assert.throws(() => formatValue(NaN));
  assert.throws(() => formatValue(1e39));
});

test('single values, rounds, stored rounds and raw bodies match the shared table', () => {
  for (const c of doc.single) {
    const canonical = valuePayload(sensorId, c.type, c.value, c.timestamp);
    assert.equal(canonical, c.canonical, c.name);
    assert.equal(sign(secret, canonical + c.timestamp), c.signature);
  }
  const checkRound = (c) => {
    const canonical = roundPayload(sensorId, c.timestamp, c.values.map((v) => [v.type, v.value]));
    assert.equal(canonical, c.canonical);
    assert.equal(sign(secret, canonical + c.timestamp), c.signature);
  };
  doc.round.forEach(checkRound);
  for (const c of doc.rounds) {
    c.rounds.forEach(checkRound);
    const envelope = roundsEnvelope(sensorId, c.sent, c.rounds.map((r) => r.signature));
    assert.equal(envelope, c.envelope, c.name);
    assert.equal(sign(secret, envelope + c.sent), c.signature);
  }
  for (const c of doc.raw) assert.equal(sign(secret, c.payload + c.timestamp), c.signature, c.name);
});

test('type names follow the backend rule', () => {
  for (const bad of ['temperature', '1TEMP', 'PM2.5', '', 'A'.repeat(65)]) assert.equal(isValidType(bad), false, bad);
  assert.equal(isValidType('PM25'), true);
  assert.throws(() => roundPayload('s', 1, [['T', 1], ['T', 2]]));
});

const NOW = 1730642400;

/** A fetch that answers like HydroNode and checks every signature. */
function fakeServer(key, answers = []) {
  const requests = [];
  const fetch = async (url, init) => {
    const path = new URL(url).pathname;
    const body = init.body;
    const ts = init.headers['X-Timestamp'];
    let message = body;
    if (path === '/api/webhook/sensor-rounds') {
      const data = JSON.parse(body);
      message = roundsEnvelope(data.sensorId, data.timestamp, data.rounds.map((r) => r.signature));
    }
    requests.push({ path, body, headers: init.headers });
    if (sign(key, message + ts) !== init.headers['X-Signature']) return new Response('Invalid signature', { status: 401 });
    const [status, text] = answers.shift() ?? [202, '{"accepted":1,"commands":[]}'];
    return new Response(text, { status });
  };
  return { fetch, requests, answers };
}

test('requests are signed and the body is the signed text', async () => {
  const server = fakeServer('secret');
  const hydro = new HydroNode({ sensorId: 'sensor-1', secret: 'secret', fetch: server.fetch, clock: () => NOW });
  assert.equal((await hydro.sendValues({ TEMPERATURE: 21.125, HUMIDITY: 48 })).status, 202);
  assert.equal(server.requests[0].path, '/api/webhook/sensor-values');
  assert.equal(server.requests[0].body,
    `{"sensorId":"sensor-1","timestamp":${NOW},"values":[{"type":"TEMPERATURE","value":21.13},{"type":"HUMIDITY","value":48.00}]}`);
  assert.equal((await hydro.sendValue('CO2', 612)).status, 202);
});

test('commands are acknowledged before they run', async () => {
  const server = fakeServer('secret', [[202, JSON.stringify({ accepted: 1, commands: [
    { id: 'c1', command: 'pump', type: 'UINT32', value: 4000 },
    { id: 'c2', command: 'lamp', type: 'UINT32', value: 1 },
    { id: 'c3', command: 'fan', type: 'BOOL', value: true },
    { id: 'c4', command: 'pump', type: 'UINT32', value: -1 },
  ] })]]);
  const hydro = new HydroNode({ sensorId: 'sensor-1', secret: 'secret', fetch: server.fetch, clock: () => NOW });
  const ran = [];
  hydro.on('pump', (ms) => ran.push(['pump', ms, server.requests.length]), 'UINT32');
  hydro.on('lamp', (on) => ran.push(['lamp', on]), 'BOOL');
  await hydro.sendValues({ TEMPERATURE: 20 });
  const ack = JSON.parse(server.requests[1].body);
  assert.equal(server.requests[1].path, '/api/webhook/sensor-command-ack');
  assert.deepEqual(ack.commandIds, ['c1']);
  assert.deepEqual(Object.fromEntries(ack.declined.map((d) => [d.id, d.reason])),
    { c2: 'TYPE_MISMATCH', c3: 'NO_HANDLER', c4: 'INVALID_VALUE' });
  assert.deepEqual(ran, [['pump', 4000, 2]]);
});

test('the buffer keeps what HydroNode asks to retry', async () => {
  const server = fakeServer('secret', [[202, '{"accepted":3,"rounds":3,"rejected":[{"round":1,"status":429}],"retry":[1],"commands":[]}']]);
  const hydro = new HydroNode({ sensorId: 'sensor-1', secret: 'secret', fetch: server.fetch, clock: () => NOW });
  for (let i = 0; i < 5; i++) hydro.record({ TEMPERATURE: 20 + i }, NOW - 3600 + i * 600);
  await hydro.flush(4);
  assert.equal(hydro.buffer.length, 2);
  assert.equal(hydro.buffer[0][0], NOW - 3000);
  const body = JSON.parse(server.requests[0].body);
  assert.equal(body.rounds.length, 4);
  assert.equal(body.rounds[0].timestamp, NOW - 3600);
  await hydro.flush();
  assert.equal(hydro.buffer.length, 0);
});

test('an unset clock is refused', async () => {
  const hydro = new HydroNode({ sensorId: 's', secret: 'k', fetch: fakeServer('k').fetch, clock: () => 12 });
  await assert.rejects(() => hydro.sendValues({ T: 1 }));
});
