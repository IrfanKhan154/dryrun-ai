import os
import tempfile
import secrets
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Header
from fastapi.responses import FileResponse

from monitored_runner import run_sandbox
from ai_analyzer import analyze_python_code
from comparison import compare_predictions


app = FastAPI(
    title="DryRun AI",
    description="Know what your Python code does before you run it.",
    version="0.4.0"
)

MAX_FILE_SIZE = 100 * 1024


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
        # Stage 1: AI predicts behaviour without executing code.
        predictions = analyze_python_code(source_code)

        # Stage 2: Save source for isolated sandbox execution.
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".py",
            dir="/tmp",
            delete=False
        ) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name

        os.chmod(temp_path, 0o644)

        # Stage 3: Execute inside Docker sandbox.
        execution = run_sandbox(temp_path)

        # Stage 4: Return predictions and observations.
        # Stage 4: Compare AI predictions with sandbox evidence.
        comparison = compare_predictions(predictions, execution)

        # Stage 5: Return the complete analysis report.
        return {
            "project": "DryRun AI",
            "analysis_status": "completed",
            "ai_predictions": predictions,
            "sandbox_execution": execution,
            "comparison": comparison,
            "comparison_status": comparison["status"]
        }

    except Exception:
        import logging
        logging.exception("DryRun analysis pipeline failed")
        raise HTTPException(
            status_code=502,
            detail="AI analysis or sandbox execution could not be completed."
        )

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

        await file.close()
