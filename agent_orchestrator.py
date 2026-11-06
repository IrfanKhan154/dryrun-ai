import json
import uuid
from pathlib import Path
from typing import Any

from agent_planner import create_agent_plan
from agent_schemas import AgentPlan
from ai_analyzer import analyze_python_code
from comparison import compare_predictions
from containment_lab import verify_containment
from monitored_runner import run_sandbox
from policy_engine import evaluate_plan


def _stage(status: str, error: str | None = None) -> dict[str, str]:
    result = {"status": status}
    if error:
        result["error"] = error
    return result


def _error_message(error: Exception) -> str:
    return str(error)[:500] or error.__class__.__name__


def _base_report(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "overall_status": "failed",
        "pipeline_stages": {
            "analysis": _stage("pending"),
            "planning": _stage("pending"),
            "policy": _stage("pending"),
            "execution": _stage("pending"),
            "containment": _stage("pending"),
            "comparison": _stage("pending"),
        },
        "ai_analysis": None,
        "agent_plan": None,
        "policy_decision": None,
        "sandbox_result": None,
        "containment_report": None,
        "evidence_summary": [],
        "prediction_comparisons": [],
        "limitations": [],
        "execution_scope": {
            "sandbox_profile": "default_restricted_docker",
            "planned_test_cases": 0,
            "executed_sandbox_runs": 0,
            "individually_executed_test_cases": 0,
            "test_cases_individually_executed": False,
        },
    }


def _skip_remaining(report: dict[str, Any], stages: tuple[str, ...]) -> None:
    for name in stages:
        if report["pipeline_stages"][name]["status"] == "pending":
            report["pipeline_stages"][name] = _stage("skipped")


def run_agent_pipeline(source_code: str, script_path: str | Path) -> dict[str, Any]:
    """Run the bounded analysis, planning, policy, execution, and comparison flow."""
    run_id = str(uuid.uuid4())
    report = _base_report(run_id)

    try:
        analysis = analyze_python_code(source_code)
    except Exception as exc:
        report["pipeline_stages"]["analysis"] = _stage(
            "failed", _error_message(exc)
        )
        _skip_remaining(
            report,
            ("planning", "policy", "execution", "containment", "comparison"),
        )
        report["limitations"].append("AI analysis did not complete.")
        return report

    report["ai_analysis"] = analysis
    report["pipeline_stages"]["analysis"] = _stage("completed")

    try:
        plan = create_agent_plan(
            source_code,
            json.dumps(analysis, separators=(",", ":")),
        )
        if not isinstance(plan, AgentPlan):
            raise TypeError("Planner did not return a validated AgentPlan.")
    except Exception as exc:
        report["pipeline_stages"]["planning"] = _stage(
            "failed", _error_message(exc)
        )
        _skip_remaining(
            report, ("policy", "execution", "containment", "comparison")
        )
        report["limitations"].append("AI test planning did not complete.")
        return report

    report["agent_plan"] = plan.model_dump(mode="json")
    report["execution_scope"]["planned_test_cases"] = len(plan.test_cases)
    report["pipeline_stages"]["planning"] = _stage("completed")

    try:
        policy = evaluate_plan(plan)
    except Exception as exc:
        report["pipeline_stages"]["policy"] = _stage(
            "failed", _error_message(exc)
        )
        _skip_remaining(report, ("execution", "containment", "comparison"))
        report["limitations"].append("Policy validation did not complete.")
        return report

    report["policy_decision"] = policy
    report["pipeline_stages"]["policy"] = _stage(
        "approved" if policy.get("allowed") is True else "denied"
    )
    report["limitations"].extend(policy.get("telemetry_limitations", []))

    if policy.get("allowed") is not True:
        report["pipeline_stages"]["execution"] = _stage("skipped")
        report["pipeline_stages"]["containment"] = _stage("skipped")
        report["pipeline_stages"]["comparison"] = _stage("skipped")
        report["limitations"].append(
            "Policy denial prevented sandbox execution."
        )
        return report

    try:
        sandbox_result = run_sandbox(script_path)
    except Exception as exc:
        report["pipeline_stages"]["execution"] = _stage(
            "failed", _error_message(exc)
        )
        _skip_remaining(report, ("comparison",))
        report["limitations"].append(
            "Sandbox infrastructure failed; no successful analysis is claimed."
        )
        return report

    report["sandbox_result"] = sandbox_result
    report["execution_scope"]["executed_sandbox_runs"] = 1
    if not isinstance(sandbox_result, dict):
        report["pipeline_stages"]["execution"] = _stage(
            "failed", "Sandbox did not return a report object."
        )
        _skip_remaining(report, ("comparison",))
        return report

    execution_status = sandbox_result.get("status")
    report["pipeline_stages"]["execution"] = _stage(
        "completed" if execution_status == "success" else "failed"
    )

    try:
        report["containment_report"] = verify_containment(
            sandbox_result.get("host_metadata"),
            report["run_id"],
        )
        report["pipeline_stages"]["containment"] = _stage("completed")
        report["limitations"].extend(
            report["containment_report"].get("limitations", [])
        )
    except Exception as exc:
        report["pipeline_stages"]["containment"] = _stage(
            "failed", _error_message(exc)
        )
        report["limitations"].append(
            "Containment verification did not complete."
        )

    try:
        comparison = compare_predictions(analysis, sandbox_result)
    except Exception as exc:
        report["pipeline_stages"]["comparison"] = _stage(
            "failed", _error_message(exc)
        )
        report["limitations"].append("Evidence comparison did not complete.")
        return report

    report["pipeline_stages"]["comparison"] = _stage("completed")
    report["prediction_comparisons"] = comparison.get("comparisons", [])
    report["evidence_summary"] = sandbox_result.get("evidence", [])
    report["limitations"].extend(comparison.get("limitations", []))
    report["limitations"].append(
        "The plan describes targeted questions; one sandbox run was performed "
        "and planned test cases were not individually executed."
    )

    containment_passed = (
        report.get("containment_report", {}).get("overall_status") == "passed"
    )
    if execution_status == "success" and containment_passed:
        report["overall_status"] = "completed"
    elif execution_status == "success":
        report["overall_status"] = "failed"
    else:
        report["overall_status"] = execution_status or "failed"

    return report
