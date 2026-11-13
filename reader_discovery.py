"""Advertise the running RFID receiver using local-network mDNS (Bonjour)."""
import ipaddress
import threading
import uuid

SERVICE_TYPE = '_trainrfid._tcp.local.'


def lan_addresses(host='0.0.0.0'):
    import ifaddr
    addresses = set()
    for adapter in ifaddr.get_adapters():
        for entry in adapter.ips:
            if not isinstance(entry.ip, str):
                continue  # IPv6
            address = ipaddress.ip_address(entry.ip)
            if not address.is_loopback and not address.is_unspecified:
                if host == '0.0.0.0' or host == str(address):
                    addresses.add(str(address))
    return sorted(addresses)


class ReceiverDiscovery:
    def __init__(self, host, port, controller_id, report):
        self.host, self.port = host, port
        self.controller_id, self.report = controller_id, report
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=5)

    def run(self):
        advertisements = {}

        def remove(address):
            zc, info = advertisements.pop(address)
            try:
                zc.unregister_service(info)
            finally:
                zc.close()

        try:
            from zeroconf import IPVersion, ServiceInfo, Zeroconf
            ident = 'train-controller-' + uuid.uuid4().hex[:12]
            previous = None
            while not self.stopped.is_set():
                addresses = lan_addresses(self.host)
                for address in list(advertisements):
                    if address not in addresses:
                        try:
                            remove(address)
                        except Exception as exc:
                            self.report(f'Automatic discovery cleanup failed on {address}: {exc}')
                for address in addresses:
                    if address in advertisements:
                        continue
                    zc = None
                    try:
                        # A reader may use only the first advertised IPv4 address.
                        # Keep the Internet Wi-Fi address off the sensor Ethernet LAN.
                        zc = Zeroconf(interfaces=[address], ip_version=IPVersion.V4Only)
                        candidate = ServiceInfo(
                            SERVICE_TYPE, ident + '.' + SERVICE_TYPE,
                            parsed_addresses=[address], port=self.port,
                            properties={'controller': self.controller_id, 'version': '1', 'path': '/events'},
                            server=ident + '.local.')
                        # Both adapters can also be on the same LAN. They represent
                        # one controller, so retain one service name on every link.
                        zc.register_service(candidate, cooperating_responders=True)
                        advertisements[address] = (zc, candidate)
                    except Exception as exc:
                        if zc is not None:
                            zc.close()
                        self.report(f'Automatic discovery unavailable on {address}: {exc}. Retrying. Manual SERVER_URL still works.')
                ready = sorted(advertisements)
                if ready != previous:
                    if ready:
                        endpoints = ', '.join(f'http://{address}:{self.port}' for address in ready)
                        self.report(f'Automatic reader discovery ready ({self.controller_id}): {endpoints}')
                    else:
                        self.report('Automatic discovery waiting for a LAN address')
                    previous = ready
                self.stopped.wait(5)
        except Exception as exc:
            self.report(f'Automatic discovery unavailable: {exc}. Manual SERVER_URL still works.')
        finally:
            for address in list(advertisements):
                try:
                    remove(address)
                except Exception as exc:
                    self.report(f'Automatic discovery cleanup failed on {address}: {exc}')
