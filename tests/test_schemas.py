import unittest

from pydantic import ValidationError

from schemas import AIAnalysisReport, Prediction, RiskLevel


def valid_prediction(**overrides):
    value = {
        "operation": "file_read",
        "target": "/etc/passwd",
        "risk_level": "high",
        "reason": "The code reads a system file.",
        "evidence": "open('/etc/passwd')",
        "confidence": 0.95,
    }
    value.update(overrides)
    return value


def valid_report(**overrides):
    value = {
        "summary": "The code reads a sensitive local file.",
        "risk_level": "high",
        "predictions": [valid_prediction()],
        "limitations": ["Static analysis cannot establish execution."],
    }
    value.update(overrides)
    return value


class AIAnalysisReportTests(unittest.TestCase):
    def test_valid_ai_report(self):
        report = AIAnalysisReport.model_validate(valid_report())

        self.assertEqual(report.risk_level, RiskLevel.HIGH)
        self.assertEqual(report.predictions[0].confidence, 0.95)

    def test_valid_and_invalid_risk_levels(self):
        for risk_level in ("low", "medium", "high", "critical"):
            with self.subTest(risk_level=risk_level):
                report = AIAnalysisReport.model_validate(
                    valid_report(risk_level=risk_level)
                )
                self.assertEqual(report.risk_level.value, risk_level)

        with self.assertRaises(ValidationError):
            AIAnalysisReport.model_validate(valid_report(risk_level="unknown"))

    def test_missing_required_fields(self):
        for field in ("summary", "risk_level", "predictions", "limitations"):
            with self.subTest(field=field):
                data = valid_report()
                del data[field]
                with self.assertRaises(ValidationError):
                    AIAnalysisReport.model_validate(data)

    def test_invalid_prediction_structure(self):
        data = valid_report(predictions=[{"operation": "file_read"}])

        with self.assertRaises(ValidationError):
            AIAnalysisReport.model_validate(data)

        with self.assertRaises(ValidationError):
            AIAnalysisReport.model_validate(
                valid_report(predictions={"operation": "file_read"})
            )

    def test_confidence_must_be_between_zero_and_one(self):
        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValidationError):
                    Prediction.model_validate(
                        valid_prediction(confidence=confidence)
                    )

    def test_empty_required_strings_are_rejected(self):
        for field in ("summary",):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    AIAnalysisReport.model_validate(valid_report(**{field: " "}))

        for field in ("operation", "target", "reason"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Prediction.model_validate(valid_prediction(**{field: " "}))

    def test_unexpected_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            AIAnalysisReport.model_validate(valid_report(extra="rejected"))

        with self.assertRaises(ValidationError):
            Prediction.model_validate(valid_prediction(extra="rejected"))

    def test_prediction_list_is_bounded(self):
        predictions = [valid_prediction() for _ in range(101)]

        with self.assertRaises(ValidationError):
            AIAnalysisReport.model_validate(valid_report(predictions=predictions))

    def test_invalid_limitations_are_rejected(self):
        for limitations in ([""], [42], ["x" * 1_001]):
            with self.subTest(limitations=limitations):
                with self.assertRaises(ValidationError):
                    AIAnalysisReport.model_validate(
                        valid_report(limitations=limitations)
                    )


if __name__ == "__main__":
    unittest.main()
