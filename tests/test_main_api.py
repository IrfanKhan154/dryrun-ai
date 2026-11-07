import io
import os
import stat
import tempfile
import unittest
from unittest.mock import call, patch
from pathlib import Path

from fastapi.testclient import TestClient

from main import STAGING_DIR, app
from request_controls import reset_rate_limits


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
        reset_rate_limits()
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

    def post(
        self,
        content=b"print('x')",
        filename="sample.py",
        key=API_KEY,
        objective=None,
    ):
        data = {"file": (filename, io.BytesIO(content), "text/x-python")}
        if objective is not None:
            data["objective"] = (None, objective)
        return self.client.post(
            "/analyze",
            headers={"X-API-Key": key},
            files=data,
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

    def test_request_size_guard_rejects_oversized_requests(self):
        response = self.client.post(
            "/analyze",
            headers={"X-API-Key": API_KEY, "Content-Length": str(2 * 1024 * 1024 + 1)},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "Request is too large.")

    def test_daily_run_cap_returns_friendly_message(self):
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True), \
                patch("main.DAILY_RUN_CAP", 1), \
                patch("main.reserve_daily_run", side_effect=[True, False]), \
                patch("main.run_demo_pipeline", return_value={}):
            self.assertEqual(
                self.client.post("/demo/analyze/normal").status_code,
                200,
            )
            response = self.client.post("/demo/analyze/normal")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["detail"],
            "Demo limit reached. Please try again tomorrow.",
        )
        self.assertNotIn("sqlite", response.text.lower())

    def test_api_key_owner_bypasses_daily_cap_and_probe_click_reserves_once(self):
        with patch("main.DAILY_RUN_CAP", 1), \
                patch("main.run_agent_pipeline", return_value=REPORT), \
                patch("main.persist_run"):
            self.assertEqual(self.post().status_code, 200)
            self.assertEqual(self.post().status_code, 200)
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True), \
                patch("main.DAILY_RUN_CAP", 1), \
                patch("main.run_containment_probes", return_value={"results": []}), \
                patch("main.reserve_daily_run", return_value=True) as reserve:
            response = self.client.post(
                "/containment/probes",
            )
        self.assertEqual(response.status_code, 200)
        reserve.assert_called_once_with(1)

    def test_demo_daily_limit_has_demo_message(self):
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True), \
                patch("main.DAILY_RUN_CAP", 1), \
                patch("main.reserve_daily_run", side_effect=[True, False]), \
                patch("main.run_demo_pipeline", return_value={}):
            self.assertEqual(
                self.client.post("/demo/analyze/normal").status_code,
                200,
            )
            response = self.client.post("/demo/analyze/normal")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["detail"],
            "Demo limit reached. Please try again tomorrow.",
        )

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

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

    def test_analysis_objective_is_bounded_and_forwarded(self):
        with patch("main.run_agent_pipeline", return_value=REPORT) as pipeline:
            response = self.post(objective="Review credential access.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            pipeline.call_args.args[2],
            "Review credential access.",
        )
        self.assertEqual(
            self.post(objective="x" * 501).status_code,
            400,
        )

    def test_planner_failure(self):
        report = {
            **REPORT,
            "overall_status": "failed",
            "pipeline_stages": {
                **REPORT["pipeline_stages"],
                "planning": {
                    "status": "failed",
                    "category": "planner_invalid_json",
                    "error": "AI planner returned malformed or truncated JSON.",
                },
            },
        }
        with patch("main.run_agent_pipeline", return_value=report):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(API_KEY, response.text)
        self.assertIn("planner_invalid_json", response.text)
        self.assertIn("malformed or truncated JSON", response.text)

    def test_containment_probe_endpoint_requires_key_and_returns_report(self):
        self.assertEqual(
            self.client.post("/containment/probes").status_code,
            401,
        )
        probe_report = {
            "verified_count": 8,
            "total": 8,
            "results": [],
            "limitations": [],
        }
        with patch("main.run_containment_probes", return_value=probe_report):
            response = self.client.post(
                "/containment/probes",
                headers={"X-API-Key": API_KEY},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), probe_report)

    def test_demo_probe_does_not_need_api_key_but_upload_needs_access_code(self):
        probe_report = {
            "verified_count": 8,
            "total": 8,
            "results": [],
            "limitations": [],
        }
        with patch.dict(
            os.environ,
            {"DEMO_MODE": "true"},
            clear=True,
        ), patch("main.run_containment_probes", return_value=probe_report):
            response = self.client.post("/containment/probes")
            self.assertEqual(response.status_code, 200)
            upload = self.post(key="")
            self.assertEqual(upload.status_code, 403)

        demo_report = {**REPORT, "run_id": "demo-run"}
        with patch.dict(
            os.environ,
            {"DEMO_MODE": "true", "ACCESS_CODE": "demo-code"},
            clear=True,
        ), patch("main.run_agent_pipeline", return_value=demo_report), \
                patch("main.persist_run") as persist:
            wrong = self.client.post(
                "/analyze",
                headers={"X-Access-Code": "wrong"},
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(wrong.status_code, 403)
            correct = self.client.post(
                "/analyze",
                headers={"X-Access-Code": "demo-code"},
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(correct.status_code, 200)
            persist.assert_not_called()

            whitespace = self.client.post(
                "/analyze",
                headers={"X-Access-Code": "  demo-code  "},
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(whitespace.status_code, 200)

            missing = self.client.post(
                "/analyze",
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(missing.status_code, 403)
            self.assertNotIn("demo-code", missing.text)

    def test_demo_upload_access_configuration_and_header_contract(self):
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True):
            samples = self.client.get("/demo/samples")
            self.assertEqual(samples.status_code, 200)
            self.assertFalse(samples.json()["upload_enabled"])
            response = self.client.post(
                "/analyze",
                headers={"X-Access-Code": "test-code"},
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(response.status_code, 403)

        with patch.dict(
            os.environ,
            {"DEMO_MODE": "true", "ACCESS_CODE": "test-code"},
            clear=True,
        ), patch("main.run_agent_pipeline", return_value=REPORT):
            response = self.client.post(
                "/analyze",
                headers={"X-Access-Code": "test-code"},
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("X-Access-Code", response.text)

    def test_normal_api_key_authentication_remains_required(self):
        with patch.dict(
            os.environ,
            {"DEMO_MODE": "false", "DRYRUN_API_KEY": API_KEY},
            clear=True,
        ), patch(
            "main.run_agent_pipeline",
            return_value={**REPORT, "run_id": "normal-auth-run"},
        ), patch("main.persist_run"):
            missing = self.client.post(
                "/analyze",
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(missing.status_code, 401)
            accepted = self.client.post(
                "/analyze",
                headers={"X-API-Key": API_KEY},
                files={"file": ("sample.py", b"print('x')")},
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertNotIn(API_KEY, accepted.text)

    def test_demo_sample_analysis_uses_cached_server_analysis(self):
        analysis = {"summary": "demo", "predictions": [], "limitations": []}
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True), \
                patch("main.run_demo_pipeline", return_value={
                    "sample_id": "normal",
                    "analysis_status": "completed",
                    "ai_predictions": analysis,
                    "execution_performed": True,
                }) as pipeline:
            first = self.client.post("/demo/analyze/normal")
            second = self.client.post("/demo/analyze/normal")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        pipeline.assert_any_call("normal")

    def test_demo_sample_response_is_renderable_without_history(self):
        demo_report = {
            "sample_id": "normal",
            "analysis_status": "completed",
            "ai_predictions": {
                "summary": "The sample prints a value.",
                "risk_level": "low",
                "predictions": [],
                "limitations": [],
            },
            "sandbox_execution": {
                "status": "success",
                "exit_code": 0,
                "program_stdout": "done",
                "evidence": [{
                    "source": "docker_engine",
                    "evidence_type": "container_lifecycle",
                    "trust_tier": "docker_engine_metadata",
                }],
                "execution_duration_seconds": 0.2,
            },
            "comparison": {"comparisons": [], "limitations": []},
            "containment_report": {
                "overall_status": "passed",
                "verified_count": 9,
                "failed_count": 0,
                "unknown_count": 0,
                "controls": [],
                "limitations": [],
            },
            "pipeline_stages": {
                stage: {"status": "completed"}
                for stage in (
                    "analysis", "planning", "policy",
                    "execution", "containment", "comparison",
                )
            },
            "execution_performed": True,
            "limitations": [],
        }
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True), \
                patch("main.run_demo_pipeline", return_value=demo_report):
            response = self.client.post("/demo/analyze/normal")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sandbox_execution"]["evidence"][0]["source"], "docker_engine")
        self.assertNotIn("run_id", payload)

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
        frontend_root = Path(__file__).parent.parent / "static"
        frontend = "\n".join(
            (frontend_root / name).read_text(encoding="utf-8")
            for name in ("index.html", "styles.css", "app.js")
        )
        self.assertIn("Sandbox configuration checks", frontend)
        self.assertIn("renderContainment", frontend)
        self.assertIn("textContent", frontend)
        self.assertIn("containment_report", frontend)
        self.assertIn("normalizeReport", frontend)
        self.assertIn("sandbox.evidence", frontend)
        self.assertIn("No security findings were confirmed in this run.", frontend)
        self.assertIn("Sandbox configuration checks", frontend)
        self.assertIn("What the agent discovered", frontend)
        self.assertIn("What the agent did", frontend)
        self.assertIn("Execution scope", frontend)
        self.assertIn("Public demo results are shown in Reports and are not saved to shared history.", frontend)
        self.assertIn('/static/styles.css', frontend)
        self.assertIn('/static/styles.css?v=20261107-report-contract', frontend)
        self.assertIn('/static/app.js?v=20261107-report-contract', frontend)
        self.assertIn('data-nav="analyze"', frontend)
        self.assertIn('data-nav="runs"', frontend)
        self.assertIn('data-nav="containment"', frontend)
        self.assertIn('data-nav="reports"', frontend)
        self.assertIn('aria-current="page"', frontend)
        self.assertIn('role="tablist"', frontend)
        self.assertIn('aria-selected="true"', frontend)
        self.assertIn("Checking connection", frontend)
        self.assertIn("Backend online", frontend)
        self.assertIn("Backend unavailable", frontend)
        self.assertIn("function runDemoSample()", frontend)
        self.assertNotIn("async function runDemoSample()", frontend[frontend.index("async function analyze()"):])
        self.assertIn("initializeStep", frontend)
        self.assertIn('fetch("/health")', frontend)
        self.assertIn('"not-started": "Waiting to run"', frontend)

    def test_frontend_contract_renders_actual_report_sections(self):
        frontend = (
            Path(__file__).parent.parent.joinpath("static", "app.js")
            .read_text(encoding="utf-8")
        )
        actual_response = {
            "ai_predictions": {
                "summary": "Five predicted behaviors require review.",
                "risk_level": "high",
                "predictions": [
                    {
                        "operation": f"operation-{index}",
                        "target": f"target-{index}",
                        "confidence": 0.8,
                        "reason": "Model reasoning.",
                    }
                    for index in range(5)
                ],
            },
            "sandbox_execution": {
                "host_metadata": {"source": "docker_engine"},
                "execution_duration_seconds": 1.2,
            },
            "comparison": {
                "comparisons": [
                    {
                        "operation": f"operation-{index}",
                        "target": f"target-{index}",
                        "status": "predicted_not_observed",
                        "reason": "No independent telemetry.",
                    }
                    for index in range(5)
                ],
                "limitations": ["Telemetry is limited."],
            },
            "execution_scope": {
                "planned_test_cases": 5,
                "executed_sandbox_runs": 1,
                "individually_executed_test_cases": 0,
            },
            "containment_report": {"controls": [{} for _ in range(9)]},
            "evidence_summary": [
                {"source": "docker_engine", "trust_tier": "docker_engine_metadata"}
            ],
        }
        self.assertEqual(len(actual_response["ai_predictions"]["predictions"]), 5)
        self.assertEqual(len(actual_response["comparison"]["comparisons"]), 5)
        self.assertEqual(len(actual_response["containment_report"]["controls"]), 9)
        for marker in (
            "comparisonList",
            "renderExecution",
            "hostMetadata",
            "plannedTests",
            "agent_plan",
            "sandbox_execution?.stdout",
            "planned_test_cases",
            "individually_executed_test_cases",
            "predicted_not_observed",
            "Not independently observed",
            "evidence_summary",
            "limitations",
            "textContent",
        ):
            self.assertIn(marker, frontend)
        self.assertNotIn('innerHTML', frontend)

    def test_frontend_assets_are_served(self):
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
