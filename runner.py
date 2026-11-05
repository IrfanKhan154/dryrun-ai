import json
import subprocess
import sys
import uuid
from pathlib import Path


def run_sandbox(script_path):
    script = Path(script_path).resolve()

    if not script.is_file() or script.suffix != ".py":
        raise ValueError("A valid Python file is required.")

    container_name = f"dryrun-{uuid.uuid4().hex[:12]}"

    command = [
        "docker", "run",
        "--name", container_name,
        "--rm",
        "--network", "none",
        "--read-only",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "128m",
        "--cpus", "0.5",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--mount",
        f"type=bind,source={script},target=/app/sample.py,readonly",
        "--workdir", "/app",
        "python:3.12-slim",
        "python", "sample.py"
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15
        )

        return {
            "status": "success" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }

    except subprocess.TimeoutExpired:
        cleanup = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=10
        )

        return {
            "status": "timeout",
            "exit_code": None,
            "stdout": "",
            "stderr": "Execution exceeded 15 seconds.",
            "cleanup": "confirmed" if cleanup.returncode == 0 else "check_required"
        }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "sample.py"
    print(json.dumps(run_sandbox(target), indent=2))
