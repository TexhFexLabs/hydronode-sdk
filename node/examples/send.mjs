// Send one reading from Node.js 18+:
//   SENSOR_ID=... SECRET=... node examples/send.mjs
import { HydroNode } from '../hydronode.mjs';

const hydro = new HydroNode({ sensorId: process.env.SENSOR_ID, secret: process.env.SECRET, debug: true });

// All values of one reading in one request; types are A-Z, 0-9 and _.
const answer = await hydro.sendValues({ TEMPERATURE: 21.5, HUMIDITY: 48.2 });

// 202 = stored. 401 = check SENSOR_ID / SECRET or the clock. 429 = too fast.
console.log(answer.status, answer.text);
