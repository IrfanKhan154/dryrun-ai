import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = "/var/lib/dryrun/dryrun.sqlite3"
SCHEMA_VERSION = 1
MAX_JSON_BYTES = 16 * 1024
MAX_TEXT_LENGTH = 4_000


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_size_bytes INTEGER NOT NULL,
    risk_level TEXT,
    summary TEXT,
    policy_allowed INTEGER,
    sandbox_status TEXT,
    sandbox_exit_code INTEGER,
    containment_status TEXT,
    failure_stage TEXT
);
CREATE TABLE IF NOT EXISTS agent_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    details_json TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    target TEXT NOT NULL,
    risk_level TEXT,
    reason TEXT,
    confidence REAL,
    comparison_status TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    source TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    trust_tier TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS containment_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    control_id TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_value TEXT,
    actual_value TEXT,
    explanation TEXT,
    evidence_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_steps_run_id ON agent_steps(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run_id ON evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_containment_run_id ON containment_checks(run_id);
"""


def database_path() -> str:
    return os.getenv("DRYRUN_DB_PATH", DEFAULT_DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("Database payload exceeds the configured limit.")
    return encoded


def _hash_json(value: Any) -> tuple[str, str]:
    payload = _json(value)
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(path: str | None = None) -> sqlite3.Connection:
    selected = path or database_path()
    parent = Path(selected).parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    connection = sqlite3.connect(
        selected,
        timeout=5,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database(path: str | None = None) -> None:
    selected = path or database_path()
    connection = _connect(selected)
    try:
        connection.executescript(SCHEMA)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        os.chmod(selected, 0o600)
    finally:
        connection.close()


def _sanitize_filename(filename: str) -> str:
    name = Path(filename or "upload.py").name
    safe = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in name
    )
    return (safe[:255] or "upload.py")


def _failure_stage(report: dict[str, Any]) -> str | None:
    for stage, details in report.get("pipeline_stages", {}).items():
        if isinstance(details, dict) and details.get("status") in {
            "failed", "denied"
        }:
            return stage
    return None


def persist_run(
    report: dict[str, Any],
    filename: str,
    source: bytes,
    path: str | None = None,
) -> None:
    initialize_database(path)
    selected = path or database_path()
    now = _now()
    analysis = report.get("ai_analysis") or {}
    policy = report.get("policy_decision") or {}
    sandbox = report.get("sandbox_result") or {}
    containment = report.get("containment_report") or {}
    run_id = report["run_id"]
    connection = _connect(selected)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO runs (
                run_id, created_at, updated_at, status, filename,
                source_sha256, source_size_bytes, risk_level, summary,
                policy_allowed, sandbox_status, sandbox_exit_code,
                containment_status, failure_stage
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                now,
                now,
                str(report.get("overall_status", "failed"))[:100],
                _sanitize_filename(filename),
                hashlib.sha256(source).hexdigest(),
                len(source),
                str(analysis.get("risk_level", ""))[:100] or None,
                str(analysis.get("summary", ""))[:MAX_TEXT_LENGTH] or None,
                (
                    1 if policy.get("allowed") is True
                    else 0 if "allowed" in policy else None
                ),
                str(sandbox.get("status", ""))[:100] or None,
                sandbox.get("exit_code"),
                str(containment.get("overall_status", ""))[:100] or None,
                _failure_stage(report),
            ),
        )
        stages = report.get("pipeline_stages", {})
        for stage, details in stages.items():
            details = details if isinstance(details, dict) else {}
            connection.execute(
                """
                INSERT INTO agent_steps
                (run_id, stage, status, started_at, finished_at, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stage,
                    str(details.get("status", "unknown"))[:100],
                    details.get("started_at"),
                    details.get("finished_at"),
                    _json(details),
                ),
            )
        for finding in report.get("prediction_comparisons", [])[:100]:
            connection.execute(
                """
                INSERT INTO findings
                (run_id, operation, target, risk_level, reason, confidence,
                 comparison_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(finding.get("operation", ""))[:200],
                    str(finding.get("target", ""))[:2_000],
                    str(finding.get("predicted_risk", ""))[:100] or None,
                    str(finding.get("reason", ""))[:MAX_TEXT_LENGTH] or None,
                    finding.get("ai_confidence"),
                    str(finding.get("status", ""))[:100] or None,
                ),
            )
        evidence = report.get("evidence_summary", [])
        for sequence, item in enumerate(evidence[:100], start=1):
            item = item if isinstance(item, dict) else {"value": str(item)}
            payload, digest = _hash_json(item)
            connection.execute(
                """
                INSERT INTO evidence
                (run_id, sequence_number, source, evidence_type, trust_tier,
                 payload_json, payload_sha256, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sequence,
                    str(item.get("source", "unknown"))[:200],
                    str(item.get("evidence_type", "unknown"))[:200],
                    str(item.get("trust_tier", ""))[:200] or None,
                    payload,
                    digest,
                    str(item.get("collected_at", now))[:100],
                ),
            )
        for check in containment.get("controls", [])[:9]:
            expected = _json(check.get("expected_value"))
            actual = _json(check.get("actual_value"))
            connection.execute(
                """
                INSERT INTO containment_checks
                (run_id, control_id, status, expected_value, actual_value,
                 explanation, evidence_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(check.get("control_id", ""))[:200],
                    str(check.get("status", "unknown"))[:100],
                    expected,
                    actual,
                    str(check.get("explanation", ""))[:MAX_TEXT_LENGTH],
                    str(check.get("evidence_source", ""))[:200],
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _run_summary(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_runs(limit: int = 20, offset: int = 0, path: str | None = None):
    initialize_database(path)
    limit = min(max(int(limit), 1), 100)
    offset = max(int(offset), 0)
    connection = _connect(path)
    try:
        rows = connection.execute(
            """
            SELECT run_id, created_at, updated_at, status, filename,
                   source_sha256, source_size_bytes, risk_level, summary,
                   policy_allowed, sandbox_status, sandbox_exit_code,
                   containment_status, failure_stage
            FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [_run_summary(row) for row in rows]
    finally:
        connection.close()


def get_run(run_id: str, path: str | None = None):
    initialize_database(path)
    connection = _connect(path)
    try:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        result = {"run": dict(row)}
        for table, key in (
            ("agent_steps", "steps"),
            ("findings", "findings"),
            ("evidence", "evidence"),
            ("containment_checks", "containment_checks"),
        ):
            result[key] = [
                dict(item) for item in connection.execute(
                    f"SELECT * FROM {table} WHERE run_id = ? ORDER BY id",
                    (run_id,),
                ).fetchall()
            ]
        return result
    finally:
        connection.close()
