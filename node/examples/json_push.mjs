// Send plain JSON to a JSON push interface: no signature, no clock, no SDK.
//
// For scripts and gateways that cannot sign. In HydroNode: Operations > Interfaces > Add interface >
// JSON push. Copy the push URL (it ends in the token, hpi_...) and run:
//
//   HYDRONODE_PUSH_URL="https://hydronode.tech/api/ingest/v1/push/hpi_..." node json_push.mjs
//
// The first request shows up as a discovered device in your project; Add turns its fields into data
// points. With "Device field" set on the interface (here "station") each value of it is its own device.
// Node 18+, no dependencies. The token also works as a header on the bare path:
// POST https://hydronode.tech/api/ingest/v1/push with "X-Push-Token: hpi_...".

const url = process.env.HYDRONODE_PUSH_URL;
if (!url) {
  console.error('Set HYDRONODE_PUSH_URL to the push URL of your interface.');
  process.exit(2);
}

const answer = await fetch(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ station: 'north', temp: 12.5, humidity: 71, wind: 3.1 }),
});
// 200 {"status":"ok","accepted":3,"skipped":0} once fields are mapped; 401 unknown token, 429 too fast.
console.log(answer.status, await answer.text());
process.exit(answer.ok ? 0 : 1);
