import unittest
from unittest.mock import patch

import demo_samples
from agent_schemas import AgentPlan


class DemoSampleCacheTests(unittest.TestCase):
    def setUp(self):
        demo_samples._analysis_cache.clear()

    def test_builtin_analysis_is_cached(self):
        analysis = {"summary": "cached", "predictions": [], "limitations": []}
        with patch(
            "demo_samples.analyze_python_code",
            return_value=analysis,
        ) as analyzer:
            first = demo_samples.cached_ai_analysis("normal")
            second = demo_samples.cached_ai_analysis("normal")

        self.assertEqual(first, analysis)
        self.assertEqual(second, analysis)
        analyzer.assert_called_once()

    def test_unknown_sample_is_rejected(self):
        with self.assertRaises(KeyError):
            demo_samples.sample_source("unknown")

    def test_analysis_cache_ttl_is_one_hour(self):
        self.assertEqual(demo_samples.AI_CACHE_TTL_SECONDS, 3600)

    def test_demo_pipeline_uses_fresh_sandbox_and_comparison(self):
        analysis = {"summary": "demo", "predictions": [], "limitations": []}
        plan = AgentPlan(
            summary="demo plan",
            test_cases=[{
                "focus_area": "general",
                "question": "Does it run?",
                "rationale": "Demo coverage.",
                "expected_evidence": ["operation_telemetry_unavailable"],
            }],
            stop_conditions=["Stop after one run."],
        )
        with patch("demo_samples.cached_ai_analysis", return_value=analysis), \
                patch("demo_samples.create_agent_plan", return_value=plan), \
                patch("demo_samples.evaluate_plan", return_value={"allowed": True}), \
                patch("demo_samples.run_sandbox", return_value={
                    "status": "success",
                    "host_metadata": {"available": False},
                }) as sandbox, \
                patch("demo_samples.verify_containment", return_value={
                    "overall_status": "failed",
                }), \
                patch("demo_samples.compare_predictions", return_value={
                    "status": "completed",
                    "comparisons": [],
                }) as compare:
            result = demo_samples.run_demo_pipeline("normal")
        sandbox.assert_called_once()
        compare.assert_called_once()
        self.assertTrue(result["execution_performed"])
        self.assertEqual(result["pipeline_stages"]["comparison"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
