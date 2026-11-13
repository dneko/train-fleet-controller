import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, call
from zeroconf import IPVersion
from reader_discovery import ReceiverDiscovery, lan_addresses, SERVICE_TYPE


class DiscoveryTests(unittest.TestCase):
    def run_discovery(self, snapshots, zeroconfs):
        # Drive network changes without wall-clock delays or background races.
        discovery = ReceiverDiscovery.__new__(ReceiverDiscovery)
        discovery.host, discovery.port = '0.0.0.0', 8765
        discovery.controller_id, discovery.report = 'train-fleet', MagicMock()
        discovery.stopped = MagicMock()
        discovery.stopped.is_set.side_effect = [False] * len(snapshots) + [True]
        with patch('zeroconf.Zeroconf', side_effect=zeroconfs) as factory, patch(
                'reader_discovery.lan_addresses', side_effect=snapshots):
            discovery.run()
        return factory, discovery.report

    def test_addresses_exclude_loopback_ipv6_and_respect_bind(self):
        adapter = SimpleNamespace(ips=[SimpleNamespace(ip=ip) for ip in
                                      ['127.0.0.1', '192.168.1.10', '10.0.0.2', ('::1', 0, 0)]])
        with patch('ifaddr.get_adapters', return_value=[adapter]):
            self.assertEqual(lan_addresses(), ['10.0.0.2', '192.168.1.10'])
            self.assertEqual(lan_addresses('192.168.1.10'), ['192.168.1.10'])
            self.assertEqual(lan_addresses('127.0.0.1'), [])

    def test_advertisement_has_endpoint_and_no_token_and_cleans_up(self):
        registered = threading.Event()
        zc = MagicMock()
        messages = []
        def report(message):
            messages.append(message)
            registered.set()
        with patch('zeroconf.Zeroconf', return_value=zc), patch(
                'reader_discovery.lan_addresses', return_value=['192.168.1.10']):
            discovery = ReceiverDiscovery('0.0.0.0', 8765, 'train-fleet', report)
            try:
                self.assertTrue(registered.wait(3))
                info = zc.register_service.call_args.args[0]
                self.assertEqual(info.type, SERVICE_TYPE)
                self.assertEqual(info.port, 8765)
                self.assertEqual(info.parsed_addresses(), ['192.168.1.10'])
                self.assertEqual(info.properties, {b'controller': b'train-fleet', b'version': b'1', b'path': b'/events'})
            finally:
                discovery.close()
            zc.unregister_service.assert_called_once_with(info)
            zc.close.assert_called_once()

    def test_discovery_failure_is_reported_and_resources_closed(self):
        reported = threading.Event()
        messages = []
        zc = MagicMock()
        zc.register_service.side_effect = OSError('multicast unavailable')
        def report(message):
            messages.append(message)
            reported.set()
        with patch('zeroconf.Zeroconf', return_value=zc), patch(
                'reader_discovery.lan_addresses', return_value=['192.168.1.10']):
            discovery = ReceiverDiscovery('0.0.0.0', 8765, 'train-fleet', report)
            self.assertTrue(reported.wait(3))
            discovery.close()
        self.assertIn('Manual SERVER_URL still works', messages[0])
        zc.close.assert_called_once()

    def test_wifi_and_ethernet_advertise_only_their_own_addresses(self):
        wifi, ethernet = MagicMock(), MagicMock()
        addresses = ['172.18.156.53', '192.168.0.196']
        factory, report = self.run_discovery([addresses], [wifi, ethernet])
        self.assertEqual(factory.call_args_list, [
            call(interfaces=[address], ip_version=IPVersion.V4Only) for address in addresses])
        infos = []
        for address, zc in zip(addresses, (wifi, ethernet)):
            info = zc.register_service.call_args.args[0]
            self.assertEqual(info.parsed_addresses(), [address])
            zc.register_service.assert_called_once_with(info, cooperating_responders=True)
            zc.unregister_service.assert_called_once_with(info)
            zc.close.assert_called_once()
            infos.append(info)
        # A sensor seeing both interfaces still discovers one controller.
        self.assertEqual(infos[0].name, infos[1].name)
        self.assertEqual(infos[0].server, infos[1].server)
        self.assertIn('http://192.168.0.196:8765', report.call_args.args[0])

    def test_network_changes_keep_existing_interface_and_refresh_new_one(self):
        wifi, ethernet, changed = MagicMock(), MagicMock(), MagicMock()
        factory, report = self.run_discovery([
            [],
            ['172.18.156.53'],
            ['172.18.156.53', '192.168.0.196'],
            ['172.18.156.53', '192.168.0.197'],
            ['172.18.156.53', '192.168.0.197'],
            [],
        ], [wifi, ethernet, changed])
        self.assertEqual(factory.call_count, 3)
        for zc in (wifi, ethernet, changed):
            zc.register_service.assert_called_once()
            zc.unregister_service.assert_called_once()
            zc.close.assert_called_once()
        self.assertIn('waiting for a LAN address', report.call_args.args[0])

    def test_one_failed_interface_does_not_disable_ethernet_and_is_retried(self):
        wifi, ethernet, retry = MagicMock(), MagicMock(), MagicMock()
        wifi.register_service.side_effect = OSError('multicast unavailable')
        addresses = ['172.18.156.53', '192.168.0.196']
        factory, report = self.run_discovery([addresses, addresses], [wifi, ethernet, retry])
        self.assertEqual(factory.call_count, 3)
        wifi.close.assert_called_once()
        ethernet.register_service.assert_called_once()
        retry.register_service.assert_called_once()
        self.assertIn('unavailable on 172.18.156.53', report.call_args_list[0].args[0])
        self.assertIn('http://192.168.0.196:8765', report.call_args_list[1].args[0])

    def test_cleanup_failure_still_closes_both_interfaces(self):
        wifi, ethernet = MagicMock(), MagicMock()
        wifi.unregister_service.side_effect = OSError('interface gone')
        self.run_discovery([['172.18.156.53', '192.168.0.196']], [wifi, ethernet])
        wifi.close.assert_called_once()
        ethernet.unregister_service.assert_called_once()
        ethernet.close.assert_called_once()

    def test_disconnected_interface_cleanup_failure_keeps_other_network_running(self):
        wifi, ethernet = MagicMock(), MagicMock()
        wifi.unregister_service.side_effect = OSError('interface gone')
        _, report = self.run_discovery([
            ['172.18.156.53', '192.168.0.196'], ['192.168.0.196']], [wifi, ethernet])
        wifi.close.assert_called_once()
        ethernet.register_service.assert_called_once()
        self.assertEqual(report.call_args.args[0],
                         'Automatic reader discovery ready (train-fleet): http://192.168.0.196:8765')
