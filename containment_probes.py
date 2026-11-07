import hashlib
import json
import time
from pathlib import Path
from threading import Lock
from typing import Any

from execution_gate import sandbox_execution
from monitored_runner import run_sandbox


PROBE_ROOT = Path(__file__).parent / "samples" / "probes"
PROBE_CACHE_TTL_SECONDS = 60
_probe_cache: tuple[float, str, dict[str, Any]] | None = None
_probe_cache_lock = Lock()

PROBES: tuple[dict[str, str], ...] = (
    {
        "id": "normal",
        "filename": "normal.py",
        "expected": "Completes with exit code 0",
    },
    {
        "id": "network_attempt",
        "filename": "network_attempt.py",
        "expected": "Fails under network=none",
    },
    {
        "id": "write_outside_tmp",
        "filename": "write_outside_tmp.py",
        "expected": "Fails outside writable /tmp",
    },
    {
        "id": "env_read",
        "filename": "env_read.py",
        "expected": "Completes without exposing values",
    },
    {
        "id": "infinite_loop",
        "filename": "infinite_loop.py",
        "expected": "Times out at sandbox limit",
    },
    {
        "id": "memory_hog",
        "filename": "memory_hog.py",
        "expected": "Is stopped by memory limit",
    },
    {
        "id": "process_flood",
        "filename": "process_flood.py",
        "expected": "Is stopped by process limit",
    },
    {
        "id": "forgery",
        "filename": "forgery.py",
        "expected": "Output remains untrusted",
    },
)


def _host_facts(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result.get("host_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "exit_code": result.get("exit_code"),
        "timeout": result.get("status") == "timeout",
        "oom_killed": metadata.get("oom_killed"),
        "wall_time_seconds": result.get("execution_duration_seconds"),
        "container_removed": result.get("cleanup") == "confirmed",
    }


def _actual(probe_id: str, facts: dict[str, Any]) -> str:
    if probe_id == "forgery":
        return "Program output ignored; no host confirmation"
    if facts["timeout"]:
        return "Timed out"
    if facts["oom_killed"] is True:
        return "OOMKilled"
    if facts["exit_code"] is None:
        return "No exit code"
    return f"Exit code {facts['exit_code']}"


def _passed(probe_id: str, facts: dict[str, Any]) -> bool:
    if not facts["container_removed"]:
        return False
    if probe_id == "normal" or probe_id == "env_read":
        return facts["exit_code"] == 0 and not facts["timeout"]
    if probe_id in {"network_attempt", "write_outside_tmp", "process_flood"}:
        return facts["exit_code"] not in (None, 0) and not facts["timeout"]
    if probe_id == "infinite_loop":
        return facts["timeout"]
    if probe_id == "memory_hog":
        return facts["oom_killed"] is True
    if probe_id == "forgery":
        return True
    return False


def _cache_digest(report: dict[str, Any]) -> str:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached_probe_report() -> dict[str, Any] | None:
    with _probe_cache_lock:
        if _probe_cache is None:
            return None
        created, digest, report = _probe_cache
        if time.monotonic() - created >= PROBE_CACHE_TTL_SECONDS:
            return None
        if _cache_digest(report) != digest:
            return None
        return json.loads(json.dumps(report))


def _store_probe_report(report: dict[str, Any]) -> None:
    global _probe_cache
    with _probe_cache_lock:
        _probe_cache = (time.monotonic(), _cache_digest(report), report)


def run_containment_probes() -> dict[str, Any]:
    cached = _cached_probe_report()
    if cached is not None:
        return cached
    results = []
    for definition in PROBES:
        with sandbox_execution():
            result = run_sandbox(PROBE_ROOT / definition["filename"])
        facts = _host_facts(result)
        results.append({
            "probe": definition["id"],
            "expected": definition["expected"],
            "actual": _actual(definition["id"], facts),
            "status": "passed" if _passed(definition["id"], facts) else "failed",
            "evidence_source": (
                "submitted_program_stdout_untrusted"
                if definition["id"] == "forgery"
                else "host_runner_and_docker_engine"
            ),
            "trust_tier": (
                "program_reported"
                if definition["id"] == "forgery"
                else "host_observed"
            ),
            "facts": facts,
        })
    passed = sum(item["status"] == "passed" for item in results)
    report = {
        "verified_count": passed,
        "total": len(results),
        "results": results,
        "limitations": [
            "Probe outcomes are bounded observations, not proof of universal containment.",
            "The forgery probe output is always program_reported and untrusted.",
        ],
    }
    _store_probe_report(report)
    return report
