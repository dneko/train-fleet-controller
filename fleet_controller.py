"""Run: python fleet_controller.py [--config fleet.json] [--demo]."""
import argparse
import asyncio
import copy
from pathlib import Path
import threading
import tkinter as tk

from fleet_model import load_config, is_external
from train_controllers import create_controller, connection_identity, controller_spec, supports
# Compatibility for existing integrations importing the original connection class.
from train_controllers.pybricks import PybricksController as TrainConnection, CHAR_UUID


class Backend:
    def __init__(self, config, events, demo):
        self.events, self.demo = events, demo
        self.loop = asyncio.new_event_loop()
        self.trains, self.tasks = {}, {}
        self.updates = set()
        self.hub_update = None
        self.recording_history = False
        self.config = config
        self.startup_error = None
        self.started = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.started.wait()
        if self.startup_error is not None:
            self.thread.join()
            raise self.startup_error

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.settings_lock = asyncio.Lock()
        try:
            self.trains = self.connections(self.config)
        except Exception as exc:
            self.startup_error = exc
            self.loop.close()
            return
        finally:
            self.started.set()
        self.loop.run_forever()
        self.loop.close()

    def connect(self, train_id):
        if train_id not in getattr(self, 'requested_trains', self.trains):
            return
        async def start():
            async with self.settings_lock:
                train = self.trains.get(train_id)
                if train is None or train.busy:
                    return
                task = self.tasks.get(train_id)
                if task is None or task.done():
                    self.tasks[train_id] = self.loop.create_task(train.run_reconnecting())
        if not self.demo:
            def schedule():
                task = self.loop.create_task(start())
                self.updates.add(task)
                task.add_done_callback(self.updates.discard)
            self.loop.call_soon_threadsafe(schedule)

    def upload_program(self, train_id, source):
        from train_controllers.pybricks_updates import upload_program
        return upload_program(self, train_id, source)

    async def upload_connection(self, connection, source, device=None):
        from train_controllers.pybricks_updates import upload_connection
        return await upload_connection(self, connection, source, device)

    def update_hubs(self, source):
        from train_controllers.pybricks_updates import update_hubs
        return update_hubs(self, source)

    def speed(self, train_id, value):
        if train_id not in self.trains:
            return
        if self.demo:
            self.events.put(("status", train_id, f"Demo speed: {value}"))
        else:
            def send():
                train = self.trains.get(train_id)
                if train is not None and (value == 0 or train_id not in getattr(self, 'changing_trains', set())):
                    train.set_speed(value)
            self.loop.call_soon_threadsafe(send)

    def waiting(self, train_id, waiting):
        if (not self.demo and train_id in self.trains
                and supports(self.trains[train_id].train, 'waiting_indicator')):
            self.loop.call_soon_threadsafe(self.trains[train_id].set_waiting, waiting)

    def record_history(self, enabled):
        def update():
            self.recording_history = bool(enabled)
            for connection in self.trains.values():
                connection.recording_history = self.recording_history and supports(connection.train, 'power_telemetry')
                if connection.recording_history:
                    connection.record_sent_throttle(connection.last_speed if connection.accepting else None)
        self.loop.call_soon_threadsafe(update)

    def connections(self, config):
        connections = {t['id']: create_controller(t, self.events.put)
                       for t in config['trains'] if not is_external(t)}
        for connection in connections.values():
            connection.recording_history = (getattr(self, 'recording_history', False)
                                            and supports(connection.train, 'power_telemetry'))
        return connections

    def update_trains(self, config):
        """Reconcile hardware sessions; unrelated writers continue uninterrupted."""
        config = copy.deepcopy(config)
        updates = {t['id']: t for t in config['trains'] if not is_external(t)}
        self.requested_trains = set(updates)
        async def update():
            removed = self.trains.keys() - updates.keys()
            changed = {tid for tid in self.trains.keys() & updates.keys()
                       if connection_identity(self.trains[tid].train) != connection_identity(updates[tid])}
            self.changing_trains = removed | changed
            active = {tid for tid in self.changing_trains
                      if tid in self.tasks and not self.tasks[tid].done()}
            try:
                for tid in self.changing_trains:
                    self.trains[tid].set_speed(0)
                if active:
                    await asyncio.sleep(.6)
                # Release all changed endpoints before reconnecting any of them.
                tasks = [self.tasks.pop(tid) for tid in self.changing_trains if tid in self.tasks]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for tid in removed:
                    self.trains.pop(tid)
                for tid, updated in updates.items():
                    connection = self.trains.get(tid)
                    if connection is None or controller_spec(connection.train).type_id != controller_spec(updated).type_id:
                        connection = self.connections({'trains': [updated]})[tid]
                        self.trains[tid] = connection
                    else:
                        connection.train = updated
                    if tid in active:
                        self.tasks[tid] = self.loop.create_task(connection.run_reconnecting())
                self.config = config
            finally:
                self.changing_trains = set()
        async def apply():
            async with self.settings_lock:
                await update()
        def schedule():
            task = self.loop.create_task(apply())
            self.updates.add(task)
            task.add_done_callback(self.updates.discard)
        self.loop.call_soon_threadsafe(schedule)

    async def shutdown(self):
        # Cancel writers before issuing the final zero commands.
        batch = getattr(self, 'hub_update', None)
        if batch is not None and not batch.done():
            batch.cancel()
            await asyncio.gather(batch, return_exceptions=True)
        updates = list(self.updates)
        for task in updates:
            task.cancel()
        await asyncio.gather(*updates, return_exceptions=True)
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        # Connections close on cancellation; no further motion commands can be sent.

    def reconfigure(self, config):
        self.requested_trains = {t['id'] for t in config['trains'] if not is_external(t)}
        async def apply():
            for train in self.trains.values():
                train.set_speed(0)
            await asyncio.sleep(1)
            await self.shutdown()
            self.config = config
            self.tasks = {}
            self.trains = self.connections(config)
            self.events.put(('configured', config))
        asyncio.run_coroutine_threadsafe(apply(), self.loop)

    def close(self):
        async def finish():
            # Stop requests use the existing writer, avoiding concurrent GATT writes.
            for train in self.trains.values():
                train.set_speed(0)
            await asyncio.sleep(1)
            await self.shutdown()
        future = asyncio.run_coroutine_threadsafe(finish(), self.loop)
        future.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=Path(__file__).with_name('fleet.json'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--demo', action='store_true')
    modes.add_argument('--motor-only', action='store_true', help='Manual motor control without map or sensors')
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    from fleet_ui import FleetApp
    root = tk.Tk()
    app = FleetApp(root, config, args.config, args.demo, Backend, motor_only=args.motor_only)
    root.mainloop()
    app.backend.thread.join(timeout=5)


if __name__ == '__main__':
    main()
