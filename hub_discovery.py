"""Discover active train hubs by their advertised Bluetooth names."""
import asyncio


async def scan_hubs(trains, debug, timeout=10):
    from bleak import BleakScanner

    targets = {train['hub_name']: train['id'] for train in trains}
    debug(f'Scanning Bluetooth for {timeout}s. Exact target names: {list(targets)!r}')
    devices, reported = {}, set()

    def discovered(device, advertisement):
        # The local name can arrive in a later scan response. Prefer it to the
        # OS-cached device name, which may still contain an old hub name.
        previous = devices.get(device.address)
        name = advertisement.local_name or (previous[1] if previous else device.name)
        devices[device.address] = (device, name)
        key = (device.address, name)
        if name and key not in reported:
            reported.add(key)
            match = 'MATCH' if name in targets else 'ignored (not an active target)'
            debug(f'Found {name!r} at {device.address}; RSSI={advertisement.rssi} dBm; '
                  f'services={advertisement.service_uuids!r}; {match}')

    async with BleakScanner(detection_callback=discovered):
        await asyncio.sleep(timeout)

    found, errors = {}, {}
    for name, tid in targets.items():
        matches = [device for device, seen_name in devices.values() if seen_name == name]
        if len(matches) == 1:
            found[tid] = matches[0]
            debug(f'Matched active train {tid!r}: {name!r} at {matches[0].address}')
        elif matches:
            errors[tid] = (f'Multiple hubs advertise {name!r}: '
                           f'{", ".join(device.address for device in matches)}. '
                           'Give each hub a unique name before updating.')
        else:
            errors[tid] = (f'Hub {name!r} not found after {timeout}s. Power it on, disconnect '
                           'Pybricks Code/other controllers, and check the exact active-train hub name.')
        if tid in errors:
            debug(errors[tid])
    debug(f'Scan finished: {len(devices)} Bluetooth devices; '
          f'{len(found)}/{len(targets)} active hub names matched.')
    return found, errors
