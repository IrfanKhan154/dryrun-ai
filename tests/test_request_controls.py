import unittest
from types import SimpleNamespace
from unittest.mock import patch

from request_controls import allow_request, client_ip, reset_rate_limits


class RequestControlTests(unittest.TestCase):
    def setUp(self):
        reset_rate_limits()

    def test_forwarded_ip_is_trusted_only_from_local_proxy(self):
        forwarded = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-forwarded-for": "198.51.100.10"},
        )
        spoofed_list = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"x-forwarded-for": "198.51.100.10, 127.0.0.1"},
        )
        direct = SimpleNamespace(
            client=SimpleNamespace(host="198.51.100.20"),
            headers={"x-forwarded-for": "198.51.100.10"},
        )
        self.assertEqual(client_ip(forwarded), "198.51.100.10")
        self.assertEqual(client_ip(spoofed_list), "127.0.0.1")
        self.assertEqual(client_ip(direct), "198.51.100.20")

    def test_rate_limit_blocks_after_configured_window_quota(self):
        with patch("request_controls.RATE_LIMIT_MAX_REQUESTS", 2):
            self.assertTrue(allow_request("198.51.100.30"))
            self.assertTrue(allow_request("198.51.100.30"))
            self.assertFalse(allow_request("198.51.100.30"))
            self.assertTrue(allow_request("198.51.100.31"))


if __name__ == "__main__":
    unittest.main()
