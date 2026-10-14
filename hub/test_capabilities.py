import unittest

from hub.capabilities import describe_capabilities, normalize_capabilities


class CapabilityRegistryTest(unittest.TestCase):
    def test_core_capabilities_stay_enabled_when_stored_value_is_false(self):
        normalized = normalize_capabilities({"search": False})
        self.assertTrue(normalized["search"])
        self.assertTrue(describe_capabilities({"search": False})["search"]["enabled"])

    def test_available_non_core_capabilities_can_still_be_disabled(self):
        normalized = normalize_capabilities({"media": False})
        self.assertFalse(normalized["media"])


if __name__ == "__main__":
    unittest.main()
