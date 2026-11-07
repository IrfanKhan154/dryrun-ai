import hashlib
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from database import (
    get_run,
    initialize_database,
    list_runs,
    persist_run,
    reserve_daily_run,
)


def report(run_id="run-1", status="completed", allowed=True):
    return {
        "run_id": run_id,
        "overall_status": status,
        "ai_analysis": {
            "risk_level": "high",
            "summary": "Bounded report",
        },
        "policy_decision": {"allowed": allowed},
        "sandbox_result": {"status": "success", "exit_code": 0},
        "containment_report": {
            "overall_status": "passed",
            "controls": [
                {
                    "control_id": f"control-{index}",
                    "status": "verified",
                    "expected_value": True,
                    "actual_value": True,
                    "explanation": "verified",
                    "evidence_source": "docker_engine_configuration_metadata",
                }
                for index in range(9)
            ],
        },
        "pipeline_stages": {
            "analysis": {"status": "completed"},
            "planning": {"status": "completed"},
            "policy": {"status": "approved"},
            "execution": {"status": "completed"},
        },
        "prediction_comparisons": [{
            "operation": "file_read",
            "target": "/tmp/x",
            "predicted_risk": "high",
            "reason": "reason",
            "ai_confidence": 0.8,
            "status": "predicted_not_observed",
        }],
        "evidence_summary": [{
            "source": "docker_engine",
            "evidence_type": "container_lifecycle",
            "trust_tier": "docker_engine_metadata",
            "status": "exited",
        }],
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "nested" / "dryrun.sqlite3")

    def tearDown(self):
        self.directory.cleanup()

    def test_initialization_and_tables(self):
        initialize_database(self.path)
        with sqlite3.connect(self.path) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "runs", "agent_steps", "findings", "evidence",
                "containment_checks",
            }.issubset(tables))
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 1
            )
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO agent_steps(run_id,stage,status) "
                    "VALUES ('missing','x','x')"
                )

    def test_successful_persistence_and_hash(self):
        source = b"print('secret?')"
        persist_run(report(), "folder/unsafe name.py", source, self.path)
        result = get_run("run-1", self.path)
        self.assertEqual(result["run"]["filename"], "unsafe_name.py")
        self.assertEqual(
            result["run"]["source_sha256"],
            hashlib.sha256(source).hexdigest(),
        )
        self.assertEqual(len(result["steps"]), 4)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["evidence"]), 1)
        self.assertEqual(len(result["containment_checks"]), 9)
        self.assertEqual(
            result["evidence"][0]["payload_sha256"],
            hashlib.sha256(
                result["evidence"][0]["payload_json"].encode()
            ).hexdigest(),
        )

    def test_foreign_keys_and_failed_policy_denied_runs(self):
        persist_run(
            report("denied", "failed", False),
            "x.py",
            b"source",
            self.path,
        )
        result = get_run("denied", self.path)
        self.assertEqual(result["run"]["policy_allowed"], 0)
        self.assertEqual(result["run"]["status"], "failed")

    def test_duplicate_run_id_and_rollback(self):
        persist_run(report(), "x.py", b"x", self.path)
        with self.assertRaises(sqlite3.IntegrityError):
            persist_run(report(), "x.py", b"x", self.path)
        with self.assertRaises(ValueError):
            persist_run(
                {**report("bad"), "evidence_summary": ["x" * 20_000]},
                "x.py",
                b"x",
                self.path,
            )
        self.assertIsNone(get_run("bad", self.path))

    def test_pagination_ordering_and_permissions(self):
        persist_run(report("old"), "x.py", b"x", self.path)
        persist_run(report("new"), "x.py", b"x", self.path)
        rows = list_runs(1, 0, self.path)
        self.assertEqual(rows[0]["run_id"], "new")
        self.assertEqual(rows[0]["source_sha256"], hashlib.sha256(b"x").hexdigest())
        self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.path).parent.stat().st_mode & 0o777, 0o700)

    def test_source_and_secrets_are_not_stored(self):
        persist_run(report(), "x.py", b"API_KEY=secret-value", self.path)
        raw = Path(self.path).read_bytes()
        self.assertNotIn(b"secret-value", raw)
        self.assertNotIn(b"API_KEY", raw)
        self.assertNotIn(b"print", raw)

    def test_concurrent_connections(self):
        errors = []

        def save(index):
            try:
                persist_run(report(f"run-{index}"), "x.py", b"x", self.path)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=save, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(list_runs(100, 0, self.path)), 5)


class DatabaseApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "api.sqlite3")
        self.env = patch.dict(
            os.environ,
            {"DRYRUN_DB_PATH": self.path, "DRYRUN_API_KEY": "key"},
            clear=True,
        )
        self.env.start()
        from fastapi.testclient import TestClient
        from main import app
        self.client = TestClient(app)

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_history_authentication_and_missing_run(self):
        self.assertEqual(self.client.get("/runs").status_code, 401)
        self.assertEqual(
            self.client.get("/runs", headers={"X-API-Key": "wrong"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/runs/missing", headers={"X-API-Key": "key"}
            ).status_code,
            404,
        )

    def test_history_pagination_and_detail(self):
        persist_run(report(), "x.py", b"x", self.path)
        response = self.client.get(
            "/runs?limit=10", headers={"X-API-Key": "key"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["runs"]), 1)
        detail = self.client.get(
            "/runs/run-1", headers={"X-API-Key": "key"}
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["containment_checks"]), 9)

    def test_daily_run_cap_is_persistent_and_atomic(self):
        self.assertTrue(reserve_daily_run(2, self.path))
        self.assertTrue(reserve_daily_run(2, self.path))
        self.assertFalse(reserve_daily_run(2, self.path))
        self.assertFalse(reserve_daily_run(2, self.path))


if __name__ == "__main__":
    unittest.main()
