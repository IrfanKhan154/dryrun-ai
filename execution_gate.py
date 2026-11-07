from contextlib import contextmanager
from threading import BoundedSemaphore


MAX_SANDBOX_EXECUTIONS = 2
SANDBOX_ACQUIRE_TIMEOUT_SECONDS = 10
_sandbox_slots = BoundedSemaphore(MAX_SANDBOX_EXECUTIONS)


@contextmanager
def sandbox_execution(timeout: float = SANDBOX_ACQUIRE_TIMEOUT_SECONDS):
    if not _sandbox_slots.acquire(timeout=timeout):
        raise SandboxBusyError(
            "Sandbox capacity is temporarily unavailable."
        )
    try:
        yield
    finally:
        _sandbox_slots.release()


class SandboxBusyError(RuntimeError):
    """Raised when the bounded sandbox capacity cannot be acquired."""
