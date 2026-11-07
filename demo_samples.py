import hashlib
import time
import json
from pathlib import Path
from threading import Lock
from typing import Any

from ai_analyzer import analyze_python_code
from agent_planner import create_agent_plan
from agent_schemas import AgentPlan
from comparison import compare_predictions
from containment_lab import verify_containment
from execution_gate import SandboxBusyError, sandbox_execution
from monitored_runner import run_sandbox
from policy_engine import evaluate_plan


DEMO_SAMPLE_ROOT = Path(__file__).parent / "samples" / "probes"
DEMO_SAMPLES = {
    "normal": "normal.py",
    "network_attempt": "network_attempt.py",
    "write_outside_tmp": "write_outside_tmp.py",
    "infinite_loop": "infinite_loop.py",
    "forgery": "forgery.py",
}
AI_CACHE_TTL_SECONDS = 60 * 60
_analysis_cache: dict[str, tuple[float, str, dict[str, Any]]] = {}
_analysis_cache_lock = Lock()


def sample_source(sample_id: str) -> str:
    filename = DEMO_SAMPLES.get(sample_id)
    if filename is None:
        raise KeyError(sample_id)
    return (DEMO_SAMPLE_ROOT / filename).read_text(encoding="utf-8")


def list_demo_samples() -> list[dict[str, str]]:
    return [
        {"id": sample_id, "filename": filename}
        for sample_id, filename in DEMO_SAMPLES.items()
    ]


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cached_ai_analysis(sample_id: str) -> dict[str, Any]:
    source = sample_source(sample_id)
    now = time.monotonic()
    with _analysis_cache_lock:
        cached = _analysis_cache.get(sample_id)
        if cached and now - cached[0] < AI_CACHE_TTL_SECONDS:
            if _digest(cached[2]) == cached[1]:
                return json.loads(json.dumps(cached[2]))
    analysis = analyze_python_code(source)
    with _analysis_cache_lock:
        _analysis_cache[sample_id] = (now, _digest(analysis), analysis)
    return json.loads(json.dumps(analysis))


def run_demo_pipeline(sample_id: str) -> dict[str, Any]:
    source = sample_source(sample_id)
    analysis = cached_ai_analysis(sample_id)
    report: dict[str, Any] = {
        "sample_id": sample_id,
        "analysis_status": "failed",
        "ai_predictions": analysis,
        "execution_performed": False,
        "pipeline_stages": {
            "analysis": {"status": "completed"},
            "planning": {"status": "pending"},
            "policy": {"status": "pending"},
            "execution": {"status": "pending"},
            "containment": {"status": "pending"},
            "comparison": {"status": "pending"},
        },
        "limitations": [],
    }
    try:
        planner_context = json.dumps(
            {"analysis": analysis, "analysis_objective": ""},
            separators=(",", ":"),
        )
        plan = create_agent_plan(source, planner_context)
        if not isinstance(plan, AgentPlan):
            raise TypeError("Planner did not return a validated plan.")
        report["agent_plan"] = plan.model_dump(mode="json")
        report["pipeline_stages"]["planning"] = {"status": "completed"}
        policy = evaluate_plan(plan)
        report["policy_decision"] = policy
        report["pipeline_stages"]["policy"] = {
            "status": "approved" if policy.get("allowed") is True else "denied"
        }
        if policy.get("allowed") is not True:
            report["pipeline_stages"]["execution"] = {"status": "skipped"}
            report["pipeline_stages"]["containment"] = {"status": "skipped"}
            report["pipeline_stages"]["comparison"] = {"status": "skipped"}
            report["limitations"].append("Policy denied sandbox execution.")
            return report
        with sandbox_execution():
            sandbox = run_sandbox(DEMO_SAMPLE_ROOT / DEMO_SAMPLES[sample_id])
        report["sandbox_execution"] = sandbox
        report["execution_performed"] = True
        report["pipeline_stages"]["execution"] = {
            "status": "completed" if sandbox.get("status") == "success" else "failed"
        }
        containment = verify_containment(
            sandbox.get("host_metadata"), f"demo-{sample_id}"
        )
        report["containment_report"] = containment
        report["pipeline_stages"]["containment"] = {"status": "completed"}
        report["comparison"] = compare_predictions(analysis, sandbox)
        report["pipeline_stages"]["comparison"] = {"status": "completed"}
        report["analysis_status"] = (
            "completed"
            if sandbox.get("status") == "success"
            and containment.get("overall_status") == "passed"
            else "failed"
        )
        return report
    except SandboxBusyError:
        report["pipeline_stages"]["execution"] = {
            "status": "failed",
            "category": "sandbox_busy",
            "error": "Sandbox capacity is temporarily unavailable.",
        }
        report["limitations"].append("Sandbox capacity was unavailable.")
        return report
    except Exception:
        report["limitations"].append("Demo pipeline could not complete.")
        return report
