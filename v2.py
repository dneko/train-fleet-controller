# SPDX-License-Identifier: MIT
# Copyright (c) 2020 Henrik Blidh
# Copyright (c) 2022-2023 The Pybricks Authors
# Modifications copyright (c) 2026 David Turner
# Adapted from the Pybricks communication example; see THIRD_PARTY_NOTICES.md.

import asyncio
import threading
from contextlib import suppress
from bleak import BleakScanner, BleakClient
import tkinter as tk

PYBRICKS_COMMAND_EVENT_CHAR_UUID = "c5f50002-8280-46da-89f4-6d8051e4aeef"
HUB_NAME = "Train A"

# Shared objects
loop = asyncio.new_event_loop()
ready_event = asyncio.Event()
client = None


# ----------------------------
# BLE SECTION
# ----------------------------

async def ble_main(status_label):

    global client
    main_task = asyncio.current_task()

    def handle_disconnect(_):
        print("Hub was disconnected.")
        status_label.config(text="Disconnected", fg="red")
        if not main_task.done():
            main_task.cancel()

    def handle_rx(_, data: bytearray):
        if data[0] == 0x01:
            payload = data[1:]
            if payload == b"rdy":
                ready_event.set()

    device = await BleakScanner.find_device_by_name(HUB_NAME)

    if device is None:
        status_label.config(text="Hub Not Found", fg="red")
        return

    client = BleakClient(device, handle_disconnect)

    await client.connect()
    await client.start_notify(PYBRICKS_COMMAND_EVENT_CHAR_UUID, handle_rx)

    status_label.config(text="Connected", fg="green")

    # Keep alive
    while True:
        await asyncio.sleep(1)


async def send_speed(speed: int):
    global client

    if client is None or not client.is_connected:
        return

    await ready_event.wait()
    ready_event.clear()

    cmd = f"s{speed}"

    await client.write_gatt_char(
        PYBRICKS_COMMAND_EVENT_CHAR_UUID,
        b"\x06" + cmd.encode() + b"\n",
        response=True
    )


# ----------------------------
# TKINTER UI
# ----------------------------

def start_ble(status_label):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_main(status_label))


def on_slider_change(value):
    speed = int(float(value))
    asyncio.run_coroutine_threadsafe(send_speed(speed), loop)


def stop_motor():
    slider.set(0)
    asyncio.run_coroutine_threadsafe(send_speed(0), loop)


root = tk.Tk()
root.title("Train Controller")

status_label = tk.Label(root, text="Connecting...", font=("Arial", 14))
status_label.pack(pady=10)

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

# Start BLE in background thread
threading.Thread(target=start_ble, args=(status_label,), daemon=True).start()

root.mainloop()
