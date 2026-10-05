from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from open_recommender.recommender import GrantSessionRanker, GrantSessionRankRequest


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
spec = importlib.util.spec_from_file_location("evaluate_ranking", EXAMPLES / "evaluate_ranking.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class RankingEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.dataset = json.loads((EXAMPLES / "ranking-evaluation.json").read_text())

    def test_metrics_have_known_denominators_and_undefined_cases_are_not_zero(self):
        judgments = {"a": 3, "b": 0}
        self.assertEqual(evaluation.metrics(["a", "b"], judgments, 2), {"ndcg": 1, "recall": 1})
        self.assertAlmostEqual(evaluation.metrics(["b", "a"], judgments, 2)["ndcg"], 1 / math.log2(3))
        self.assertEqual(evaluation.metrics(["b", "a"], judgments, 1), {"ndcg": 0, "recall": 0})
        self.assertEqual(evaluation.metrics(["a"], {"a": 0}, 1), {"ndcg": None, "recall": None})

    def test_example_is_repeatable_and_reports_losses_as_well_as_gains(self):
        before = deepcopy(self.dataset)
        report = evaluation.evaluate(self.dataset)
        self.assertEqual(report, evaluation.evaluate(self.dataset))
        self.assertEqual(before, self.dataset)
        self.assertTrue(report["synthetic"])
        self.assertEqual(report["case_count"], 5)
        self.assertEqual(report["measurable_case_count"], 4)
        self.assertEqual(report["orf_vs_site"], {"wins": 2, "ties": 1, "losses": 1})
        cases = {case["case_id"]: case for case in report["cases"]}
        self.assertEqual(cases["cold-start"]["orf"], cases["cold-start"]["orf_no_preferences"])
        self.assertEqual(cases["freshness-only"]["orf"], cases["freshness-only"]["orf_no_preferences"])
        self.assertLess(cases["stale-preference"]["orf"]["ndcg"], cases["stale-preference"]["site"]["ndcg"])
        self.assertGreater(cases["useful-transfer"]["orf"]["recall"], cases["useful-transfer"]["site"]["recall"])
        self.assertNotIn("orf:technology/python", json.dumps(report))
        self.assertEqual(report, json.loads(subprocess.check_output(
            [sys.executable, str(EXAMPLES / "evaluate_ranking.py")], text=True)))

    def test_malformed_or_partly_judged_datasets_fail_without_a_report(self):
        mutations = [
            lambda data: data.update(k=True),
            lambda data: data.update(as_of="2026-10-01T00:00:00"),
            lambda data: data.update(synthetic="true"),
            lambda data: data.update(version=2),
            lambda data: data.update(cases=[]),
            lambda data: data.update(private_key="must-not-be-accepted"),
            lambda data: data["cases"][0]["relevance"].pop("generic"),
            lambda data: data["cases"][0]["relevance"].update(generic=True),
            lambda data: data["cases"][0]["relevance"].update(generic=4),
            lambda data: data["cases"][0]["granted_topic_weights"].update({"orf:science": float("nan")}),
            lambda data: data["cases"][0]["granted_topic_weights"].update({"orf:science": True}),
            lambda data: data["cases"].append(deepcopy(data["cases"][0])),
        ]
        for mutate in mutations:
            data = deepcopy(self.dataset)
            mutate(data)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                evaluation.evaluate(data)

    def test_ranking_uses_one_fixed_timezone_aware_time_for_freshness_and_result(self):
        request = GrantSessionRankRequest.from_dict({"candidates": [
            {"candidate_id": "one-week", "site_score": 0.8, "published_at": "2026-09-24T00:00:00Z"},
            {"candidate_id": "now", "site_score": 0.8, "published_at": "2026-10-01T00:00:00Z"},
        ]}, default_schema_version="0.3.0")
        ranker = GrantSessionRanker({})
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        result = ranker.rank(request, site_id="offline", grant_id="offline", now=now)
        self.assertEqual(result.reranked_at, "2026-10-01T00:00:00+00:00")
        self.assertEqual([item.breakdown["freshness"] for item in result.ranked_candidates], [1, 0.5])
        self.assertEqual(result, ranker.rank(request, site_id="offline", grant_id="offline", now=now))
        with self.assertRaises(ValueError):
            ranker.rank(request, site_id="offline", grant_id="offline", now=now.replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()
