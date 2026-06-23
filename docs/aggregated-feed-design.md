# AggregatedFeed Class Design

## Overview

The `AggregatedFeed` class reads events from a user's `.orf` file (synced across multiple sites), de-duplicates recommendations, and ranks them by a composite score: **consensus** (how many sites recommend this?) + **freshness** (recent > old) + **user affinity** (matches user's topic interests).

**Key design principle:** Feed aggregation is stateless. It reads from event logs only; no side effects, no external service calls needed.

The current reference implementation lives in `src/open_recommender/recommender/feed.py`.
For compatibility, `open_recommender.models` re-exports the recommender classes used by
older callers and tests.

---

## 1. Class Structure & Data Holding

### Core Class Signature

```python
@dataclass
class AggregatedFeed:
    """
    Aggregates and ranks recommendations from multiple synced sites.
    
    Algorithm summary:
    - De-duplicate: Group recommendations by canonical entity key (type + external_id)
    - Collect scores: Gather all source sites + their confidence scores + timestamps
    - Rank: weighted_score = (consensus_factor * 0.5) + (freshness_factor * 0.3) + (affinity_factor * 0.2)
    - Return: Top N items with transparent score breakdown and source attribution
    
    Scoring details:
    - consensus_factor: (num_sites_recommending / max_possible_sites) normalized to [0,1]
    - freshness_factor: time decay with half-life of 30 days
    - affinity_factor: max(topic_weights) if item tags match user topics, 0 otherwise
    
    Edge cases handled:
    - Cold-start (single-source items): ranked purely on freshness + affinity, no consensus bonus
    - Conflicting scores: transparent display, no averaging (preserve original)
    - Stale data (30+ days old): decays to near-zero freshness
    - Items with no topic tags: rely on consensus + freshness only
    - Empty event log: returns empty feed
    """
    
    profile: ORFProfile
    min_source_for_consensus: int = 2  # Items from < 2 sources ranked on freshness alone
    max_sources_normalization: int = 5  # Max sites for consensus calculation
    freshness_half_life_days: int = 30  # Time decay parameter
    affinity_weight: float = 0.2  # Weight for topic matching
    consensus_weight: float = 0.5  # Weight for multi-site agreement
    freshness_weight: float = 0.3  # Weight for recency
    unknown_source_penalty: float = 0.7  # Discount for items from unregistered sites
```

### Key Data Structures

#### 1. **RecommendationSource** (typed tuple/dataclass)
Represents one site's recommendation for an item.

```python
@dataclass
class RecommendationSource:
    site_id: str
    site_name: str  # Human-readable, extracted from access grant or request
    score: float  # 0-10 range (normalized from original)
    score_source: str  # "imdb" | "netflix" | "user_rating" | "algorithm" 
    recommended_at: str  # ISO timestamp
    recommendation_reason: str | None  # e.g., "you rated similar items highly"
    
    @property
    def days_ago(self) -> float:
        """Age of this recommendation in days (from now)."""
```

#### 2. **AggregatedRecommendationItem**
Represents one de-duplicated item with all its sources.

```python
@dataclass
class AggregatedRecommendationItem:
    entity_key: str  # Canonical key: "<entity_type>:<external_id>" e.g., "movie:tt1234567"
    entity_type: str  # "movie", "article", "song", "podcast", "book", etc.
    external_id: str  # Site's original ID for this item
    title: str
    description: str | None
    thumbnail_url: str | None
    primary_tags: list[str]  # Genre, category, topic keywords from original source
    
    sources: list[RecommendationSource]  # All sites that recommend this
    
    weighted_score: float  # 0.0-1.0, the final ranking score
    consensus_factor: float  # 0.0-1.0, how many sites agree
    freshness_factor: float  # 0.0-1.0, how recent
    affinity_factor: float  # 0.0-1.0, matches user topics
    
    has_conflicting_scores: bool  # True if sources differ by > 2 points
    score_range: tuple[float, float] | None  # (min, max) if conflicting
    best_source: RecommendationSource  # Highest-scoring source
    
    @property
    def average_score(self) -> float:
        """Mean of all source scores."""
    
    @property
    def consensus_count(self) -> int:
        """Number of sources recommending this."""
```

#### 3. **FeedAggregationMetrics** (diagnostic output)
For debugging and transparency.

