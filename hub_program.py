"""The bundled City Hub program and its Pybricks BLE download protocol.

Protocol: https://github.com/pybricks/technical-info/blob/master/pybricks-ble-profile.md
MPY container: https://github.com/pybricks/pybricksdev/blob/master/pybricksdev/compile.py
Only the bundled single-module program is supported; all its imports are hub builtins.
"""
import asyncio
import hashlib
from pathlib import Path
import re
import struct
import subprocess

PROGRAM_PATH = Path(__file__).with_name('firmware') / 'city_hub.py'
PROGRAM_ID = 'train-fleet-city/1'
COMMAND_UUID = 'c5f50002-8280-46da-89f4-6d8051e4aeef'
CAPABILITIES_UUID = 'c5f50003-8280-46da-89f4-6d8051e4aeef'
PROTOCOL_UUID = '00002a28-0000-1000-8000-00805f9b34fb'
PNP_UUID = '00002a50-0000-1000-8000-00805f9b34fb'


def read_program():
    return PROGRAM_PATH.read_text(encoding='utf-8')


def source_revision(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]


def compiler_available():
    try:
        import mpy_cross_v6
        return Path(mpy_cross_v6.MPY_CROSS_PATH).is_file()
    except ImportError:
        return False


def compile_program(source):
    try:
        from mpy_cross_v6 import MPY_CROSS_PATH
    except ImportError as exc:
        raise RuntimeError('Upload compiler is missing. Install requirements.txt, then reopen the controller.') from exc
    process = subprocess.run([MPY_CROSS_PATH, '-', '-s', 'city_hub.py'],
                             input=source.encode('utf-8'), capture_output=True, timeout=20)
    if process.returncode:
        raise ValueError('Cannot compile City Hub program: ' + process.stderr.decode('utf-8', errors='replace').strip())
    mpy = process.stdout
    if len(mpy) < 4 or mpy[:2] != b'M\x06' or mpy[2] >> 2:
        raise ValueError('Compiler did not produce supported MicroPython 6 bytecode')
    return struct.pack('<I', len(mpy)) + b'__main__\0' + mpy


async def write_command(client, data):
    await asyncio.wait_for(client.write_gatt_char(COMMAND_UUID, data, response=True), 5)


async def wait_for_reply(connection, seconds, message):
    try:
        await asyncio.wait_for(connection.ready.wait(), seconds)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(connection.hub_error or message) from exc


async def upload_program(client, connection, program):
    """Called only by the sole hub writer, before its motor-command loop starts."""
    async def read(uuid, label):
        connection.debug(f'Reading {label} ({uuid}; 5s timeout)…')
        value = bytes(await asyncio.wait_for(client.read_gatt_char(uuid), 5))
        connection.debug(f'{label}: {value!r} / hex={value.hex()}')
        return value

    async def write(data, label):
        connection.debug(f'{label}: command=0x{data[0]:02x}; {len(data)} bytes; 5s timeout')
        try:
            await write_command(client, data)
        except Exception as exc:
            connection.debug(f'{label} failed: {type(exc).__name__}: {exc}')
            raise

    connection.upload_status('Checking City Hub and Pybricks compatibility…')
    raw_version = (await read(PROTOCOL_UUID, 'Pybricks protocol version')).decode('ascii')
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-+].*)?', raw_version)
    if not match or not (1, 3, 0) <= tuple(map(int, match.groups())) < (2, 0, 0):
        raise ValueError('This hub needs newer supported Pybricks firmware. Install it using Pybricks Code first.')
    pnp = await read(PNP_UUID, 'hub identity')
    if len(pnp) != 7:
        raise ValueError('Cannot verify the hub type')
    _, vendor, product, _ = struct.unpack('<BHHH', pnp)
    if vendor != 0x0397 or product != 0x41:
        raise ValueError('This program requires a LEGO City Hub with its motor on port A')
    connection.debug(f'Hub identity: vendor=0x{vendor:04x}; product=0x{product:04x} (City Hub)')
    capabilities = await read(CAPABILITIES_UUID, 'hub capabilities')
    if len(capabilities) not in (10, 11):
        raise ValueError('Unsupported Pybricks hub capabilities')
    max_write, flags, max_program = struct.unpack('<HII', capabilities[:10])
    slots = capabilities[10] if len(capabilities) == 11 else 0
    if not flags & ((1 << 1) | (1 << 2) | (1 << 5)):
        raise ValueError('Hub does not support MicroPython 6 programs; update Pybricks firmware first')
    payload_size = min(max_write, client.mtu_size - 3, 512) - 5
    connection.debug(f'Transfer limits: max write={max_write}; MTU={client.mtu_size}; '
                     f'payload={payload_size}; features=0x{flags:08x}; slots={slots}; '
                     f'program={len(program)} bytes; capacity={max_program} bytes')
    if payload_size <= 0 or not program or len(program) > max_program:
        raise ValueError('Program does not fit the hub memory or Bluetooth transfer limits')

    connection.upload_status('Stopping the hub program…')
    connection.program_touched = True
    await write(b'\x00', 'Stop program (an empty hub needs no running train program)')
    try:
        await asyncio.wait_for(connection.program_stopped.wait(), 5)
    except asyncio.TimeoutError as exc:
        raise RuntimeError('No stopped-status notification from hub within 5s') from exc
    connection.debug('Hub confirms no program is running; ready to download.')
    # Clearing size before writing prevents a partial download from being runnable.
    await write(b'\x03' + struct.pack('<I', 0), 'Clear saved program size')
    for offset in range(0, len(program), payload_size):
        chunk = program[offset:offset+payload_size]
        await write(b'\x04' + struct.pack('<I', offset) + chunk,
                    f'Write chunk offset={offset} length={len(chunk)}')
        connection.upload_status(f'Uploading City Hub program… {min(100, (offset+len(chunk))*100//len(program))}%')
    await write(b'\x03' + struct.pack('<I', len(program)), 'Commit complete program size')
    connection.ready.clear()
    connection.rx = b''
    connection.rx_discarding = False
    connection.hub_error = None
    connection.program_id = None
    connection.stop_pending = False
    connection.upload_status('Starting program and checking zero throttle…')
    await write(b'\x01\x00' if slots else b'\x01', 'Start uploaded program')
    connection.debug(f'Waiting up to 8s for program identity {PROGRAM_ID!r} and startup rdy…')
    await wait_for_reply(connection, 8, 'No startup reply from the uploaded program; check motor port A and the activity log')
    if connection.program_id != PROGRAM_ID or connection.hub_error:
        raise RuntimeError('The expected City Hub program did not start; check motor port A and the activity log')
    connection.ready.clear()
    await write(b'\x06s0\n', 'Command zero throttle; waiting up to 5s for rdy')
    await wait_for_reply(connection, 5, 'No zero-throttle acknowledgment from the hub; check the activity log')
    if connection.stop_pending or connection.hub_error or not client.is_connected:
        raise RuntimeError('The hub did not confirm a healthy zero-throttle startup')
    connection.debug('Verified program identity, startup reply, and zero-throttle acknowledgment.')
