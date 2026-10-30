"""MicroPython on battery: measure every 6 minutes, send the 10 readings once an hour.

Each wake-up measures, stores the round in a file on the board's flash and goes back to deep
sleep. Every 10th wake-up connects WiFi (or a modem) and sends all 10 in one request, each with
the time it was measured. The clock (RTC) keeps running through deep sleep on ESP32 and Pico.

Copy to the board as main.py together with hydronode.py and hydronode_roots.py.
"""

import time

import machine
import network
import ntptime
from hydronode import HydroNode
from hydronode_roots import ROOTS

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
SENSOR_ID = "your-sensor-id-here"
SECRET = "your-secret-key-here"
MEASURE_EVERY_S = 360
ROUNDS_PER_SEND = 10
BUFFER_FILE = "hydronode-buffer.json"

hydro = HydroNode(SENSOR_ID, SECRET, ca=ROOTS)
hydro.load_buffer(BUFFER_FILE)


def online():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(40):
        if wlan.isconnected():
            ntptime.settime()  # exact time before signing
            return True
        time.sleep(0.5)
    return False


# First start after power-on: no time yet. Go online once to set the clock.
if time.time() < 700000000:  # MicroPython epoch 2000: anything this small is unset
    online()

hydro.record([("TEMPERATURE", 12.3), ("SOIL_MOISTURE", 37.0)])  # TODO: real readings
hydro.save_buffer(BUFFER_FILE)

if len(hydro.buffer) >= ROUNDS_PER_SEND and online():
    try:
        hydro.flush()
    except OSError as error:
        print("send failed, the rounds stay:", error)
    hydro.save_buffer(BUFFER_FILE)

network.WLAN(network.STA_IF).active(False)
machine.deepsleep(MEASURE_EVERY_S * 1000)
