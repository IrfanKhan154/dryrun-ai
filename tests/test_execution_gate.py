import threading
import time
import unittest

from execution_gate import (
    MAX_SANDBOX_EXECUTIONS,
    SandboxBusyError,
    sandbox_execution,
)


class ExecutionGateTests(unittest.TestCase):
    def test_capacity_is_two(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        def worker():
            nonlocal active, maximum
            with sandbox_execution():
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.05)
                with lock:
                    active -= 1

        threads = [
            threading.Thread(target=worker)
            for _ in range(MAX_SANDBOX_EXECUTIONS + 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(maximum, MAX_SANDBOX_EXECUTIONS)
        self.assertEqual(active, 0)

    def test_slot_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "sandbox failure"):
            with sandbox_execution():
                raise RuntimeError("sandbox failure")

        acquired = threading.Event()

        def worker():
            with sandbox_execution():
                acquired.set()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=1)
        self.assertTrue(acquired.is_set())

    def test_acquire_timeout_is_clean(self):
        first = sandbox_execution()
        second = sandbox_execution()
        first.__enter__()
        second.__enter__()
        try:
            with self.assertRaises(SandboxBusyError):
                with sandbox_execution(timeout=0.01):
                    pass
        finally:
            second.__exit__(None, None, None)
            first.__exit__(None, None, None)

    def test_analysis_and_probes_share_slots(self):
        import agent_orchestrator
        import containment_probes

        entered = threading.Event()
        release = threading.Event()

        def holder(module):
            with module.sandbox_execution():
                entered.set()
                release.wait(timeout=1)

        threads = [
            threading.Thread(
                target=holder,
                args=(agent_orchestrator,),
            ),
            threading.Thread(
                target=holder,
                args=(containment_probes,),
            ),
        ]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(SandboxBusyError):
            with sandbox_execution(timeout=0.01):
                pass
        release.set()
        for thread in threads:
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
