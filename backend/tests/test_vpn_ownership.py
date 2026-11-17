import unittest

from app.services.vpn_ownership import DeviceOwnershipError, find_owned_member_device


def member(member_id, username, devices):
    return {
        "id": member_id,
        "username": username,
        "wireguard": {"devices": devices},
    }


class VPNOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.device = {"device_id": "dev-a", "external_id": "provider-a", "provider": "wg-easy"}
        self.alice = member("m-alice", "alice", [self.device])

    def test_member_can_resolve_its_registered_device(self):
        self.assertIs(
            find_owned_member_device([self.alice], self.alice, "dev-a"),
            self.device,
        )

    def test_unregistered_provider_device_is_not_reachable_by_id(self):
        with self.assertRaises(LookupError):
            find_owned_member_device([self.alice], self.alice, "provider-other")

    def test_duplicate_cross_member_provider_mapping_is_denied(self):
        bob = member("m-bob", "bob", [{"device_id": "dev-b", "external_id": "provider-a", "provider": "wg-easy"}])
        with self.assertRaises(DeviceOwnershipError):
            find_owned_member_device([self.alice, bob], self.alice, "dev-a")

    def test_duplicate_mapping_inside_one_member_is_denied(self):
        duplicate = {"device_id": "dev-b", "external_id": "provider-a", "provider": "wg-easy"}
        alice = member("m-alice", "alice", [self.device, duplicate])
        with self.assertRaises(DeviceOwnershipError):
            find_owned_member_device([alice], alice, "dev-a")

    def test_missing_external_identity_is_denied(self):
        alice = member("m-alice", "alice", [{"device_id": "dev-a", "provider": "wg-easy"}])
        with self.assertRaises(DeviceOwnershipError):
            find_owned_member_device([alice], alice, "dev-a")


if __name__ == "__main__":
    unittest.main()
