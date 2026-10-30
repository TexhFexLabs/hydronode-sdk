"""MicroPython on an ESP32 or Raspberry Pi Pico W: WiFi, time, one reading a minute.

Copy to the board: hydronode.py, hydronode_roots.py and this file as main.py
(e.g. `mpremote cp hydronode.py hydronode_roots.py : && mpremote cp micropython_esp32.py :main.py`).
"""

import time

import network
import ntptime
from hydronode import HydroNode
from hydronode_roots import ROOTS

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
SENSOR_ID = "your-sensor-id-here"
SECRET = "your-secret-key-here"

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(WIFI_SSID, WIFI_PASSWORD)
while not wlan.isconnected():
    time.sleep(0.5)

# The signature carries the time; HydroNode accepts +-2 minutes. ntptime sets the clock to UTC.
ntptime.settime()

# ca=ROOTS: check the server certificate (needs ~40 KB RAM for TLS; fine on ESP32 and Pico W).
hydro = HydroNode(SENSOR_ID, SECRET, ca=ROOTS, network="wifi", debug=True)


def read_sensor():
    # TODO: your sensor, e.g. a BME280 over I2C, or a DS18B20 via onewire
    return 21.5, 48.0


while True:
    temperature, humidity = read_sensor()
    try:
        # A list keeps the order on MicroPython (dicts do not); either is signed correctly.
        hydro.send_values([("TEMPERATURE", temperature), ("HUMIDITY", humidity)])
    except OSError as error:
        print("send failed:", error)
    time.sleep(60)
