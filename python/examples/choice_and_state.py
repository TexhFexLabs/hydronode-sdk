"""A heater mode and a pump that HydroNode sets and that report the state they are in.

HydroNode shows every value as a data point. In the web app, once after the first values arrived:
    HEATER_MODE  Read as: Choice, options 0 Off, 1 Eco, 2 Boost. Command heater_mode, type UINT32.
    PUMP         Read as: On/off. Command pump, type BOOL.
The app (and Home Assistant) then shows them as controls, "Pending" until the device confirms.

Report the state the hardware really took, also after changes made on the device itself, so the
app never shows a state that is not real. Works the same on MicroPython.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hydronode import HydroNode  # noqa: E402

MODES = ("Off", "Eco", "Boost")

hydro = HydroNode(os.environ["SENSOR_ID"], os.environ["SECRET"])
state = {"heater_mode": 0, "pump": False, "changed": True}


def set_heater_mode(index):
    if index >= len(MODES):  # not an option: keep the mode, report it again
        print("heater_mode", index, "is not a mode, kept", MODES[state["heater_mode"]])
    else:
        state["heater_mode"] = index
        print("heater", MODES[index])  # switch your relays here
    state["changed"] = True


def set_pump(on):
    state["pump"] = on
    print("pump", "on" if on else "off")  # switch your relay here
    state["changed"] = True


hydro.on("heater_mode", set_heater_mode, "UINT32")
hydro.on("pump", set_pump, "BOOL")


def round_values():
    return {
        "WATER_TEMPERATURE": 24.5,  # your measurement
        "HEATER_MODE": state["heater_mode"],
        "PUMP": 1 if state["pump"] else 0,
    }


last_round = 0
while True:
    # Handlers run inside send_values(); the new state goes out with the next call, a few seconds
    # later, instead of from inside the handler. One round per type every 10 s at most.
    if state["changed"] or time.time() - last_round >= 60:
        state["changed"] = False
        hydro.send_values(round_values())  # commands come back in this answer
        last_round = time.time()
    time.sleep(10)
