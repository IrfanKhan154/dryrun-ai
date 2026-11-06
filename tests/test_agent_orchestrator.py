import unittest
from unittest.mock import DEFAULT, patch

from agent_orchestrator import run_agent_pipeline
from agent_schemas import AgentPlan


ANALYSIS = {
    "summary": "The code may write a temporary file.",
    "risk_level": "medium",
    "predictions": [{
        "operation": "file_write",
        "target": "/tmp/example",
        "risk_level": "medium",
        "reason": "The source opens a temporary path for writing.",
        "evidence": "open('/tmp/example', 'w')",
        "confidence": 0.8,
    }],
    "limitations": ["Static analysis cannot establish execution."],
}

PLAN_DATA = {
    "summary": "Check the temporary file behavior.",
    "test_cases": [{
        "focus_area": "filesystem",
        "question": "Does the code attempt a file write?",
        "rationale": "File writes are relevant to the risk assessment.",
        "expected_evidence": [
            "docker_lifecycle",
            "exit_status",
            "operation_telemetry_unavailable",
        ],
    }],
    "stop_conditions": ["Stop on timeout."],
}

SANDBOX = {
    "status": "success",
    "exit_code": 0,
    "program_stdout": "done",
    "stderr": "",
    "evidence": [{
        "source": "docker_engine",
        "trust_tier": "docker_engine_metadata",
    }],
}

COMPARISON = {
    "status": "completed",
    "comparisons": [{
        "operation": "file_write",
        "status": "predicted_not_observed",
    }],
    "limitations": ["Operation telemetry is unavailable."],
}


def plan():
    return AgentPlan.model_validate(PLAN_DATA)


class AgentOrchestratorTests(unittest.TestCase):
    def components(self):
        return patch.multiple(
            "agent_orchestrator",
            analyze_python_code=lambda source: ANALYSIS,
            create_agent_plan=lambda source, previous: plan(),
            evaluate_plan=lambda value: {
                "allowed": True,
                "policy_version": "1.0",
                "sandbox_profile": "default_restricted_docker",
                "reasons": ["approved"],
                "telemetry_limitations": [
                    "operation_telemetry_unavailable"
                ],
            },
            run_sandbox=lambda path: SANDBOX,
            compare_predictions=lambda analysis, sandbox: COMPARISON,
        )

    def test_successful_complete_pipeline(self):
        with self.components():
            result = run_agent_pipeline("print('done')", "/tmp/script.py")

        self.assertEqual(result["overall_status"], "completed")
        self.assertEqual(result["pipeline_stages"]["analysis"]["status"], "completed")
        self.assertEqual(result["pipeline_stages"]["planning"]["status"], "completed")
        self.assertEqual(result["pipeline_stages"]["policy"]["status"], "approved")
        self.assertEqual(result["pipeline_stages"]["execution"]["status"], "completed")
        self.assertEqual(result["pipeline_stages"]["comparison"]["status"], "completed")

    def test_analysis_failure_stops_pipeline(self):
        with patch("agent_orchestrator.analyze_python_code",
                   side_effect=ValueError("analysis failed")):
            result = run_agent_pipeline("source", "/tmp/script.py")

        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["pipeline_stages"]["analysis"]["status"], "failed")
        self.assertEqual(result["pipeline_stages"]["planning"]["status"], "skipped")

    def test_planner_failure_prevents_execution(self):
        with patch.multiple(
            "agent_orchestrator",
            analyze_python_code=lambda source: ANALYSIS,
            create_agent_plan=DEFAULT,
            run_sandbox=DEFAULT,
        ) as mocks:
            mocks["create_agent_plan"].side_effect = ValueError("planner failed")
            result = run_agent_pipeline("source", "/tmp/script.py")

        self.assertEqual(result["pipeline_stages"]["planning"]["status"], "failed")
        mocks["run_sandbox"].assert_not_called()

    def test_policy_denial_prevents_sandbox(self):
        with self.components():
            with patch("agent_orchestrator.evaluate_plan", return_value={
                "allowed": False,
                "policy_version": "1.0",
                "sandbox_profile": "default_restricted_docker",
                "reasons": ["denied"],
                "telemetry_limitations": [],
            }) as policy, patch(
                "agent_orchestrator.run_sandbox"
            ) as sandbox:
                result = run_agent_pipeline("source", "/tmp/script.py")

        policy.assert_called_once()
        sandbox.assert_not_called()
        self.assertEqual(result["pipeline_stages"]["execution"]["status"], "skipped")

    def test_policy_approval_permits_one_fixed_sandbox_run(self):
        with self.components(), patch(
            "agent_orchestrator.run_sandbox", return_value=SANDBOX
        ) as sandbox:
            result = run_agent_pipeline("source", "/fixed/staged.py")

        sandbox.assert_called_once_with("/fixed/staged.py")
        self.assertEqual(result["execution_scope"]["executed_sandbox_runs"], 1)

    def test_sandbox_failure_is_reported(self):
        failed = {**SANDBOX, "status": "failed", "exit_code": 2}
        with self.components(), patch(
            "agent_orchestrator.run_sandbox", return_value=failed
        ):
            result = run_agent_pipeline("source", "/tmp/script.py")

        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["pipeline_stages"]["execution"]["status"], "failed")
        self.assertEqual(result["pipeline_stages"]["comparison"]["status"], "completed")

    def test_evidence_provenance_and_comparison_inputs_are_preserved(self):
        with self.components(), patch(
            "agent_orchestrator.compare_predictions",
            return_value=COMPARISON,
        ) as compare:
            result = run_agent_pipeline("source", "/tmp/script.py")

        compare.assert_called_once_with(ANALYSIS, SANDBOX)
        self.assertEqual(result["evidence_summary"], SANDBOX["evidence"])
        self.assertEqual(result["prediction_comparisons"], COMPARISON["comparisons"])

    def test_run_id_and_scope_do_not_claim_individual_execution(self):
        with self.components():
            result = run_agent_pipeline("source", "/tmp/script.py")

        self.assertTrue(result["run_id"])
        self.assertEqual(result["execution_scope"]["planned_test_cases"], 1)
        self.assertFalse(result["execution_scope"]["test_cases_individually_executed"])
        self.assertEqual(
            result["execution_scope"]["individually_executed_test_cases"], 0
        )

    def test_planner_cannot_supply_execution_arguments(self):
        malicious = plan()
        malicious.test_cases[0].question = "docker run --privileged"
        with self.components(), patch(
            "agent_orchestrator.create_agent_plan", return_value=malicious
        ), patch("agent_orchestrator.run_sandbox") as sandbox:
            run_agent_pipeline("source", "/server/staged.py")

        sandbox.assert_called_once_with("/server/staged.py")


if __name__ == "__main__":
    unittest.main()
