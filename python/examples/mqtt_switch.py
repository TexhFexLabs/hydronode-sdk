"""A pump on MQTT that HydroNode switches and that reports the state it is really in.

Not part of the SDK: MQTT needs no signature, any MQTT client works. This one uses paho-mqtt 2.x:
    pip install "paho-mqtt>=2,<3"

In HydroNode, once:
1. Operations > Interfaces > Add interface > MQTT. Copy Username, Secret and the Topic area
   (hn/<short>/#). Every topic you use lies inside that area.
2. Run this script. "pump" (topic garage/pump) shows up as a discovered device in your project; Add
   makes it a sensor with the data point "state" (read as on/off, because the value is ON/OFF).
3. Open the data point, Command: turn on "Publish over MQTT when it is set", Topic garage/pump/set,
   On ON, Off OFF. The app and Home Assistant now show a switch, "Pending" until the new state
   arrives on garage/pump (without it, the switch turns failed after 2 minutes).

    MQTT_USERNAME=3f2a9c0d1b7e.k8w2qa MQTT_SECRET=... MQTT_TOPIC_ROOT=hn/3f2a9c0d1b7e \
        python3 mqtt_switch.py
"""

import json
import os
import ssl

import paho.mqtt.client as mqtt

HOST = os.environ.get("MQTT_HOST", "mqtt.hydronode.tech")
PORT = int(os.environ.get("MQTT_PORT", "8883"))
TLS = os.environ.get("MQTT_TLS", "1") != "0"  # HydroNode only takes TLS; 0 is for a local test broker
ROOT = os.environ["MQTT_TOPIC_ROOT"].rstrip("/")  # hn/<short>, topics in HydroNode are relative to it
STATE = ROOT + "/garage/pump"
SET = ROOT + "/garage/pump/set"

pump = {"on": False}


def switch_pump(on):
    pump["on"] = on
    print("pump", "on" if on else "off")  # switch your relay here


def report(client):
    # Report what the hardware did, also after a change on the device itself.
    client.publish(STATE, json.dumps({"state": "ON" if pump["on"] else "OFF"}), qos=1, retain=True)


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print("refused:", reason_code)  # wrong username or secret, or 3 connections already open
        return
    client.subscribe(SET, qos=1)
    report(client)  # the current state once after every (re)connect


def on_message(client, userdata, message):
    payload = message.payload.decode("utf-8", "replace").strip().upper()
    if payload in ("ON", "OFF"):
        switch_pump(payload == "ON")
    report(client)  # anything else: keep the state and report it again


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ["MQTT_SECRET"])
if TLS:
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)  # system CA store, Let's Encrypt certificate
client.on_connect = on_connect
client.on_message = on_message
client.connect(HOST, PORT, keepalive=60)
client.loop_forever()  # reconnects on its own
