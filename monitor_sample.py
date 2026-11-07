import sys
import os
import json
import subprocess
import time

events = []
outcomes = []


def record_outcome(operation, target, outcome, reason=None, **extra):
    outcomes.append({
        "operation": operation,
        "target": target,
        "outcome": outcome,
        "reason": reason,
        "evidence_type": "sample_reported_execution_result",
        **extra
    })


def audit_monitor(event, args):
    monitored_events = {
        "open",
        "os.remove",
        "os.rename",
        "os.mkdir",
        "os.rmdir",
        "subprocess.Popen",
        "socket.connect"
    }

    if event in monitored_events:
        events.append({
            "event": event,
            "timestamp": time.time(),
            "details": str(args)[:300],
            "evidence_type": "python_audit_event"
        })


# Install the Python audit hook.
try:
    sys.addaudithook(audit_monitor)

    record_outcome(
        "audit_hook_installation",
        "sys.addaudithook",
        "installed"
    )
    print("Audit hook: installed")

except Exception as e:
    record_outcome(
        "audit_hook_installation",
        "sys.addaudithook",
        "failed",
        str(e)
    )


print("=== DryRun Behaviour Monitor ===")

# Test file write inside temporary storage.
try:
    with open("/tmp/dryrun_monitor_test.txt", "w") as f:
        f.write("Monitoring test")

    print("Temporary file write: successful")

    record_outcome(
        "file_write",
        "/tmp/dryrun_monitor_test.txt",
        "allowed"
    )

except Exception as e:
    print("Temporary file write: blocked")

    record_outcome(
        "file_write",
        "/tmp/dryrun_monitor_test.txt",
        "blocked",
        str(e)
    )


# Test write to read-only root filesystem.
try:
    with open("/test_outside.txt", "w") as f:
        f.write("This should fail")

    print("Root filesystem write: successful")

    record_outcome(
        "file_write",
        "/test_outside.txt",
        "allowed"
    )

except Exception as e:
    print("Root filesystem write: BLOCKED")

    record_outcome(
        "file_write",
        "/test_outside.txt",
        "blocked",
        str(e)
    )


# Test subprocess execution.
try:
    result = subprocess.run(
        ["id"],
        capture_output=True,
        text=True,
        timeout=3
    )

    record_outcome(
        "subprocess_execution",
        "id",
        "completed" if result.returncode == 0 else "failed",
        result.stderr[:300] if result.stderr else None,
        exit_code=result.returncode
    )

    print("Process operation:", "completed" if result.returncode == 0 else "failed")

except Exception as e:
    record_outcome(
        "subprocess_execution",
        "id",
        "failed",
        str(e)
    )

    print("Process operation: failed")


print("\n=== OBSERVED EVENTS ===")
print(json.dumps(events, indent=2))

print("\nTotal observed events:", len(events))

print("\n=== EXECUTION OUTCOMES ===")
print(json.dumps(outcomes, indent=2))
