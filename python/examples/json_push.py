"""Send plain JSON to a JSON push interface: no signature, no clock, no SDK.

For scripts and devices that cannot sign. In HydroNode: Operations > Interfaces > Add interface >
JSON push. Copy the push URL (it ends in the token, hpi_...) and run:

    HYDRONODE_PUSH_URL="https://hydronode.tech/api/ingest/v1/push/hpi_..." python3 json_push.py

The first request shows up as a discovered device in your project. Add turns its fields into data
points. Several devices on one URL: set "Device field" on the interface (here "station"); each
value of it becomes its own device and is never stored as a value itself.

Python 3.8+, standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ["HYDRONODE_PUSH_URL"]


def push(fields):
    request = urllib.request.Request(
        URL,
        data=json.dumps(fields).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as answer:
            return answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as error:  # 401 unknown token, 429 too fast, 4xx bad payload
        return error.code, json.loads(error.read() or b"{}")


status, body = push({"station": "north", "temp": 12.5, "humidity": 71, "wind": 3.1})
print(status, body)  # 200 {"status": "ok", "accepted": 3, "skipped": 0} once fields are mapped
sys.exit(0 if status == 200 else 1)
