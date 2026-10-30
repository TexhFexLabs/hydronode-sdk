// A gateway that forwards readings from somewhere else (MQTT, serial, a vendor API ...) to
// HydroNode and never loses one: readings go into a buffer first and are flushed every 30 s, each
// with the time it was measured. When HydroNode is unreachable they wait (saved to a file).
//
//   SENSOR_ID=... SECRET=... node examples/gateway.mjs
import { readFileSync, writeFileSync } from 'node:fs';
import { HydroNode } from '../hydronode.mjs';

const BUFFER_FILE = './hydronode-buffer.json';
const hydro = new HydroNode({
  sensorId: process.env.SENSOR_ID,
  secret: process.env.SECRET,
  firmware: 'my-gateway/1.0.0 node',
  network: 'eth',
});

try {
  hydro.buffer = JSON.parse(readFileSync(BUFFER_FILE, 'utf8'));
} catch {
  hydro.buffer = [];
}
const save = () => writeFileSync(BUFFER_FILE, JSON.stringify(hydro.buffer));

// Commands from HydroNode (send them in the app: name "relay", type BOOL).
hydro.on('relay', (on) => console.log('relay', on ? 'on' : 'off'), 'BOOL');

// TODO: replace with your source. Here: a fake reading every 10 s.
setInterval(() => {
  hydro.record({ TEMPERATURE: 20 + Math.random() * 2, CO2: 600 + Math.round(Math.random() * 50) });
  save();
}, 10_000);

setInterval(async () => {
  try {
    const answer = await hydro.flush();
    if (answer) console.log(new Date().toISOString(), 'sent', answer.status, `${hydro.buffer.length} waiting`);
  } catch (error) {
    console.log(new Date().toISOString(), 'offline:', error.message, `${hydro.buffer.length} waiting`);
  }
  save();
}, 30_000);
