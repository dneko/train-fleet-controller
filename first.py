import asyncio
import threading
import tkinter as tk
from bleak import BleakScanner, BleakClient

# Pybricks command characteristic
PYBRICKS_COMMAND_EVENT_CHAR_UUID = "c5f50002-8280-46da-89f4-6d8051e4aeef"

# Must exactly match hub name
HUB_NAME = "Train A"


class TrainController:

    def __init__(self):
        self.client = None
        self.loop = None
        self.connected = False

    async def connect(self):
        print("Scanning for hub...")
        device = await BleakScanner.find_device_by_name(HUB_NAME)

        if device is None:
            print("❌ Hub not found.")
            return

        print("Connecting...")
        self.client = BleakClient(device)
        await self.client.connect()

        self.connected = True
        print("✅ Connected to hub.")

    async def send_speed(self, speed):
        if not self.connected:
            print("Not connected yet.")
            return

        # Hub uses input(), so we must send newline
        message = f"s{speed}\n".encode()

        # Pybricks requires 0x06 prefix
        packet = bytearray([0x06]) + message

        print("Sending:", packet)

        await self.client.write_gatt_char(
            PYBRICKS_COMMAND_EVENT_CHAR_UUID,
            packet,
            response=False
        )

    def send_speed_threadsafe(self, speed):
        if self.loop:
            asyncio.run_coroutine_threadsafe(
                self.send_speed(speed),
                self.loop
            )


def start_async_loop(controller):
    loop = asyncio.new_event_loop()
    controller.loop = loop
    asyncio.set_event_loop(loop)

    loop.run_until_complete(controller.connect())
    loop.run_forever()


# ----------------------------
# Start BLE background thread
# ----------------------------

controller = TrainController()

threading.Thread(
    target=start_async_loop,
    args=(controller,),
    daemon=True
).start()


# ----------------------------
# Tkinter GUI (main thread)
# ----------------------------

root = tk.Tk()
root.title("Train Speed Control")

status_label = tk.Label(root, text="Move slider to control speed")
status_label.pack(pady=5)


def slider_changed(value):
    speed = int(float(value))
    status_label.config(text=f"Speed: {speed}")
    controller.send_speed_threadsafe(speed)


slider = tk.Scale(
    root,
    from_=-100,
    to=100,
    orient=tk.HORIZONTAL,
    length=400,
    label="Speed",
    command=slider_changed
)

slider.pack(padx=20, pady=20)
slider.set(0)

root.mainloop()
