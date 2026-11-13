# SPDX-License-Identifier: MIT
# Pybricks communication example credits: see THIRD_PARTY_NOTICES.md.
"""Upload using Fleet Controller or Pybricks Code. City Hub motor on port A."""
from pybricks.pupdevices import DCMotor
from pybricks.parameters import Port, Color
from pybricks.hubs import CityHub
from pybricks.tools import wait, StopWatch
from usys import stdin
from uselect import poll

hub = CityHub()
motor = DCMotor(Port.A)
keyboard = poll()
keyboard.register(stdin)
watchdog = StopWatch()
battery_timer = StopWatch()
# Separate from the motor watchdog: telemetry must never keep a train armed.
BATTERY_REPORT_MS = 2000
light_mode = None


def show_light(mode):
    global light_mode
    if mode == light_mode:
        return  # Heartbeats must not restart the blink pattern.
    light_mode = mode
    if mode == 'waiting':
        hub.light.blink(Color.YELLOW, [250, 750])
    else:
        hub.light.on({'startup': Color.YELLOW, 'ready': Color.GREEN,
                      'error': Color.RED, 'exit': Color.WHITE}[mode])


def report_battery():
    try:
        voltage = hub.battery.voltage()
        current = hub.battery.current()
    except (OSError, ValueError):
        # Keep motor control working if a reading is temporarily unavailable.
        return
    print("battery", voltage, current)


command = ""
armed = False
timed_out = False
motor.stop()
show_light('startup')
print('program train-fleet-city/1')
print('features waiting-led')
print("rdy")
try:
    report_battery()
    while True:
        if watchdog.time() > 2000 and not timed_out:
            motor.stop()
            armed = False
            command = ""
            timed_out = True
            print("timeout")
            print('program train-fleet-city/1')
            print('features waiting-led')
            print("rdy")
            show_light('error')
        if keyboard.poll(0):
            char = stdin.read(1)
            if char == "\n":
                cmd = command.strip()
                command = ""
                if cmd == "bye":
                    break
                try:
                    if not cmd.startswith("s"):
                        raise ValueError()
                    waiting = cmd == 's0w1'
                    speed = 0 if waiting else int(cmd[1:])
                    if not -100 <= speed <= 100:
                        raise ValueError()
                    # A zero command must rearm after startup or a timeout.
                    if speed == 0:
                        armed = True
                    if armed:
                        motor.dc(speed)
                        watchdog.reset()
                        timed_out = False
                        show_light('waiting' if waiting else 'ready')
                except (ValueError, TypeError):
                    motor.stop()
                    armed = False
                    show_light('error')
                print("rdy")
            elif len(command) < 16:
                command += char
            else:
                command = ""
                motor.stop()
                armed = False
                show_light('error')
        if battery_timer.time() >= BATTERY_REPORT_MS:
            report_battery()
            battery_timer.reset()
        wait(5)
finally:
    motor.stop()
    show_light('exit')
