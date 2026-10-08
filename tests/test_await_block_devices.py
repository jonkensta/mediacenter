"""Exercise DAS discovery without udev, hardware, or real sleeps."""

import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "await-block-devices"
loader = importlib.machinery.SourceFileLoader("await_block_devices", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
devices = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"pyudev": types.ModuleType("pyudev")}):
    loader.exec_module(devices)


class AwaitBlockDevicesTests(unittest.TestCase):
    def test_all_devices_present_returns_without_sleeping(self):
        with patch.object(devices, "present_uuids", return_value={"a", "b"}), \
                patch.object(devices.time, "sleep") as sleep:
            devices.await_block_devices(["a", "b"])
        sleep.assert_not_called()

    def test_devices_seen_in_different_polls_are_not_ready(self):
        with patch.object(devices, "present_uuids", side_effect=[{"a"}, {"b"}, {"b"}]), \
                patch.object(devices.time, "monotonic", side_effect=[0, 0, 5, 10]), \
                patch.object(devices.time, "sleep"):
            with self.assertRaisesRegex(devices.DeviceTimeoutError, "device\\(s\\): a$"):
                devices.await_block_devices(["a", "b"], timeout=10)

    def test_waits_for_disappeared_device_to_return(self):
        with patch.object(devices, "present_uuids", side_effect=[{"a"}, {"b"}, {"a", "b"}]) as present, \
                patch.object(devices.time, "monotonic", side_effect=[0, 0, 5]), \
                patch.object(devices.time, "sleep"):
            devices.await_block_devices(["a", "b"], timeout=10)
        self.assertEqual(present.call_count, 3)

    def test_sleep_is_capped_by_remaining_timeout(self):
        with patch.object(devices, "present_uuids", return_value=set()), \
                patch.object(devices.time, "monotonic", side_effect=[0, 0, 2]), \
                patch.object(devices.time, "sleep") as sleep:
            with self.assertRaises(devices.DeviceTimeoutError):
                devices.await_block_devices(["a"], poll_interval=5, timeout=2)
        sleep.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
