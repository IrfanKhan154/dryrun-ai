import unittest

from agent_schemas import AgentPlan
from policy_engine import (
    POLICY_VERSION,
    SANDBOX_PROFILE,
    evaluate_plan,
)


def valid_plan():
    return AgentPlan.model_validate({
        "summary": "Bounded security checks.",
        "test_cases": [{
            "focus_area": "network",
            "question": "Does the code attempt network access?",
            "rationale": "Network access is not allowed in the sandbox.",
            "expected_evidence": [
                "docker_lifecycle",
                "exit_status",
                "operation_telemetry_unavailable",
            ],
        }],
        "stop_conditions": ["Stop on timeout."],
    })


class PolicyEngineTests(unittest.TestCase):
    def test_policy_approval(self):
        decision = evaluate_plan(valid_plan())
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["policy_version"], POLICY_VERSION)
        self.assertEqual(decision["sandbox_profile"], SANDBOX_PROFILE)

    def test_rejects_unvalidated_plan(self):
        with self.assertRaises(TypeError):
            evaluate_plan({"test_cases": []})

    def test_unsupported_requests_are_not_accepted(self):
        with self.assertRaises(ValueError):
            AgentPlan.model_validate({
                "summary": "Unsupported request.",
                "test_cases": [{
                    "focus_area": "shell_command",
                    "question": "Run a command.",
                    "rationale": "Not allowed.",
                    "expected_evidence": ["program_stdout"],
                }],
                "stop_conditions": ["Stop."],
            })

    def test_arbitrary_commands_and_docker_arguments_are_not_model_fields(self):
        with self.assertRaises(ValueError):
            AgentPlan.model_validate({
                **valid_plan().model_dump(),
                "command": "docker run --privileged",
            })

    def test_telemetry_limitations_are_explicit(self):
        decision = evaluate_plan(valid_plan())
        self.assertIn("operation_telemetry_unavailable",
                      decision["telemetry_limitations"])
        self.assertTrue(any(
            "not independently verified" in reason
            for reason in decision["telemetry_limitations"]
        ))


if __name__ == "__main__":
    unittest.main()