```python
@dataclass
class FeedAggregationMetrics:
    total_events_processed: int
    recommendation_events: int  # Events with op == SET_RECOMMENDATION (or similar)
    unique_items_found: int  # Before filtering
    unique_items_ranked: int  # After filtering (>= 1 source)
    items_with_consensus: int  # Items from 2+ sources
    cold_start_items: int  # Items from exactly 1 source
    avg_sources_per_item: float
    top_site_count: dict[str, int]  # {site_id: num_items_recommended}
    processing_time_ms: float
```

---

## 2. Ranking Algorithm

### Formula (Weighted Composite Score)

```
weighted_score = (consensus_factor × 0.5) + (freshness_factor × 0.3) + (affinity_factor × 0.2)
```

Each factor is normalized to `[0.0, 1.0]`.

### Factor Calculation Details

#### A. **Consensus Factor** (rewards multi-site agreement)

```
consensus_factor = min(num_sources / max_sources_normalization, 1.0)

where:
  num_sources = number of unique sites recommending this item
  max_sources_normalization = 5 (configurable)
  
Examples:
  - 1 source: 1/5 = 0.2
  - 2 sources: 2/5 = 0.4
  - 3 sources: 3/5 = 0.6
  - 5+ sources: 5/5 = 1.0
```

**Cold-start handling:** Items with only 1 source still contribute to feed (don't filter them out), but get no consensus bonus.

#### B. **Freshness Factor** (exponential decay with configurable half-life)

```
freshness_factor = 0.5 ^ (age_days / half_life_days)

where:
  age_days = (now - recommendation_timestamp) in days
  half_life_days = 30 (default, configurable)

Examples (with 30-day half-life):
  - 0 days old: 0.5^(0/30) = 1.0
  - 15 days old: 0.5^(15/30) = 0.707
  - 30 days old: 0.5^(30/30) = 0.5
  - 60 days old: 0.5^(60/30) = 0.25
  - 90 days old: 0.5^(90/30) = 0.125
```

**Note:** No hard cutoff. Very old items simply approach 0.0 asymptotically.

#### C. **Affinity Factor** (user's topic preferences)

```
affinity_factor = max_topic_weight_if_match_found, or 0.0

where:
  max_topic_weight_if_match_found = highest topic.weight from user's profile
                                     for any topic that appears in item.primary_tags
  
  If no topics match, affinity_factor = 0.0
```

**Logic:**
1. Extract `item.primary_tags` (from recommendation event payload)
2. For each tag, check if user has a topic preference matching that tag (fuzzy or exact match)
3. If match found, use that topic's `.weight` (already 0.0-1.0 from ORF profile)
4. Use the **maximum** weight if multiple topics match
5. If no match, affinity_factor = 0.0

**Example:**
- Item has tags: `["sci-fi", "thriller"]`
- User's profile has: `{orf:entertainment/scifi: 0.9, orf:entertainment/horror: 0.4}`
- Match: `sci-fi` ≈ `orf:entertainment/scifi` → weight 0.9
- affinity_factor = 0.9

#### D. **Final Score Assembly**

For each de-duplicated item:

```
weighted_score = (consensus_factor × 0.5) + (freshness_factor × 0.3) + (affinity_factor × 0.2)

Result: [0.0, 1.0] (all factors normalized)
```

### Concrete Example

**Scenario:** User synced Netflix, IMDb, and an indie podcast platform.

**Item:** Movie "The Matrix"

| Source | Score | Timestamp | Days Old | Recommendation Reason |
|--------|-------|-----------|----------|----------------------|
| Netflix | 8.5 | 2025-01-15 | 10 | "Trending in Sci-Fi" |
| IMDb | 8.7 | 2025-01-10 | 15 | "Top Rated (1990s)" |
| PodcastApp | 7.2 | 2024-12-20 | 31 | "Guests mentioned" |

**Scoring:**

- **Consensus:** 3 sources / 5 max = 0.6
- **Freshness:** average of individual freshness factors
  - Netflix: 0.5^(10/30) = 0.787
  - IMDb: 0.5^(15/30) = 0.707
  - PodcastApp: 0.5^(31/30) = 0.495
  - Average: (0.787 + 0.707 + 0.495) / 3 = **0.663**
- **Affinity:** User has `orf:entertainment/scifi: 0.85`, item tagged `["sci-fi", ...]`
  - **Affinity = 0.85**
- **Final Score:** (0.6 × 0.5) + (0.663 × 0.3) + (0.85 × 0.2) = 0.30 + 0.199 + 0.17 = **0.669**

---

## 3. De-Duplication Logic

### Entity Key Strategy

**Canonical entity key format:**
```
"<entity_type>:<external_id>"

Examples:
  "movie:tt0133093"           # IMDb movie ID
  "article:nytimes/2025-01-20/headline-slug"
  "song:spotify:6rqhFgbbKwnb9MLmUQDvDm"
  "podcast:apple-podcasts:123456"
  "book:isbn:978-0-13-110362-7"
```

### De-Duplication Rules

1. **Extract identity from event payload:**
   - Each recommendation event carries: `payload.entity_type`, `payload.external_id`, `payload.title`
   - Plus optional: `payload.thumbnail_url`, `payload.description`, `payload.tags`

2. **Build entity key:**
   ```python
   entity_key = f"{payload['entity_type']}:{payload['external_id']}"
   ```

3. **Group by entity key:**
   - Iterate through all recommendation events
   - Create a dict: `{entity_key: [source1, source2, ...]}`

4. **Handle merging conflicts:**
   - **Title:** Use the longest or most recent variant (more likely to be up-to-date)
   - **Description:** Prefer from authoritative source or most recent
   - **Tags:** Union of all unique tags from all sources
   - **Thumbnail:** Use first non-null URL found
   - **Scores:** Keep all individual scores, never average (show conflicts transparently)

5. **Filter out near-duplicates (optional anti-spam):**
    - Items with the same title but different external_id (e.g., same movie on Netflix & IMDb with different IDs)
    - Use exact site-provided IDs in the current reference implementation; do not add fuzzy matching

### Why This Approach?

- **Sites already have IDs:** IMDb uses `tt*`, Spotify uses URIs, ISBN is standardized
- **No external service calls:** Everything computed from local events
- **Transparent:** User can see which sources contributed
- **Scalable:** O(n) grouping, no clustering algorithms needed

---

## 4. Cold-Start Handling

### Definition
Items with only 1 recommendation source (new to the feed, niche items).

### Strategy

1. **Don't filter cold-start items out.** Include them in the feed.
2. **Rank them purely by freshness + affinity:**
   ```
   weighted_score = (0 × 0.5) + (freshness_factor × 0.3) + (affinity_factor × 0.2)
                  = (freshness_factor × 0.3) + (affinity_factor × 0.2)
   ```
   
   This gives a max score of 0.5 for perfectly fresh, perfectly matching items.

3. **Order within cold-start items:**
   - Primary: affinity_factor (user's explicit preferences rank highest)
   - Secondary: freshness_factor (recent discoveries from that source)

4. **Display indicator:**
   ```python
   item.consensus_count == 1  # Signal to UI: "Just from Netflix, not yet verified"
   ```

### Rationale

- **User value:** New recommendations from a trusted source are still useful
- **Prevents echo chamber:** Different sites will surface different items
- **Gradual consensus:** If item becomes popular, it'll naturally climb as more sites recommend it
- **Graceful bootstrap:** Users can discover items before they reach consensus threshold

---

## 5. Edge Cases & Testing Strategy

### Test Case Categories

#### **A. De-Duplication Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_dedup_exact_match` | Same movie ID (`tt123`) from Netflix + IMDb | Single entry with 2 sources, no duplicates |
| `test_dedup_different_ids_same_title` | IMDb `tt123` + custom site `my-id-456`, both titled "The Matrix" | Two separate entries (different external IDs) |
| `test_dedup_missing_external_id` | Recommendation event lacks `external_id` | Skip or error gracefully |
| `test_dedup_title_normalization` | "The Matrix" vs "the matrix" (case) | Treat as same item (normalize case in key? or trust external_id) |
| `test_dedup_multiple_thumbnails` | Site A and B both provide thumbnail URLs | Pick first non-null, or most recent |

#### **B. Ranking Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_rank_perfect_consensus` | Movie from 5+ sites, fresh, user loves genre | Score ≈ 1.0 or near-max |
| `test_rank_single_source_recent` | Movie from 1 site, 1 day old, user adores genre | Score ≈ 0.2-0.5 (no consensus bonus) |
| `test_rank_stale_no_affinity` | Movie from 1 site, 100 days old, no matching topics | Score ≈ 0.01 (minimal) |
| `test_rank_high_consensus_no_affinity` | 5 sources agree, item has no matching topics | Score = consensus + freshness only |
| `test_rank_conflicting_scores_high_low` | Netflix 9/10, other site 2/10 (same item) | Both scores preserved, not averaged |
| `test_rank_multiple_matching_topics` | Item tagged `[scifi, thriller]`, user likes both at 0.9 + 0.7 | Affinity uses max (0.9), not sum |
| `test_rank_unknown_site_penalty` | Item from unregistered site (no access grant) | Apply 0.7× discount or require site registration |

#### **C. Cold-Start Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_cold_start_bootstrap` | New item from 1 source, very fresh | Ranks on freshness (0.3×) + affinity (0.2×) |
| `test_cold_start_no_affinity` | New item, no matching topics | Ranks purely on freshness, likely near end |
| `test_cold_start_becomes_hot` | Item starts at 1 source, second site recommends it | Consensus factor jumps from 0.2 → 0.4, re-ranks |
| `test_cold_start_old_and_lonely` | 1 source, 60 days old, no affinity | Very low score, sinks to bottom |

#### **D. Temporal Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_freshness_time_decay` | 3 identical items, ages 0/15/30 days | Scores decay following 0.5^(age/30) |
| `test_freshness_relative_ranking` | Item A (5 days, no affinity) vs Item B (30 days, high affinity) | Item B wins due to affinity weight (0.2 vs 0.3) |
| `test_timestamps_in_future` | Recommendation event timestamp is > now | Treat as "now" or error gracefully |
| `test_timestamps_invalid_format` | Timestamp is malformed ISO | Error or skip event |

#### **E. Metadata Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_missing_title` | Recommendation event lacks `title` | Skip item or use external_id as fallback |
| `test_missing_tags` | Recommendation event has no `tags` field | Affinity factor = 0.0, no error |
| `test_empty_profile_topics` | User's profile has no topic preferences | All items have affinity_factor = 0.0 (no penalty) |
| `test_null_scores` | Recommendation event lacks `score` field | Use neutral 5.0? Or skip? |
| `test_score_out_of_range` | Score is 15.0 or -2.5 (not 0-10) | Clamp to [0.0, 10.0] or error |

#### **F. Event Log Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_empty_profile` | Profile has zero events | Return empty feed + metrics |
| `test_no_recommendation_events` | Profile has only `SET_TOPIC` events, no recommendations | Return empty feed |
| `test_single_recommendation` | Profile has 1 recommendation event | Feed with 1 item |
| `test_duplicate_events` | Same recommendation from same site recorded twice | Deduplicate by event_id? Or accept both timestamps? |
| `test_revoked_site_access` | Item from a site that user later revoked access to | Include in feed but mark as "revoked source"? Or filter out? |

#### **G. Serialization Edge Cases**

| Test | Scenario | Expected Behavior |
|------|----------|-------------------|
| `test_to_dict_preserves_scores` | Serialize → deserialize round-trip | All scores preserved, no precision loss |
| `test_to_dict_null_thumbnail` | Item has no thumbnail | Field present but null in JSON |
| `test_to_dict_empty_tags` | Item has empty tag list | Empty array in JSON, not null |

---

## 6. Known Limitations

### Limitations of This Design

1. **No Trend Detection**
   - Doesn't detect "suddenly popular" items (spike in recommendations in last 24h)
   - All freshness is time-only decay, not velocity

2. **No Site Trustworthiness**
   - All sources weighted equally (if one site is spam-heavy, can't downweight it)

3. **No User Engagement Signal**
   - Doesn't consider "has user clicked this recommendation before?"

4. **Topic Matching is Shallow**
   - Simple `tag in topics` check, no semantic similarity
   - "Horror" won't match a user who likes "suspense"

5. **No Filtering by Genre/Type**
   - Feed includes movies, podcasts, articles mixed together

6. **No Multi-Context Support**
   - Doesn't handle "work mode vs. weekend mode" contexts

7. **Score Range Handling**
   - Assumes all sources use 0-10 scale; sites might use 0-5 or 0-100

8. **No Caching**
   - Every `feed.aggregate()` call re-processes entire event log

---

## 7. Method Signatures (Preview for Implementation)

```python
class AggregatedFeed:
    def __init__(
        self,
        profile: ORFProfile,
        min_source_for_consensus: int = 2,
        max_sources_normalization: int = 5,
        freshness_half_life_days: int = 30,
        affinity_weight: float = 0.2,
        consensus_weight: float = 0.5,
        freshness_weight: float = 0.3,
    ):
        """Initialize aggregation parameters."""
    
    def aggregate(
        self,
        top_n: int | None = None,
        filter_by_sites: set[str] | None = None,
        filter_by_entity_types: set[str] | None = None,
        min_score: float = 0.0,
    ) -> tuple[list[AggregatedRecommendationItem], FeedAggregationMetrics]:
        """
        Main entry point: process all events, deduplicate, rank, filter, return top N.
        
        Returns: (ranked_items, metrics)
        """
    
    def deduplicate_items(self) -> dict[str, AggregatedRecommendationItem]:
        """
        Group recommendation events by entity_key.
        Returns: dict[entity_key -> item with all sources collected]
        """
    
    def rank_items(
        self,
        items: dict[str, AggregatedRecommendationItem],
    ) -> list[AggregatedRecommendationItem]:
        """
        Compute weighted_score for each item, sort descending.
        Returns: list[item] sorted by score, highest first
        """
    
    def compute_consensus_factor(self, num_sources: int) -> float:
        """Normalize source count to [0, 1]."""
    
    def compute_freshness_factor(self, timestamp: str) -> float:
        """Exponential decay based on age."""
    
    def compute_affinity_factor(self, item_tags: list[str]) -> float:
        """Max topic weight matching item tags."""
    
    def find_matching_topics(self, item_tags: list[str]) -> list[str]:
        """
        Fuzzy match item tags to user's topics.
        Handles namespace mismatch: "sci-fi" ≈ "orf:entertainment/scifi"
        """
    
    def to_dict(self, items: list[AggregatedRecommendationItem]) -> dict:
        """Serialize feed to JSON-compatible dict."""
    
    def from_dict(cls, data: dict) -> AggregatedFeed:
        """Deserialize from JSON."""
```

---

## 8. Recommendation Event Schema (Input Contract)

The ORF profile's `event_log` will contain `SignedEvent`s with `op=SET_RECOMMENDATION` (new EventOp to be added).

Expected event payload structure:

```python
# event.op == EventOp.SET_RECOMMENDATION
# event.payload should contain:

{
    "entity_type": "movie",  # Required
    "external_id": "tt0133093",  # Required (unique per site for this entity)
    "title": "The Matrix",  # Required
    "description": "A hacker discovers reality is a simulation",  # Optional
    "thumbnail_url": "https://...",  # Optional
    "source_url": "https://imdb.com/title/tt0133093/",  # Optional
    "score": 8.5,  # Required (0-10)
    "score_source": "user_rating",  # Optional: "user_rating" | "algorithm" | "community"
    "confidence": 0.95,  # Optional: site's confidence in recommendation (0-1)
    "reason": "You rated similar movies highly",  # Optional
    "tags": ["sci-fi", "thriller", "action"],  # Required
    "source_site": "netflix",  # Optional (but recommended)
}
```

Sites send this via the ORF sync protocol; CLI/SDK handles wrapping it in a SignedEvent.

---

## 9. Summary: Design Decisions Rationale

| Decision | Rationale | Alternative Rejected |
|----------|-----------|----------------------|
| Consensus weight 0.5 | Multi-site agreement is most reliable signal | Heavier on affinity (user preferences drift) |
| Freshness weight 0.3 | Recency matters but not dominant (old hits still valuable) | Time-only decay without weighting |
| Affinity weight 0.2 | User interests matter, but can't assume topics predict rec quality | Heavier on affinity (ignores consensus) |
| 30-day half-life | Good balance: 1-month stale is half-strength, 3 months ≈ 10% | Fixed cutoff at 30 days (too harsh) |
| Max sources = 5 | Practical (most users sync 2-5 sites max); scales to 20 in future | Different per item (too complex) |
| Include cold-start items | User value from new discoveries; prevents echo chamber | Filter items with <2 sources (too restrictive) |
| Entity key format | Leverages sites' existing IDs; no external lookups | Fuzzy matching on title (error-prone) |
| Keep conflicting scores | Transparency: show Netflix 9/10 vs. other 2/10 | Average (loses important signal) |

---

This design is ready for implementation. Each test case is concrete enough to write directly as unit tests. The ranking algorithm is deterministic and auditable. De-duplication rules are clear. Cold-start is handled gracefully.
