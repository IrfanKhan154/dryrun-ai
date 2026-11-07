from datetime import datetime, timezone
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictStr


MAX_EXPLANATION_LENGTH = 500
MAX_LIMITATIONS = 20


class ContainmentControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    control_id: StrictStr
    status: StrictStr
    expected_value: Any
    actual_value: Any
    explanation: StrictStr = Field(max_length=MAX_EXPLANATION_LENGTH)
    evidence_source: StrictStr


class ContainmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr
    overall_status: StrictStr
    controls: list[ContainmentControl]
    verified_count: int
    failed_count: int
    unknown_count: int
    limitations: list[StrictStr] = Field(max_length=MAX_LIMITATIONS)
    verification_timestamp: StrictStr


def _control(
    control_id: str,
    expected: Any,
    actual: Any,
    status: str,
    explanation: str,
) -> dict[str, Any]:
    return {
        "control_id": control_id,
        "status": status,
        "expected_value": expected,
        "actual_value": actual,
        "explanation": explanation,
        "evidence_source": "docker_engine_configuration_metadata",
    }


def _check(metadata: dict[str, Any], key: str, expected: Any, control_id: str):
    actual = metadata.get(key)
    if actual is None:
        return _control(
            control_id,
            expected,
            None,
            "unknown",
            "Docker Engine metadata did not report this value.",
        )
    if actual == expected:
        return _control(
            control_id,
            expected,
            actual,
            "verified",
            "Docker Engine reported the expected sandbox configuration.",
        )
    return _control(
        control_id,
        expected,
        actual,
        "failed",
        "Docker Engine reported a configuration different from the required restriction.",
    )


def _check_contains(
    metadata: dict[str, Any],
    key: str,
    expected: str,
    control_id: str,
) -> dict[str, Any]:
    actual = metadata.get(key)
    if not isinstance(actual, list):
        return _control(
            control_id,
            [expected],
            actual,
            "unknown",
            "Docker Engine did not report a valid list for this restriction.",
        )
    if expected in actual:
        return _control(
            control_id,
            [expected],
            actual,
            "verified",
            "Docker Engine reported the required restriction.",
        )
    return _control(
        control_id,
        [expected],
        actual,
        "failed",
        "Docker Engine did not report the required restriction.",
    )


def _check_limit(
    metadata: dict[str, Any],
    key: str,
    maximum: int | float,
    expected: str,
    control_id: str,
) -> dict[str, Any]:
    actual = metadata.get(key)
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return _control(
            control_id,
            expected,
            actual,
            "unknown",
            "Docker Engine did not report a valid numeric limit.",
        )
    if 0 < actual <= maximum:
        return _control(
            control_id,
            expected,
            actual,
            "verified",
            "Docker Engine reported a positive limit within the required maximum.",
        )
    return _control(
        control_id,
        expected,
        actual,
        "failed",
        "Docker Engine reported a limit outside the required range.",
    )


def verify_containment(
    metadata: dict[str, Any] | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Verify only Docker-reported configuration, never runtime operations."""
    identifier = run_id or str(uuid.uuid4())
    valid_metadata = metadata if isinstance(metadata, dict) else {}
    available = valid_metadata.get("available") is True
    if not available:
        valid_metadata = {}

    controls = [
        _check(valid_metadata, "network_mode", "none", "network_isolation"),
        _check(valid_metadata, "readonly_rootfs", True, "readonly_rootfs"),
        _check(valid_metadata, "user", "65534:65534", "non_root_user"),
        _check_contains(valid_metadata, "cap_drop", "ALL", "capability_drop"),
        _check_contains(
            valid_metadata,
            "security_opt",
            "no-new-privileges",
            "no_new_privileges",
        ),
        _check_limit(
            valid_metadata,
            "memory",
            128 * 1024 * 1024,
            "0 < memory <= 134217728 bytes",
            "memory_limit",
        ),
        _check_limit(
            valid_metadata,
            "nano_cpus",
            500_000_000,
            "0 < nano_cpus <= 500000000",
            "cpu_limit",
        ),
        _check_limit(
            valid_metadata,
            "pids_limit",
            64,
            "0 < pids_limit <= 64",
            "process_limit",
        ),
        _check(valid_metadata, "privileged", False, "privileged_disabled"),
    ]

    verified = sum(control["status"] == "verified" for control in controls)
    failed = sum(control["status"] == "failed" for control in controls)
    unknown = sum(control["status"] == "unknown" for control in controls)
    overall = "passed" if failed == 0 and unknown == 0 else "failed"
    limitations = [
        "Docker Engine metadata verifies reported configuration only; it "
        "does not prove an operation was blocked.",
        "Filesystem, process, and network operations were not individually "
        "monitored by this verification.",
        "Docker shares the host kernel; this report does not establish "
        "protection against every kernel-level escape.",
    ]
    if not available:
        limitations.append(
            "Required Docker Engine configuration metadata was unavailable "
            "or malformed."
        )

    report = ContainmentReport.model_validate({
        "run_id": identifier,
        "overall_status": overall,
        "controls": controls,
        "verified_count": verified,
        "failed_count": failed,
        "unknown_count": unknown,
        "limitations": limitations,
        "verification_timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return report.model_dump(mode="json")


def verify_containment_metadata(
    metadata: dict[str, Any] | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    return verify_containment(metadata, run_id)
