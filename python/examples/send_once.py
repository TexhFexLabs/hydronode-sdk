"""Send one reading from any computer with Python 3.8+ (Raspberry Pi, server, laptop).

    SENSOR_ID=... SECRET=... python3 send_once.py

Copy hydronode.py next to this file (or run it from the repository). No packages to install.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hydronode import HydroNode  # noqa: E402

hydro = HydroNode(os.environ["SENSOR_ID"], os.environ["SECRET"], debug=True)

# All values of one reading in one request. Types: A-Z, 0-9 and _ (see the sensor type list
# at https://hydronode.tech/docs/guide/sensor-types/).
answer = hydro.send_values({"TEMPERATURE": 21.5, "HUMIDITY": 48.2})

# 202 = stored. 401 = check SENSOR_ID / SECRET or the computer's clock. 429 = too fast.
print(answer.status, answer.text)
