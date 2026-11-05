import json
import os
import selectors
import subprocess
import sys
import time
import uuid
from pathlib import Path


TIMEOUT_SECONDS = 15
CLEANUP_TIMEOUT = 5
MAX_OUTPUT_BYTES = 256 * 1024


def cleanup_container(container_name):
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=CLEANUP_TIMEOUT
        )

        return "confirmed" if result.returncode == 0 else "check_required"

    except (subprocess.TimeoutExpired, OSError):
        return "check_required"


def terminate_container(container_name):
    try:
        subprocess.run(
            ["docker", "kill", container_name],
            capture_output=True,
            text=True,
            timeout=CLEANUP_TIMEOUT
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def inspect_container(container_name, duration_seconds):
    try:
        result = subprocess.run(
            [
                "docker", "inspect",
                "--format", "{{json .State}}",
                container_name
            ],
            capture_output=True,
            text=True,
            timeout=CLEANUP_TIMEOUT
        )

        if result.returncode != 0:
            return {
                "available": False,
                "source": "docker_engine",
                "reason": "container_inspection_failed"
            }

        state = json.loads(result.stdout)

        return {
            "available": True,
            "source": "docker_engine",
            "trust_tier": "docker_engine_metadata",
            "status": state.get("Status"),
            "running": bool(state.get("Running")),
            "oom_killed": bool(state.get("OOMKilled")),
            "exit_code": state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "error": state.get("Error") or None,
            "duration_seconds": round(duration_seconds, 3)
        }

    except (
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError
    ):
        return {
            "available": False,
            "source": "docker_engine",
            "reason": "container_metadata_unavailable"
        }


def build_host_evidence(metadata):
    if not metadata or not metadata.get("available"):
        return []

    return [{
        "source": "docker_engine",
        "evidence_type": "container_lifecycle",
        "trust_tier": "docker_engine_metadata",
        "status": metadata.get("status"),
        "exit_code": metadata.get("exit_code"),
        "oom_killed": metadata.get("oom_killed"),
        "started_at": metadata.get("started_at"),
        "finished_at": metadata.get("finished_at"),
        "duration_seconds": metadata.get("duration_seconds")
    }]


def parse_report(stdout, stderr, returncode,
                 host_metadata=None, duration_seconds=None):

    host_metadata = host_metadata or {
        "available": False,
        "source": "docker_engine",
        "reason": "metadata_not_collected"
    }

    return {
        "status": "success" if returncode == 0 else "failed",
        "exit_code": returncode,
        "stdout": stdout,
        "program_stdout": stdout,
        "stderr": stderr,
        "observations": [],
        "execution_outcomes": [],
        "evidence": build_host_evidence(host_metadata),
        "host_metadata": host_metadata,
        "execution_duration_seconds": (
            round(duration_seconds, 3)
            if duration_seconds is not None else None
        ),
        "evidence_integrity": {
            "status": "sample_markers_ignored",
            "message": (
                "Submitted-program event and outcome markers are "
                "untrusted and are not parsed. Docker Engine lifecycle "
                "metadata is collected separately, but does not prove "
                "individual file, process, or network operations."
            )
        }
    }


def run_sandbox(script_path):
    script = Path(script_path).resolve()

    if not script.is_file() or script.suffix.lower() != ".py":
        raise ValueError("A valid Python file is required.")

    container_name = f"dryrun-{uuid.uuid4().hex[:12]}"

    command = [
        "docker", "run",
        "--name", container_name,
        "--init",
        "--network", "none",
        "--read-only",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "128m",
        "--memory-swap", "128m",
        "--cpus", "0.5",
        "--ulimit", "nofile=64:64",
        "--ulimit", "core=0",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--mount",
        f"type=bind,source={script},target=/app/sample.py,readonly",
        "--workdir", "/app",
        "python:3.12-slim",
        "python", "sample.py"
    ]

    process = None
    selector = selectors.DefaultSelector()
    start_time = time.monotonic()

    output = {
        "stdout": bytearray(),
        "stderr": bytearray()
    }

    truncated = {
        "stdout": False,
        "stderr": False
    }

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True
        )

        streams = {
            process.stdout: "stdout",
            process.stderr: "stderr"
        }

        for stream, name in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        deadline = time.monotonic() + TIMEOUT_SECONDS
        timed_out = False

        while selector.get_map():
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                timed_out = True
                break

            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fd, 4096)

                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue

                name = key.data
                available = MAX_OUTPUT_BYTES - len(output[name])

                if available > 0:
                    output[name].extend(chunk[:available])

                if len(chunk) > available:
                    truncated[name] = True

            if process.poll() is not None and not selector.get_map():
                break

        duration = time.monotonic() - start_time

        if timed_out:
            process.kill()
            process.wait(timeout=2)

            terminate_container(container_name)

            metadata = inspect_container(
                container_name, duration
            )
            cleanup_status = cleanup_container(container_name)

            stdout = output["stdout"].decode(
                "utf-8", errors="replace"
            )
            stderr = output["stderr"].decode(
                "utf-8", errors="replace"
            )

            report = parse_report(
                stdout,
                stderr + "\nExecution exceeded 15 seconds.",
                None,
                metadata,
                duration
            )

            report.update({
                "status": "timeout",
                "exit_code": None,
                "cleanup": cleanup_status,
                "output_truncated": truncated
            })

            return report

        returncode = process.wait(timeout=2)

        duration = time.monotonic() - start_time

        stdout = output["stdout"].decode(
            "utf-8", errors="replace"
        )
        stderr = output["stderr"].decode(
            "utf-8", errors="replace"
        )

        # Inspect the container BEFORE removing it.
        metadata = inspect_container(
            container_name, duration
        )

        cleanup_status = cleanup_container(container_name)

        report = parse_report(
            stdout,
            stderr,
            returncode,
            metadata,
            duration
        )

        report["output_truncated"] = truncated
        report["cleanup"] = cleanup_status

        return report

    except Exception:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

        terminate_container(container_name)

        duration = time.monotonic() - start_time

        metadata = inspect_container(
            container_name, duration
        )

        cleanup_status = cleanup_container(container_name)

        return {
            "status": "error",
            "exit_code": None,
            "stdout": "",
            "stderr": "Sandbox execution could not be completed.",
            "cleanup": cleanup_status,
            "observations": [],
            "execution_outcomes": [],
            "evidence": build_host_evidence(metadata),
            "host_metadata": metadata,
            "execution_duration_seconds": round(duration, 3),
            "evidence_integrity": {
                "status": "sample_markers_ignored",
                "message": "Submitted-program markers are not trusted."
            }
        }

    finally:
        selector.close()

        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


if __name__ == "__main__":
    target = (
        sys.argv[1] if len(sys.argv) > 1
        else "monitor_sample.py"
    )
    print(json.dumps(run_sandbox(target), indent=2))
