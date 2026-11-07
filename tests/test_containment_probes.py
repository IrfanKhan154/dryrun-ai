import unittest
from unittest.mock import patch
import time

import containment_probes
from containment_probes import run_containment_probes


def sandbox_result(exit_code=0, status="success", oom=False, cleanup="confirmed"):
    return {
        "status": status,
        "exit_code": exit_code,
        "cleanup": cleanup,
        "execution_duration_seconds": 1.25,
        "host_metadata": {"available": True, "oom_killed": oom},
    }


def weakened_sandbox_result(
    *,
    exit_code=0,
    status="success",
    network_mode="bridge",
    pids_limit=1024,
):
    return {
        "status": status,
        "exit_code": exit_code,
        "cleanup": "confirmed",
        "execution_duration_seconds": 1.25,
        "host_metadata": {
            "available": True,
            "oom_killed": False,
            "network_mode": network_mode,
            "pids_limit": pids_limit,
        },
    }


class ContainmentProbeTests(unittest.TestCase):
    def setUp(self):
        containment_probes._probe_cache = None

    def test_runs_all_probes_and_returns_host_facts_only(self):
        responses = [
            sandbox_result(),
            sandbox_result(exit_code=1),
            sandbox_result(exit_code=1),
            sandbox_result(),
            sandbox_result(status="timeout", exit_code=None),
            sandbox_result(exit_code=137, oom=True),
            sandbox_result(exit_code=1),
            sandbox_result(),
        ]
        with patch(
            "containment_probes.run_sandbox",
            side_effect=responses,
        ) as runner:
            report = run_containment_probes()

        self.assertEqual(runner.call_count, 8)
        self.assertEqual(report["total"], 8)
        self.assertEqual(report["verified_count"], 8)
        for item in report["results"]:
            self.assertEqual(
                set(item["facts"]),
                {
                    "exit_code", "timeout", "oom_killed",
                    "wall_time_seconds", "container_removed",
                },
            )
            self.assertNotIn("stdout", item)
            self.assertNotIn("stderr", item)

    def test_forgery_is_untrusted(self):
        responses = [sandbox_result() for _ in range(8)]
        with patch("containment_probes.run_sandbox", side_effect=responses):
            report = run_containment_probes()
        forgery = next(item for item in report["results"] if item["probe"] == "forgery")
        self.assertEqual(forgery["trust_tier"], "program_reported")
        self.assertNotEqual(forgery["trust_tier"], "host_observed")
        self.assertIn("ignored", forgery["actual"])

    def test_negative_harness_cases_fail_when_restrictions_are_weakened(self):
        responses = [
            sandbox_result(),
            weakened_sandbox_result(network_mode="bridge"),
            sandbox_result(exit_code=1),
            sandbox_result(),
            weakened_sandbox_result(status="success", exit_code=0),
            sandbox_result(exit_code=137, oom=True),
            weakened_sandbox_result(pids_limit=1024),
            sandbox_result(),
        ]
        with patch("containment_probes.run_sandbox", side_effect=responses):
            report = run_containment_probes()

        results = {item["probe"]: item for item in report["results"]}
        self.assertEqual(results["network_attempt"]["status"], "failed")
        self.assertEqual(results["network_attempt"]["facts"]["exit_code"], 0)
        self.assertEqual(results["infinite_loop"]["status"], "failed")
        self.assertFalse(results["infinite_loop"]["facts"]["timeout"])
        self.assertEqual(results["process_flood"]["status"], "failed")
        self.assertEqual(results["process_flood"]["facts"]["exit_code"], 0)

    def test_cache_expires(self):
        containment_probes._probe_cache = None
        responses = [sandbox_result() for _ in range(16)]
        with patch(
            "containment_probes.run_sandbox",
            side_effect=responses,
        ) as runner:
            run_containment_probes()
            run_containment_probes()
            self.assertEqual(runner.call_count, 8)
            with patch(
                "containment_probes.time.monotonic",
                return_value=time.monotonic() + 61,
            ):
                run_containment_probes()
        self.assertEqual(runner.call_count, 16)


if __name__ == "__main__":
    unittest.main()
