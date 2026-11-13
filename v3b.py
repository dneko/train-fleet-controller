# SPDX-License-Identifier: MIT
# Copyright (c) 2020 Henrik Blidh
# Copyright (c) 2022-2023 The Pybricks Authors
# Modifications copyright (c) 2026 David Turner
# Adapted from the Pybricks communication example; see THIRD_PARTY_NOTICES.md.

import asyncio
import threading
import time
from contextlib import suppress
from bleak import BleakScanner, BleakClient
import tkinter as tk

PYBRICKS_COMMAND_EVENT_CHAR_UUID = "c5f50002-8280-46da-89f4-6d8051e4aeef"
HUB_NAME = "Train B"

loop = asyncio.new_event_loop()
ready_event = asyncio.Event()
client = None
last_ready_time = 0
health_status = "red"


# ----------------------------
# BLE SECTION
# ----------------------------

async def ble_main(status_label, health_canvas):

    global client, last_ready_time

    main_task = asyncio.current_task()

    def handle_disconnect(_):
        status_label.config(text="Disconnected", fg="red")
        set_health("red", health_canvas)
        if not main_task.done():
            main_task.cancel()

    def handle_rx(_, data: bytearray):
        global last_ready_time
        if data[0] == 0x01:
            payload = data[1:]
            if payload == b"rdy":
                ready_event.set()
                last_ready_time = time.time()

    device = await BleakScanner.find_device_by_name(HUB_NAME)

    if device is None:
        status_label.config(text="Hub Not Found", fg="red")
        return

    client = BleakClient(device, handle_disconnect)
    await client.connect()
    await client.start_notify(PYBRICKS_COMMAND_EVENT_CHAR_UUID, handle_rx)

    status_label.config(text="Connected", fg="green")

    # Start keepalive
    asyncio.create_task(keep_alive(health_canvas))

    while True:
        await asyncio.sleep(1)


async def send_command(command: str):
    global client

    if client is None or not client.is_connected:
        return

    await ready_event.wait()
    ready_event.clear()

    await client.write_gatt_char(
        PYBRICKS_COMMAND_EVENT_CHAR_UUID,
        b"\x06" + command.encode() + b"\n",
        response=True
    )


async def keep_alive(health_canvas):
    global last_ready_time

    while True:

        # Fire-and-forget ping
        if client and client.is_connected:
            await client.write_gatt_char(
                PYBRICKS_COMMAND_EVENT_CHAR_UUID,
                b"\x06ping\n",
                response=False
            )

        now = time.time()
        delta = now - last_ready_time

        if delta < 2:
            set_health("green", health_canvas)
        elif delta < 4:
            set_health("yellow", health_canvas)
        else:
            set_health("red", health_canvas)

        await asyncio.sleep(1)

# ----------------------------
# TK SECTION
# ----------------------------

def start_ble(status_label, health_canvas):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_main(status_label, health_canvas))


def on_slider_change(value):
    speed = int(float(value))
    asyncio.run_coroutine_threadsafe(send_command(f"s{speed}"), loop)


def stop_motor():
    slider.set(0)
    asyncio.run_coroutine_threadsafe(send_command("s0"), loop)


def set_health(color, canvas):
    canvas.itemconfig(health_dot, fill=color)


root = tk.Tk()
root.title("Train Controller")

status_label = tk.Label(root, text="Connecting...", font=("Arial", 14))
status_label.pack(pady=5)

health_canvas = tk.Canvas(root, width=50, height=50)
health_canvas.pack()
health_dot = health_canvas.create_oval(10, 10, 40, 40, fill="red")

slider = tk.Scale(
    root,
    from_=100,
    to=-100,
    orient=tk.VERTICAL,
    length=300,
    label="Speed",
    command=on_slider_change
)
slider.pack(pady=10)

stop_button = tk.Button(root, text="STOP", bg="red", fg="white", command=stop_motor)
stop_button.pack(pady=10)

threading.Thread(
    target=start_ble,
    args=(status_label, health_canvas),
    daemon=True
).start()

root.mainloop()
