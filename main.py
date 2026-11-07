import asyncio
import hashlib
import logging
import os
import secrets
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_orchestrator import run_agent_pipeline
from containment_probes import run_containment_probes
from database import get_run, list_runs, persist_run, reserve_daily_run
from demo_samples import list_demo_samples, run_demo_pipeline
from execution_gate import SandboxBusyError
from request_controls import allow_request, client_ip


app = FastAPI(
    title="DryRun AI",
    description="Know what your Python code does before you run it.",
    version="0.4.0"
)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

MAX_FILE_SIZE = 100 * 1024
MAX_REQUEST_SIZE = 2 * 1024 * 1024
MAX_ANALYSIS_OBJECTIVE_LENGTH = 500
DAILY_RUN_CAP = 200
STAGING_DIR = "/var/lib/dryrun/staging"
ANALYSIS_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="dryrun-analysis",
)


@app.get("/")
def home():
    return FileResponse(
        Path(__file__).parent / "static" / "index.html"
    )


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.middleware("http")
async def request_size_guard(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            too_large = int(content_length) > MAX_REQUEST_SIZE
        except ValueError:
            too_large = True
        if too_large:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request is too large."},
            )
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


def _check_run_limits(
    request: Request,
    *,
    bypass_daily: bool = False,
    demo_request: bool = False,
) -> None:
    ip = client_ip(request)
    if not allow_request(ip):
        raise HTTPException(
            status_code=429,
            detail="Request limit reached. Please try again later.",
        )
    if not bypass_daily:
        try:
            available = reserve_daily_run(DAILY_RUN_CAP)
        except Exception:
            logging.exception("Daily run limit check failed")
            raise HTTPException(
                status_code=503,
                detail="Run service is temporarily unavailable.",
            )
        if not available:
            detail = (
                "Demo limit reached. Please try again tomorrow."
                if demo_request
                else "Daily run limit reached. Please try again tomorrow."
            )
            raise HTTPException(status_code=429, detail=detail)


def _demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "").strip().lower() == "true"


def _require_demo_upload_access(access_code: str) -> None:
    expected = os.getenv("ACCESS_CODE", "").strip()
    submitted = access_code.strip()
    if not expected or not secrets.compare_digest(submitted, expected):
        raise HTTPException(
            status_code=403,
            detail="Demo uploads require an access code.",
        )


@app.get("/demo/samples")
def demo_samples():
    if not _demo_mode():
        raise HTTPException(status_code=404, detail="Not found.")
    return {
        "samples": list_demo_samples(),
        "upload_enabled": bool(os.getenv("ACCESS_CODE", "").strip()),
    }


