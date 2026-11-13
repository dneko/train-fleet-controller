import asyncio
import tkinter as tk
from bleak import BleakClient, BleakScanner
import threading

# You may need to change this to your hub's name
HUB_NAME = "Train A"

client = None
loop = asyncio.new_event_loop()

# Replace with correct Pybricks UART characteristic UUID
UART_TX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

async def connect():
    global client
    devices = await BleakScanner.discover()

    for d in devices:
        if HUB_NAME in d.name:
            client = BleakClient(d.address)
            await client.connect()
            print("Connected!")
            return

    print("Hub not found.")

async def send_speed(value):
    if client and client.is_connected:
        data = bytes([int(value) & 0xFF])
        await client.write_gatt_char(UART_TX_UUID, data)

def start_async_loop():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(connect())
    loop.run_forever()

def update_speed(val):
    asyncio.run_coroutine_threadsafe(send_speed(val), loop)

# Start BLE thread
threading.Thread(target=start_async_loop, daemon=True).start()

# TKINTER UI
root = tk.Tk()
root.title("Train Speed Controller")

slider = tk.Scale(root,
                  from_=-100,
                  to=100,
                  orient="horizontal",
                  length=400,
                  label="Speed",
                  command=update_speed)
slider.pack(pady=20)

root.mainloop()
