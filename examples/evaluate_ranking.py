"""Offline, consented-signal evaluation; no uploads, keys, or model training."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean

from open_recommender.crypto import load_json
from open_recommender.models import CURRENT_CONTRACT_SCHEMA_VERSION, validate_timestamp, validate_topic_name
from open_recommender.recommender import GrantSessionRanker, GrantSessionRankRequest


def metrics(order: list[str], relevance: dict[str, int], k: int) -> dict:
    """Linear-gain NDCG and recall; all-zero judgments have no meaningful denominator."""
    def dcg(grades):
        return sum(grade / math.log2(rank + 2) for rank, grade in enumerate(grades))

    ideal = dcg(sorted(relevance.values(), reverse=True)[:k])
    relevant_count = sum(grade > 0 for grade in relevance.values())
    return {
        "ndcg": dcg([relevance[item] for item in order[:k]]) / ideal if ideal else None,
        "recall": sum(relevance[item] > 0 for item in order[:k]) / relevant_count if relevant_count else None,
    }


def evaluate(dataset: dict) -> dict:
    if not isinstance(dataset, dict) or set(dataset) != {"version", "synthetic", "as_of", "k", "cases"}:
        raise ValueError("Dataset requires only version, synthetic, as_of, k, and cases.")
    if type(dataset["version"]) is not int or dataset["version"] != 1 or type(dataset["synthetic"]) is not bool:
        raise ValueError("Dataset version must be 1 and synthetic must be a boolean.")
    now = datetime.fromisoformat(validate_timestamp(dataset["as_of"]).replace("Z", "+00:00"))
    k = dataset["k"]
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer.")
    cases = dataset["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a nonempty array.")
    results, seen = [], set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"case_id", "granted_topic_weights", "candidates", "relevance"}:
            raise ValueError("Each case requires only case_id, granted_topic_weights, candidates, and relevance.")
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError("case_id must be a unique nonempty string.")
        seen.add(case_id)
        weights = case["granted_topic_weights"]
        if not isinstance(weights, dict):
            raise ValueError("granted_topic_weights must be an object of already-approved signals.")
        for topic, weight in weights.items():
            validate_topic_name(topic)
            if type(weight) not in (int, float) or not 0 <= weight <= 1:
                raise ValueError("Approved topic weights must be finite numbers in [0, 1].")
        request = GrantSessionRankRequest.from_dict(
            {"candidates": case["candidates"], "top_n": k},
            default_schema_version=CURRENT_CONTRACT_SCHEMA_VERSION,
        )
        relevance = case["relevance"]
        ids = {candidate.candidate_id for candidate in request.candidates}
        if not isinstance(relevance, dict) or set(relevance) != ids:
            raise ValueError("Every candidate needs exactly one explicit relevance judgment; unjudged is not irrelevant.")
        if any(type(grade) is not int or not 0 <= grade <= 3 for grade in relevance.values()):
            raise ValueError("Relevance judgments must be integer grades from 0 through 3.")
        orders = {
            "site": [candidate.candidate_id for candidate in sorted(
                request.candidates, key=lambda candidate: (-candidate.site_score, candidate.candidate_id)
            )],
        }
        for name, signals in (("orf_no_preferences", {}), ("orf", weights)):
            ranking = GrantSessionRanker(signals).rank(request, site_id="offline", grant_id="offline", now=now)
            orders[name] = [candidate.candidate_id for candidate in ranking.ranked_candidates]
        results.append({"case_id": case_id, **{name: metrics(order, relevance, k) for name, order in orders.items()}})

    summary = {}
    for mode in ("site", "orf_no_preferences", "orf"):
        summary[mode] = {}
        for metric in ("ndcg", "recall"):
            values = [result[mode][metric] for result in results if result[mode][metric] is not None]
            summary[mode][metric] = mean(values) if values else None
    deltas = [result["orf"]["ndcg"] - result["site"]["ndcg"]
        for result in results if result["site"]["ndcg"] is not None]
    return {
        "evaluation_version": 1, "synthetic": dataset["synthetic"], "as_of": dataset["as_of"], "k": k,
        "case_count": len(results), "measurable_case_count": len(deltas), "means": summary,
        "orf_vs_site": {"wins": sum(delta > 1e-12 for delta in deltas),
            "ties": sum(abs(delta) <= 1e-12 for delta in deltas), "losses": sum(delta < -1e-12 for delta in deltas)},
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path,
        default=Path(__file__).with_name("ranking-evaluation.json"))
    args = parser.parse_args()
    try:
        report = evaluate(load_json(args.dataset.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        parser.exit(2, f"Evaluation failed: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
