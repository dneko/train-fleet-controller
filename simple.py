# SPDX-License-Identifier: MIT
# Copyright (c) 2020 Henrik Blidh
# Copyright (c) 2022-2023 The Pybricks Authors

"""
Computer-to-hub communication example
Adjusted to send speed commands like: s50, s0, s100
"""

import asyncio
from contextlib import suppress
from bleak import BleakScanner, BleakClient

PYBRICKS_COMMAND_EVENT_CHAR_UUID = "c5f50002-8280-46da-89f4-6d8051e4aeef"

HUB_NAME = "Train A"


async def main():
    main_task = asyncio.current_task()

    def handle_disconnect(_):
        print("Hub was disconnected.")
        if not main_task.done():
            main_task.cancel()

    ready_event = asyncio.Event()

    def handle_rx(_, data: bytearray):
        if data[0] == 0x01:  # stdout event
            payload = data[1:]

            if payload == b"rdy":
                ready_event.set()
            else:
                print("Hub says:", payload.decode(errors="ignore"))

    device = await BleakScanner.find_device_by_name(HUB_NAME)

    if device is None:
        print(f"Could not find hub with name: {HUB_NAME}")
        return

    async with BleakClient(device, handle_disconnect) as client:

        await client.start_notify(PYBRICKS_COMMAND_EVENT_CHAR_UUID, handle_rx)

        async def send(command: str):
            await ready_event.wait()
            ready_event.clear()

            await client.write_gatt_char(
                PYBRICKS_COMMAND_EVENT_CHAR_UUID,
                b"\x06" + command.encode() + b"\n",  # IMPORTANT: newline
                response=True
            )

        print("Start the program on the hub now with the button.")

        # Example speed sequence
        await send("s50")
        await asyncio.sleep(2)

        await send("s100")
        await asyncio.sleep(2)

        await send("s30")
        await asyncio.sleep(2)

        await send("s0")   # stop
        await asyncio.sleep(1)

        print("Done.")

    print("Disconnected.")


if __name__ == "__main__":
    with suppress(asyncio.CancelledError):
        asyncio.run(main())
