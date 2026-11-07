import unittest
from unittest.mock import Mock, patch

from containment_lab import verify_containment
from monitored_runner import inspect_container


def metadata(**overrides):
    value = {
        "available": True,
        "network_mode": "none",
        "readonly_rootfs": True,
        "user": "65534:65534",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "memory": 128 * 1024 * 1024,
        "nano_cpus": 500_000_000,
        "pids_limit": 64,
        "privileged": False,
        "image_id": "sha256:example",
        "image_reference": "python:3.12-slim",
    }
    value.update(overrides)
    return value


class ContainmentVerificationTests(unittest.TestCase):
    def test_all_controls_verified(self):
        report = verify_containment(metadata(), "run-1")
        self.assertEqual(report["overall_status"], "passed")
        self.assertEqual(report["verified_count"], 9)
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["unknown_count"], 0)

    def assert_failed(self, field, value):
        report = verify_containment(metadata(**{field: value}))
        self.assertEqual(report["overall_status"], "failed")
        self.assertEqual(report["failed_count"], 1)

    def test_network_isolation_failure(self):
        self.assert_failed("network_mode", "bridge")

    def test_writable_root_filesystem(self):
        self.assert_failed("readonly_rootfs", False)

    def test_root_execution(self):
        self.assert_failed("user", "0:0")

    def test_missing_capability_restrictions(self):
        self.assert_failed("cap_drop", [])

    def test_missing_no_new_privileges(self):
        self.assert_failed("security_opt", [])

    def test_excessive_memory_limit(self):
        self.assert_failed("memory", 128 * 1024 * 1024 + 1)

    def test_excessive_cpu_limit(self):
        self.assert_failed("nano_cpus", 500_000_001)

    def test_excessive_process_limit(self):
        self.assert_failed("pids_limit", 65)

    def test_privileged_container(self):
        self.assert_failed("privileged", True)

    def test_missing_metadata_is_unknown(self):
        report = verify_containment({"available": False}, "run-2")
        self.assertEqual(report["overall_status"], "failed")
        self.assertEqual(report["verified_count"], 0)
        self.assertEqual(report["unknown_count"], 9)

    def test_malformed_metadata_is_unknown(self):
        report = verify_containment("malformed", "run-3")
        self.assertEqual(report["unknown_count"], 9)
        self.assertEqual(report["failed_count"], 0)

    def test_unknown_values_are_never_verified(self):
        report = verify_containment(metadata(
            network_mode=None,
            readonly_rootfs="true",
            memory="128m",
        ))
        statuses = {
            control["control_id"]: control["status"]
            for control in report["controls"]
        }
        self.assertEqual(statuses["network_isolation"], "unknown")
        self.assertEqual(statuses["readonly_rootfs"], "failed")
        self.assertEqual(statuses["memory_limit"], "unknown")
        self.assertNotEqual(report["overall_status"], "passed")

    def test_limitations_state_trust_boundary(self):
        report = verify_containment(metadata())
        text = " ".join(report["limitations"])
        self.assertIn("does not prove an operation was blocked", text)
        self.assertIn("shares the host kernel", text)
        self.assertIn("not individually monitored", text)

    def test_runner_collects_only_approved_metadata(self):
        response = Mock(
            returncode=0,
            stdout=(
                '{"state":{"Status":"exited","Running":false,'
                '"OOMKilled":false,"ExitCode":0,"StartedAt":"start",'
                '"FinishedAt":"finish","Error":""},'
                '"network_mode":"none","readonly_rootfs":true,'
                '"user":"65534:65534","cap_drop":["ALL"],'
                '"security_opt":["no-new-privileges"],'
                '"memory":134217728,"nano_cpus":500000000,'
                '"pids_limit":64,"privileged":false,'
                '"image_id":"sha256:image","image_reference":"python:3.12-slim"}'
            ),
        )
        with patch("monitored_runner.subprocess.run", return_value=response) as run:
            result = inspect_container("dryrun-test", 1.0)

        self.assertEqual(result["network_mode"], "none")
        self.assertEqual(result["image_id"], "sha256:image")
        self.assertNotIn("environment", result)
        format_arg = run.call_args.args[0][3]
        self.assertNotIn("Env", format_arg)
        self.assertNotIn("Command", format_arg)


if __name__ == "__main__":
    unittest.main()
