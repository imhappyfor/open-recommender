# Evaluate ranking

The included reranker is a transparent baseline, not a proven replacement for a
site's recommender. This offline tool compares it with a site's scores on the
same, fully judged candidate pool. It runs locally, without a service or uploads.

## Run the example

From the checkout with the Python package installed:

```sh
python examples/evaluate_ranking.py
python examples/evaluate_ranking.py /path/to/consented-evaluation.json
```

The included [dataset](../examples/ranking-evaluation.json) is deliberately
**synthetic**: useful transfer, stale preferences, cold start, freshness, and no
relevant candidates. It is a regression example, not evidence of user satisfaction
or performance on a real site. There is no learning or tuning during evaluation.

The JSON report compares three orders:

| Order | Signals |
| --- | --- |
| `site` | Descending site score; ties use ascending candidate ID |
| `orf_no_preferences` | Current ORF ranker with freshness but no topic weights or feedback |
| `orf` | Same ranker with the supplied approved topic weights; no feedback |

Compare `orf` with both baselines: improvement caused by freshness alone is not
evidence that portable preferences helped. `as_of` fixes freshness and the ranking
clock; identical input produces identical reports. Site order uses scores, not an
existing site's separate business rules or original tie ordering.

## Supply judgments, not inferred clicks

The dataset uses version `1`, a boolean `synthetic`, a timezone-aware `as_of`,
positive integer `k`, and a nonempty `cases` array. Each case contains:

- `case_id`: a unique, opaque label; do not use a profile ID or person's name.
- `granted_topic_weights`: already-approved signals in `[0, 1]`; `{}` for cold start.
- `candidates`: the [ranking request candidate format](protocol.md#grant-exchange-and-use).
- `relevance`: one explicit integer judgment per candidate ID: `0` irrelevant,
  `1` somewhat relevant, `2` relevant, `3` very relevant.

Missing judgments, extra judgment IDs, duplicate case/candidate IDs, unsupported
dataset fields, and invalid values fail the whole run. An unobserved or unclicked
item must not silently receive grade zero. Grades should come from independent
held-out judgments, not from the same topic-match formula being evaluated.

## Read the metrics

**NDCG@k** uses linear gain: `sum(grade / log2(rank + 1))`, with ranks starting at
one, divided by that sum for the ideal top-k order. Higher is better; a perfect
order scores one. This follows the [normalized discounted-gain definition](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.ndcg_score.html),
using actual emitted ordering rather than averaging score ties. It does **not**
use exponential gain (`2^grade - 1`).

**Recall@k** is the fraction of candidates with grade above zero that appear in
the top k. Both metrics are restricted to the supplied pool; they do not measure
whether candidate generation missed useful items outside it. If k exceeds pool
size, all candidates are considered. All-zero cases return `null` and are excluded
from means, while remaining visible in `cases` and `case_count`.

Means weight each measurable case equally. `measurable_case_count` shows their count;
`orf_vs_site` counts NDCG wins, ties (within `1e-12`), and losses. Inspect individual
cases rather than letting a mean hide regressions. Repeated cases from one person
are not independent evidence; this tool supplies no significance test.

## Data and claim boundaries

Only use data that people agreed could be used for evaluation. Build weights from
the approved projection, never a raw profile or private sync history. The tool does
not authenticate a grant or prove scopes were enforced; service tests cover that
separate boundary. Reports omit candidate contents and topic weights, but case
labels and aggregate preferences can still be sensitive. Keep real datasets and
reports outside Git and apply the agreed retention/deletion policy locally.

Offline relevance is not a safety, privacy, latency, diversity, or adoption test.
It does not model revocation, feedback loops, exploration, position bias, or drift.
Do not turn the synthetic mean into a product-performance claim. A real-site claim
needs representative, consented held-out judgments and a separately approved
evaluation of user satisfaction, regressions, and operational behavior.
