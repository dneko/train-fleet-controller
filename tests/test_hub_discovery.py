"""Exercise advertising-name discovery without a Bluetooth adapter."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hub_discovery import scan_hubs


class HubDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def scan(self, advertisements, trains=None):
        class Scanner:
            def __init__(self, detection_callback):
                self.callback = detection_callback

            async def __aenter__(self):
                for address, cached, local in advertisements:
                    self.callback(SimpleNamespace(address=address, name=cached),
                                  SimpleNamespace(local_name=local, rssi=-42, service_uuids=['test-service']))
                return self

            async def __aexit__(self, *args):
                pass

        self.messages = []
        with patch.dict(sys.modules, {'bleak': SimpleNamespace(BleakScanner=Scanner)}):
            return await scan_hubs(trains or [dict(id='a', hub_name='Train A')], self.messages.append, timeout=0)

    async def test_late_local_name_overrides_cached_name_and_ignores_unlisted_hubs(self):
        found, errors = await self.scan([
            ('one', 'Old name', None), ('one', 'Old name', 'Train A'), ('one', 'Old name', None),
            ('two', 'Saved inactive train', 'Saved inactive train'), ('three', None, None),
        ])
        self.assertEqual(set(found), {'a'})
        self.assertEqual(found['a'].address, 'one')
        self.assertFalse(errors)
        self.assertIn('RSSI=-42', '\n'.join(self.messages))
        self.assertIn('ignored (not an active target)', '\n'.join(self.messages))

    async def test_duplicate_names_refuse_to_choose_a_hub(self):
        found, errors = await self.scan([('one', None, 'Train A'), ('two', None, 'Train A')])
        self.assertFalse(found)
        self.assertIn('Multiple hubs', errors['a'])
        self.assertIn('one, two', errors['a'])

    async def test_names_match_exactly_and_missing_target_is_actionable(self):
        found, errors = await self.scan([('one', None, 'train a'), ('two', None, 'Train A extra')])
        self.assertFalse(found)
        self.assertIn("'Train A' not found", errors['a'])
        self.assertIn('Power it on', errors['a'])

    async def test_repeated_advertisement_is_one_match(self):
        found, errors = await self.scan([('one', 'Train A', None)] * 3)
        self.assertEqual(found['a'].address, 'one')
        self.assertFalse(errors)
        self.assertEqual(sum(line.startswith('Found ') for line in self.messages), 1)
