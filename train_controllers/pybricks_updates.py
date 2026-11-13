"""City Hub program operations, isolated from generic motor dispatch."""
import asyncio
import traceback
from hub_discovery import scan_hubs
from .registry import supports


def upload_program(backend, train_id, source):
    if backend.demo:
        raise ValueError('Program upload is available in Live or Motor only mode')
    if train_id not in backend.trains or not supports(backend.trains[train_id].train, 'city_hub_program'):
        raise ValueError('Select an active Pybricks City Hub train')

    async def replace(connection, previous):
        try:
            # The old writer sends zero before relinquishing its connection.
            if previous is not None and not previous.done():
                connection.upload_status('Stopping and disconnecting the existing Bluetooth session…')
                await asyncio.sleep(.6)
                previous.cancel()
                await asyncio.gather(previous, return_exceptions=True)
            # A previous upload's control-session cleanup may reset these.
            connection.uploading = True
            connection.upload_finished.clear()
            await backend.upload_connection(connection, source)
        except asyncio.CancelledError:
            if connection.uploading:
                connection.upload_status('Upload cancelled. Train remains stopped; retry the upload when ready.', 'failed')
            raise
        finally:
            if previous is not None and not previous.done():
                previous.cancel()
                await asyncio.gather(previous, return_exceptions=True)
            connection.uploading = False

    def start():
        connection = backend.trains.get(train_id)
        if connection is None or not supports(connection.train, 'city_hub_program') or connection.busy:
            return
        if backend.updates:
            connection.upload_status('Train settings are still being applied. Retry the upload in a moment.', 'failed')
            # No connection was replaced: restore the unchanged session's readiness.
            backend.events.put(('ready', train_id, connection.accepting))
            return
        connection.set_speed(0)
        connection.uploading = True
        connection.upload_finished.clear()
        previous = backend.tasks.get(train_id)
        backend.tasks[train_id] = backend.loop.create_task(replace(connection, previous))
    backend.loop.call_soon_threadsafe(start)

async def upload_connection(backend, connection, source, device=None):
    try:
        if await connection.run(source=source, device=device):
            # A successful upload retains its writer for motor control. Later
            # disconnects retry control only, never the program replacement.
            await connection.run_reconnecting()
    except asyncio.CancelledError:
        if connection.uploading:
            connection.upload_status('Upload cancelled. Train remains stopped; retry when ready.', 'failed')
        raise
    except Exception as exc:
        connection.debug(traceback.format_exc())
        connection.upload_status(f'Upload failed: {type(exc).__name__}: {exc or "operation timed out"}. '
                                 'Train remains stopped.', 'failed')
    finally:
        connection.uploading = False
        connection.upload_finished.set()

def update_hubs(backend, source):
    """Scan the active fleet once, then connect/upload each matching hub."""
    if backend.demo:
        raise ValueError('Update hubs in Live or Motor only mode')
    if not any(supports(c.train, 'city_hub_program') for c in backend.trains.values()):
        raise ValueError('Add an active Pybricks City Hub train first')

    def debug(text):
        backend.events.put(('program_debug', None, text))

    async def update(connections, previous):
        try:
            debug('Update Hubs: stopping active Pybricks City Hub trains before scanning.')
            if previous:
                await asyncio.sleep(.6)
                for task in previous:
                    task.cancel()
                await asyncio.gather(*previous, return_exceptions=True)
            for connection in connections:
                connection.uploading = True
                connection.upload_finished.clear()
                connection.upload_status(f"Scanning active list for {connection.train['hub_name']!r}…")
            found, errors = await scan_hubs([c.train for c in connections], debug)
            for connection in connections:
                tid = connection.train['id']
                if tid in errors:
                    connection.upload_status(errors[tid], 'failed')
                    connection.uploading = False
                    continue
                backend.tasks[tid] = backend.loop.create_task(backend.upload_connection(connection, source, found[tid]))
                # Successful sessions remain connected, so wait for the upload
                # result rather than waiting for the motor writer to exit.
                await connection.upload_finished.wait()
            complete = sum(c.upload_phase == 'complete' for c in connections)
            debug(f'Update Hubs finished: {complete} succeeded; {len(connections)-complete} failed. '
                  'All target trains remain stopped.')
        except asyncio.CancelledError:
            debug('Update Hubs cancelled.')
            raise
        except Exception as exc:
            debug(traceback.format_exc())
            for connection in connections:
                if connection.uploading:
                    connection.upload_status(f'Update failed: {type(exc).__name__}: {exc}', 'failed')
                    connection.uploading = False
        finally:
            for task in previous:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*previous, return_exceptions=True)
            for connection in connections:
                if connection.uploading:
                    connection.upload_status('Update ended before completion. Train remains stopped.', 'failed')
                    connection.uploading = False

    def start():
        connections = [c for c in backend.trains.values() if supports(c.train, 'city_hub_program')]
        if backend.updates or any(c.uploading for c in connections):
            for connection in connections:
                if not connection.uploading:
                    connection.upload_status('Wait for the current settings change or upload, then retry.', 'failed')
                    backend.events.put(('ready', connection.train['id'], connection.accepting))
            return
        previous = [backend.tasks[c.train['id']] for c in connections
                    if c.train['id'] in backend.tasks and not backend.tasks[c.train['id']].done()]
        for connection in connections:
            connection.set_speed(0)
            connection.uploading = True
            connection.upload_finished.clear()
            connection.upload_phase = 'progress'
        backend.hub_update = backend.loop.create_task(update(connections, previous))
    backend.loop.call_soon_threadsafe(start)

