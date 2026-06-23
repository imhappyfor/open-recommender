# Cross-site feed

Open Recommender can aggregate recommendations from multiple sites into one local feed.
The feed is computed from the profile event log; it is not a separate hosted index.

## What it does

The CLI command:

```bash
python -m open_recommender.cli feed show profile.orf --top-n 20
```

returns de-duplicated recommendation items with transparent scoring.

## Ranking model

Each item is ranked from three signals:

1. **Consensus** — how many sites recommend the same item
2. **Freshness** — newer recommendations rank higher
3. **Affinity** — how well the item matches the user's topic preferences

The current reference implementation keeps the scoring deterministic and auditable.

## De-duplication

Items are grouped by a canonical entity key built from the item type and external ID.
The feed preserves source attribution so users can see which sites contributed.

## Scope boundary

The feed is computed locally from the profile event log. It does not require a separate feed database or a hidden ranking service.

The current implementation lives in `src/open_recommender/recommender/feed.py`.
Legacy imports remain re-exported from `open_recommender.models` for compatibility.

For the lower-level implementation details, see `docs/aggregated-feed-design.md`.
