import json
import os
import unittest
from unittest.mock import Mock, patch

import ai_analyzer


def valid_prediction(**overrides):
    value = {
        "operation": "file_read",
        "target": "/etc/passwd",
        "risk_level": "high",
        "reason": "The code reads a system file.",
        "evidence": "open('/etc/passwd')",
        "confidence": 0.95,
    }
    value.update(overrides)
    return value


def valid_report(**overrides):
    value = {
        "summary": "The code reads a sensitive local file.",
        "risk_level": "high",
        "predictions": [valid_prediction()],
        "limitations": ["Static analysis cannot establish execution."],
    }
    value.update(overrides)
    return value


def mock_response(content):
    response = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    return response


class AnalyzePythonCodeTests(unittest.TestCase):
    api_key = "test-vultr-key"

    def call_with_response(self, response, source="print('hello')"):
        with patch.dict(
            os.environ,
            {"VULTR_INFERENCE_API_KEY": self.api_key},
            clear=True,
        ), patch("ai_analyzer.httpx.post", return_value=response):
            return ai_analyzer.analyze_python_code(source)

    def test_missing_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "API key is missing"):
                ai_analyzer.analyze_python_code("print('hello')")

    def test_empty_source_code(self):
        with patch.dict(
            os.environ,
            {"VULTR_INFERENCE_API_KEY": self.api_key},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "cannot be empty"):
                ai_analyzer.analyze_python_code(" \n")

    def test_source_larger_than_100_kb(self):
        with patch.dict(
            os.environ,
            {"VULTR_INFERENCE_API_KEY": self.api_key},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "exceeds 100 KB"):
                ai_analyzer.analyze_python_code("x" * (100 * 1024 + 1))

    def test_correct_bearer_authorization_header(self):
        response = mock_response(json.dumps(valid_report()))
        with patch(
            "ai_analyzer.httpx.post", return_value=response
        ) as post, patch.dict(
            os.environ,
            {"VULTR_INFERENCE_API_KEY": self.api_key},
            clear=True,
        ):
            ai_analyzer.analyze_python_code("print('hello')")

        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            f"Bearer {self.api_key}",
        )

    def test_successful_valid_ai_response(self):
        response = mock_response(json.dumps(valid_report()))

        result = self.call_with_response(response)

        self.assertEqual(result, valid_report())
        self.assertIsInstance(result["risk_level"], str)

    def test_invalid_json(self):
        response = mock_response("{not-json")

        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            self.call_with_response(response)

    def test_malformed_provider_response(self):
        response = Mock()
        response.json.return_value = {"choices": []}

        with self.assertRaisesRegex(ValueError, "did not contain"):
            self.call_with_response(response)

    def test_invalid_risk_level(self):
        response = mock_response(
            json.dumps(valid_report(risk_level="extreme"))
        )

        with self.assertRaisesRegex(ValueError, "schema validation"):
            self.call_with_response(response)

    def test_invalid_confidence(self):
        response = mock_response(
            json.dumps(valid_report(
                predictions=[valid_prediction(confidence=1.1)]
            ))
        )

        with self.assertRaisesRegex(ValueError, "schema validation"):
            self.call_with_response(response)

    def test_missing_required_report_fields(self):
        report = valid_report()
        del report["summary"]
        response = mock_response(json.dumps(report))

        with self.assertRaisesRegex(ValueError, "schema validation"):
            self.call_with_response(response)

    def test_unexpected_ai_response_fields(self):
        response = mock_response(
            json.dumps(valid_report(unexpected="rejected"))
        )

        with self.assertRaisesRegex(ValueError, "schema validation"):
            self.call_with_response(response)


if __name__ == "__main__":
    unittest.main()