@app.post("/demo/analyze/{sample_id}")
def demo_analyze(sample_id: str, request: Request):
    if not _demo_mode():
        raise HTTPException(status_code=404, detail="Not found.")
    _check_run_limits(request, demo_request=True)
    try:
        return {
            "sample_id": sample_id,
            **run_demo_pipeline(sample_id),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Demo sample not found.")
    except Exception:
        logging.exception("Demo sample analysis failed")
        raise HTTPException(
            status_code=502,
            detail="Demo sample analysis could not complete.",
        )


@app.post("/containment/probes")
def containment_probes(request: Request, x_api_key: str = Header(default="")):
    if not (_demo_mode() and not x_api_key):
        _require_api_key(x_api_key)
    _check_run_limits(
        request,
        bypass_daily=bool(x_api_key) and not _demo_mode(),
        demo_request=_demo_mode(),
    )
    try:
        return run_containment_probes()
    except SandboxBusyError:
        raise HTTPException(
            status_code=503,
            detail="Sandbox capacity is temporarily unavailable.",
        )
    except Exception:
        logging.exception("Containment probe lab failed")
        raise HTTPException(
            status_code=502,
            detail="Containment probe lab could not complete.",
        )


@app.post("/analyze")
async def analyze(
    request: Request,
    file: UploadFile = File(...),
    objective: str = Form(default=""),
    x_api_key: str = Header(default=""),
    x_access_code: str = Header(default="", alias="X-Access-Code"),
):
    if _demo_mode():
        _require_demo_upload_access(x_access_code)
    else:
        _require_api_key(x_api_key)

    if not file.filename or Path(file.filename).suffix.lower() != ".py":
        raise HTTPException(
            status_code=400,
            detail="Upload a Python file."
        )

    content = await file.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="File exceeds 100 KB."
        )

    if not content:
        raise HTTPException(
            status_code=400,
            detail="File is empty."
        )

    try:
        source_code = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Python file must use UTF-8 encoding."
        )

    objective = objective.strip()
    if len(objective) > MAX_ANALYSIS_OBJECTIVE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Analysis objective must be 500 characters or fewer.",
        )
    _check_run_limits(
        request,
        bypass_daily=not _demo_mode(),
        demo_request=_demo_mode(),
    )

    temp_path = None

    try:
        os.makedirs(STAGING_DIR, mode=0o700, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".py",
            dir=STAGING_DIR,
            delete=False
        ) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        try:
            os.chown(temp_path, -1, 65534)
            os.chmod(temp_path, 0o440)
        except (OSError, ValueError):
            logging.exception("Uploaded file permission setup failed")
            raise HTTPException(
                status_code=502,
                detail="Analysis service could not prepare the request.",
            )

        loop = asyncio.get_running_loop()
        pipeline_args = (source_code, temp_path)
        if objective:
            pipeline_args += (objective,)
        report = await loop.run_in_executor(
            ANALYSIS_EXECUTOR,
            run_agent_pipeline,
            *pipeline_args,
        )

        if not isinstance(report, dict):
            logging.error("Agent orchestrator returned a non-object report")
            raise HTTPException(
                status_code=502,
                detail="Analysis service could not complete the request.",
            )

        persisted_evidence = []
        if not _demo_mode():
            try:
                persist_run(report, file.filename, content)
            except Exception:
                logging.exception(
                    "DryRun report persistence failed: run_id=%s",
                    report.get("run_id"),
                )
                raise HTTPException(
                    status_code=502,
                    detail="Analysis history could not be saved.",
                )

            persisted = get_run(report.get("run_id"))
            persisted_evidence = (
                persisted.get("evidence", [])
                if isinstance(persisted, dict) else []
            )
        evidence_summary = persisted_evidence or report.get(
            "evidence_summary", []
        )
        pipeline = report.get("pipeline_stages", {})
        overall_status = report.get("overall_status")
        comparison_stage = pipeline.get("comparison", {})
        comparison = {
            "status": comparison_stage.get("status", "not_run"),
            "comparisons": report.get("prediction_comparisons", []),
            "limitations": report.get("limitations", []),
        }
        containment = report.get("containment_report")
        if pipeline.get("policy", {}).get("status") == "denied":
            containment = None
        response = {
            "project": "DryRun AI",
            "analysis_status": (
                "completed" if overall_status == "completed"
                else overall_status or "failed"
            ),
            "ai_predictions": report.get("ai_analysis"),
            "sandbox_execution": report.get("sandbox_result"),
            "comparison": comparison,
            "comparison_status": comparison["status"],
            "run_id": report.get("run_id"),
            "agent_plan": report.get("agent_plan"),
            "policy_decision": report.get("policy_decision"),
            "pipeline_stages": pipeline,
            "execution_scope": report.get("execution_scope"),
            "evidence_summary": evidence_summary,
            "analysis_objective": report.get("analysis_objective", objective),
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "source_size_bytes": len(content),
            "containment_report": containment,
            "containment_status": (
                containment.get("overall_status")
                if isinstance(containment, dict) else None
            ),
            "containment_verified_count": (
                containment.get("verified_count", 0)
                if isinstance(containment, dict) else None
            ),
            "containment_failed_count": (
                containment.get("failed_count", 0)
                if isinstance(containment, dict) else None
            ),
            "containment_unknown_count": (
                containment.get("unknown_count", 0)
                if isinstance(containment, dict) else None
            ),
        }

        if pipeline.get("execution", {}).get("category") == "sandbox_busy":
            return JSONResponse(
                status_code=503,
                content={"detail": "Sandbox capacity is temporarily unavailable."},
            )
        if overall_status == "completed":
            return response
        if pipeline.get("policy", {}).get("status") == "denied":
            return JSONResponse(status_code=422, content=response)

        logging.error(
            "DryRun pipeline did not complete: run_id=%s status=%s",
            report.get("run_id"),
            overall_status,
        )
        return JSONResponse(status_code=502, content=response)

    except HTTPException:
        raise
    except Exception:
        logging.exception("DryRun analysis pipeline failed")
        raise HTTPException(
            status_code=502,
            detail="Analysis service could not complete the request."
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        await file.close()


def _require_api_key(x_api_key: str) -> None:
    expected_key = os.getenv("DRYRUN_API_KEY", "")
    if not expected_key or not secrets.compare_digest(
        x_api_key, expected_key
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
        )


@app.get("/runs")
def runs(
    x_api_key: str = Header(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    _require_api_key(x_api_key)
    try:
        return {
            "runs": list_runs(limit=limit, offset=offset),
            "limit": limit,
            "offset": offset,
        }
    except Exception:
        logging.exception("Run history listing failed")
        raise HTTPException(
            status_code=503,
            detail="Run history is temporarily unavailable.",
        )


@app.get("/runs/{run_id}")
def run_detail(run_id: str, x_api_key: str = Header(default="")):
    _require_api_key(x_api_key)
    if len(run_id) > 100:
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        result = get_run(run_id)
    except Exception:
        logging.exception("Run history lookup failed")
        raise HTTPException(
            status_code=503,
            detail="Run history is temporarily unavailable.",
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return result
