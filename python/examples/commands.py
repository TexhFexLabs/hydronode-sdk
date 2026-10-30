"""Take commands from HydroNode: switch a relay, run a pump, show a text.

In HydroNode open the sensor, then Commands, and send for example
    pump   UINT32  4000      run the pump for 4 seconds
    lamp   BOOL    true      switch the lamp
    note   STRING  hello     print a text

Commands wait on the server and arrive with the answer to the next reading. The SDK confirms
them to HydroNode before your handler runs, and declines what has no handler (HydroNode shows it).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hydronode import HydroNode  # noqa: E402

hydro = HydroNode(os.environ["SENSOR_ID"], os.environ["SECRET"])


def run_pump(ms):
    ms = min(ms, 10000)  # never trust a network value: at most 10 s
    print("pump on for", ms, "ms")
    time.sleep(ms / 1000)
    print("pump off")


hydro.on("pump", run_pump, "UINT32")
hydro.on("lamp", lambda on: print("lamp", "on" if on else "off"), "BOOL")
hydro.on("note", lambda text: print("note:", text), "STRING")

while True:
    hydro.send_values({"WATER_LEVEL": 72.0})  # commands come back in this answer
    time.sleep(30)
