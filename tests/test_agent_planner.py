import json
import os
import unittest
from unittest.mock import Mock, patch

from agent_planner import create_agent_plan


def valid_plan():
    return {
        "summary": "Check filesystem behavior and sandbox outcomes.",
        "test_cases": [{
            "focus_area": "filesystem",
            "question": "Does the code attempt file access?",
            "rationale": "File access is security-relevant.",
            "expected_evidence": [
                "program_stdout",
                "docker_lifecycle",
                "operation_telemetry_unavailable",
            ],
        }],
        "stop_conditions": ["Stop at timeout or resource exhaustion."],
    }


def response_for(value):
    response = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(value)}}]
    }
    return response


class AgentPlannerTests(unittest.TestCase):
    def test_valid_agent_plan(self):
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post",
                      return_value=response_for(valid_plan())):
            plan = create_agent_plan("open('/tmp/x')")
        self.assertEqual(plan.test_cases[0].focus_area.value, "filesystem")

    def test_invalid_focus_area(self):
        value = valid_plan()
        value["test_cases"][0]["focus_area"] = "arbitrary_command"
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post",
                      return_value=response_for(value)):
            with self.assertRaisesRegex(ValueError, "schema validation"):
                create_agent_plan("print('x')")

    def test_missing_fields(self):
        value = valid_plan()
        del value["summary"]
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post",
                      return_value=response_for(value)):
            with self.assertRaisesRegex(ValueError, "schema validation"):
                create_agent_plan("print('x')")

    def test_empty_test_cases(self):
        value = valid_plan()
        value["test_cases"] = []
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post",
                      return_value=response_for(value)):
            with self.assertRaisesRegex(ValueError, "schema validation"):
                create_agent_plan("print('x')")

    def test_excessive_test_cases(self):
        value = valid_plan()
        value["test_cases"] = value["test_cases"] * 6
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post",
                      return_value=response_for(value)):
            with self.assertRaisesRegex(ValueError, "schema validation"):
                create_agent_plan("print('x')")

    def test_malformed_provider_response(self):
        response = Mock()
        response.json.return_value = {"choices": []}
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post", return_value=response):
            with self.assertRaisesRegex(ValueError, "did not contain"):
                create_agent_plan("print('x')")

    def test_missing_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "API key"):
                create_agent_plan("print('x')")

    def test_authorization_header(self):
        response = response_for(valid_plan())
        with patch.dict(os.environ, {"VULTR_INFERENCE_API_KEY": "key"}), \
                patch("agent_planner.httpx.post",
                      return_value=response) as post:
            create_agent_plan("print('x')")
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer key",
        )
