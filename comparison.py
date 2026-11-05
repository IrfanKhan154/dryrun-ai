def compare_predictions(ai_report, sandbox_report):
    predictions = ai_report.get("predictions", [])
    if not isinstance(predictions, list):
        predictions = []

    # Only use output captured by the host-side runner.
    # Never use event or outcome records supplied by submitted code.
    stdout = str(
        sandbox_report.get(
            "program_stdout",
            sandbox_report.get("stdout")
        ) or ""
    )

    comparisons = []

    def normalize(value):
        return str(value or "").strip().lower()

    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue

        operation = normalize(prediction.get("operation"))
        target = normalize(prediction.get("target"))

        if not target or target in {"none", "none detected", "n/a"}:
            continue

        if operation == "stdout_output" and stdout.strip():
            match = {
                "status": "stdout_captured",
                "actual_outcome": "non_empty_stdout_captured",
                "reason": (
                    "The host-side runner captured non-empty standard "
                    "output. This confirms output emission only."
                )
            }
        else:
            match = {
                "status": "predicted_not_observed",
                "actual_outcome": None,
                "reason": (
                    "No trusted host-side evidence is available for this "
                    "operation. Submitted-program event and outcome "
                    "records are deliberately ignored."
                )
            }

        comparisons.append({
            "operation": prediction.get("operation"),
            "target": prediction.get("target"),
            "predicted_risk": prediction.get("risk_level"),
            "ai_confidence": prediction.get("confidence"),
            **match
        })

    return {
        "status": "completed",
        "comparisons": comparisons,
        "observed_but_not_predicted": [],
        "limitations": [
            "Submitted-program event and outcome markers are not trusted or used.",
            "Captured stdout confirms output emission only, not other operations.",
            "Independent host-side file, process, and network telemetry is not implemented yet.",
            "A predicted operation not observed is not proof that it was impossible."
        ]
    }
