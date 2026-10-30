"""A Raspberry Pi that never loses a reading.

Every minute it reads the CPU temperature (swap in your sensor) and sends it. When the internet
is gone, readings go into a buffer file and leave, with the time they were measured, as soon as a
send works again. Survives restarts and power cuts (the buffer is a JSON file).

    SENSOR_ID=... SECRET=... python3 raspberry_pi_offline_safe.py

Run it as a systemd service for a real installation.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hydronode import HydroNode  # noqa: E402

INTERVAL_S = 60
BUFFER_FILE = os.path.expanduser("~/.hydronode-buffer.json")

hydro = HydroNode(os.environ["SENSOR_ID"], os.environ["SECRET"], network="eth",
                  firmware="pi-gateway/1.0 linux")
hydro.load_buffer(BUFFER_FILE)


def read_cpu_temperature():
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        return int(f.read()) / 1000.0


while True:
    # 1. Measure and store first: whatever happens next, the reading is safe.
    hydro.record({"TEMPERATURE": read_cpu_temperature()})
    hydro.save_buffer(BUFFER_FILE)

    # 2. Send everything that waits (normally just this one reading).
    try:
        answer = hydro.flush()
        print(time.strftime("%H:%M:%S"), "sent", answer.status if answer else "-", len(hydro.buffer), "waiting")
    except OSError as error:  # no network, DNS, TLS ...
        print(time.strftime("%H:%M:%S"), "offline:", error, "-", len(hydro.buffer), "readings waiting")
    hydro.save_buffer(BUFFER_FILE)

    time.sleep(INTERVAL_S)
