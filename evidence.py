import json
import time


def create_evidence(operation, target, attempted, outcome, reason=None):
    return {
        "timestamp": time.time(),
        "operation": operation,
        "target": target,
        "attempted": attempted,
        "outcome": outcome,
        "reason": reason,
        "evidence_source": "python_audit_event_and_execution_result"
    }


if __name__ == "__main__":
    evidence = create_evidence(
        operation="file_write",
        target="/test_outside.txt",
        attempted=True,
        outcome="blocked",
        reason="Read-only file system"
    )

    print(json.dumps(evidence, indent=2))
