import io
import os
import stat
import tempfile
import unittest
from unittest.mock import call, patch
from pathlib import Path

from fastapi.testclient import TestClient

from main import STAGING_DIR, app


API_KEY = "api-test-key"
REPORT = {
    "run_id": "run-123",
    "overall_status": "completed",
    "ai_analysis": {"summary": "safe", "predictions": [], "limitations": []},
    "agent_plan": {"summary": "plan", "test_cases": [], "stop_conditions": ["stop"]},
    "policy_decision": {"allowed": True, "policy_version": "1.0"},
    "pipeline_stages": {
        "analysis": {"status": "completed"},
        "planning": {"status": "completed"},
        "policy": {"status": "approved"},
        "execution": {"status": "completed"},
        "comparison": {"status": "completed"},
    },
    "sandbox_result": {"status": "success"},
    "evidence_summary": [],
    "containment_report": {
        "overall_status": "passed",
        "verified_count": 9,
        "failed_count": 0,
        "unknown_count": 0,
        "controls": [],
        "limitations": [],
    },
    "prediction_comparisons": [],
    "limitations": ["One sandbox run only."],
    "execution_scope": {
        "planned_test_cases": 1,
        "executed_sandbox_runs": 1,
        "individually_executed_test_cases": 0,
        "test_cases_individually_executed": False,
    },
}


class MainApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.database_directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "DRYRUN_API_KEY": API_KEY,
                "DRYRUN_DB_PATH": os.path.join(
                    self.database_directory.name, "test.sqlite3"
                ),
            },
            clear=True,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.database_directory.cleanup()

    def post(self, content=b"print('x')", filename="sample.py", key=API_KEY):
        return self.client.post(
            "/analyze",
            headers={"X-API-Key": key},
            files={"file": (filename, io.BytesIO(content), "text/x-python")},
        )

    def test_missing_and_invalid_api_keys(self):
        response = self.client.post(
            "/analyze",
            files={"file": ("sample.py", b"print('x')")},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.post(key="wrong").status_code, 401)

    def test_upload_validation(self):
        self.assertEqual(self.post(filename="sample.txt").status_code, 400)
        self.assertEqual(self.post(content=b"").status_code, 400)
        self.assertEqual(
            self.post(content=b"x" * (100 * 1024 + 1)).status_code, 413
        )
        self.assertEqual(self.post(content=b"\xff").status_code, 400)

    def test_successful_orchestrator_integration_and_contract(self):
        with patch("main.run_agent_pipeline", return_value=REPORT) as pipeline:
            response = self.post()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        for field in (
            "project", "analysis_status", "ai_predictions",
            "sandbox_execution", "comparison", "comparison_status",
            "run_id", "agent_plan", "policy_decision",
            "pipeline_stages", "execution_scope", "evidence_summary",
            "containment_report", "containment_status",
            "containment_verified_count", "containment_failed_count",
            "containment_unknown_count",
        ):
            self.assertIn(field, data)
        pipeline.assert_called_once()

    def test_planner_failure(self):
        report = {
            **REPORT,
            "overall_status": "failed",
            "pipeline_stages": {
                **REPORT["pipeline_stages"],
                "planning": {"status": "failed"},
            },
        }
        with patch("main.run_agent_pipeline", return_value=report):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(API_KEY, response.text)

    def test_policy_denial(self):
        report = {
            **REPORT,
            "overall_status": "failed",
            "pipeline_stages": {
                **REPORT["pipeline_stages"],
                "policy": {"status": "denied"},
                "execution": {"status": "skipped"},
            },
            "policy_decision": {"allowed": False, "reasons": ["denied"]},
        }
        with patch("main.run_agent_pipeline", return_value=report):
            response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["pipeline_stages"]["execution"]["status"],
            "skipped",
        )
        self.assertIsNone(response.json()["containment_report"])

    def test_sandbox_infrastructure_failure(self):
        report = {
            **REPORT,
            "overall_status": "failed",
            "pipeline_stages": {
                **REPORT["pipeline_stages"],
                "execution": {"status": "failed"},
            },
            "sandbox_result": None,
        }
        with patch("main.run_agent_pipeline", return_value=report):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotEqual(response.json()["analysis_status"], "completed")

    def test_temp_file_cleanup(self):
        captured = []

        def fake_pipeline(source, path):
            captured.append(path)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(os.path.dirname(path), STAGING_DIR)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o440)
            self.assertEqual(os.stat(path).st_gid, 65534)
            return REPORT

        with patch("main.run_agent_pipeline", side_effect=fake_pipeline):
            response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(captured[0]))

    def test_permission_setup_failure_is_safe_and_does_not_run_pipeline(self):
        with patch("main.os.chown", side_effect=OSError("chown failed")), \
                patch("main.run_agent_pipeline") as pipeline:
            response = self.post()

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("chown failed", response.text)
        pipeline.assert_not_called()

    def test_permission_setup_uses_required_group_and_mode(self):
        with patch("main.os.chown") as chown, \
                patch("main.os.chmod") as chmod, \
                patch("main.run_agent_pipeline", return_value=REPORT):
            response = self.post()

        self.assertEqual(response.status_code, 200)
        staged_path = chown.call_args.args[0]
        self.assertEqual(os.path.dirname(staged_path), STAGING_DIR)
        chown.assert_called_once_with(staged_path, -1, 65534)
        self.assertIn(
            call(staged_path, 0o440),
            chmod.call_args_list,
        )
        self.assertFalse(os.path.exists(staged_path))

    def test_frontend_contains_containment_report_rendering(self):
        frontend = (
            Path(__file__).parent.parent / "static" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Sandbox Containment", frontend)
        self.assertIn("renderContainment", frontend)
        self.assertIn("textContent", frontend)
        self.assertIn("containment_report", frontend)


if __name__ == "__main__":
    unittest.main()
