import asyncio
import logging
import os
import secrets
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from agent_orchestrator import run_agent_pipeline


app = FastAPI(
    title="DryRun AI",
    description="Know what your Python code does before you run it.",
    version="0.4.0"
)

MAX_FILE_SIZE = 100 * 1024
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


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    x_api_key: str = Header(default="")
):
    expected_key = os.getenv("DRYRUN_API_KEY", "")

    if not expected_key or not secrets.compare_digest(
        x_api_key, expected_key
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key."
        )

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
        report = await loop.run_in_executor(
            ANALYSIS_EXECUTOR,
            run_agent_pipeline,
            source_code,
            temp_path,
        )

        if not isinstance(report, dict):
            logging.error("Agent orchestrator returned a non-object report")
            raise HTTPException(
                status_code=502,
                detail="Analysis service could not complete the request.",
            )

        pipeline = report.get("pipeline_stages", {})
        overall_status = report.get("overall_status")
        comparison_stage = pipeline.get("comparison", {})
        comparison = {
            "status": comparison_stage.get("status", "not_run"),
            "comparisons": report.get("prediction_comparisons", []),
            "limitations": report.get("limitations", []),
        }
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
            "evidence_summary": report.get("evidence_summary", []),
        }

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
