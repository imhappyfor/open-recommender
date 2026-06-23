from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


class RecommendationEvent(Protocol):
    """Recommendation event shape consumed by the aggregator."""

    op: str
    payload: Mapping[str, Any]
    timestamp: str


class TopicPreferenceLike(Protocol):
    """Topic preference shape consumed by affinity scoring."""

    weight: float


class RecommenderProfile(Protocol):
    """Profile shape required by the recommender."""

    profile_id: str
    event_log: list[RecommendationEvent]
    topics: Mapping[str, TopicPreferenceLike]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class RecommendationItem:
    """A recommendation from a site, with score and metadata."""

    item_id: str
    site_id: str
    site_name: str
    score: float
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "site_id": self.site_id,
            "site_name": self.site_name,
            "score": self.score,
            "reason": self.reason,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


@dataclass(slots=True)
class AggregatedRecommendation:
    """An aggregated recommendation from multiple sites."""

    item_id: str
    consensus_score: float
    freshness_boost: float
    affinity_boost: float
    final_score: float
    sources: list[RecommendationItem]
    best_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "consensus_score": self.consensus_score,
            "freshness_boost": self.freshness_boost,
            "affinity_boost": self.affinity_boost,
            "final_score": self.final_score,
            "sources": [source.to_dict() for source in self.sources],
            "best_metadata": self.best_metadata,
        }


class AggregatedFeed:
    """
    Aggregates recommendations from multiple sites into a ranked feed.

    Algorithm:
    1. Extract all RECOMMEND events from the profile's event log
    2. Group by item_id (de-duplication)
    3. For each item, compute:
       - Consensus score: (# sites recommending) / (max # sites) normalized 0-1
       - Freshness boost: exponential decay from timestamp (recent = 1.0, older = lower)
       - Affinity boost: if item matches high-weight topics in user's profile, boost it
    4. Final score: 0.4*consensus + 0.3*freshness + 0.3*affinity (default weights)
    5. Return top N items sorted by final_score descending

    Feeds are computed on-demand from event_log and not stored in the .orf file.
    """

    def __init__(self, profile: RecommenderProfile):
        self.profile = profile
        self._recommendations: dict[str, list[RecommendationItem]] = {}
        self._extract_recommendations()

    def _extract_recommendations(self) -> None:
        """Extract all RECOMMEND events from the profile's event log."""
        for event in self.profile.event_log:
            if event.op == "recommend":
                payload = event.payload
                item = RecommendationItem(
                    item_id=str(payload.get("item_id", "")),
                    site_id=str(payload.get("site_id", "")),
                    site_name=str(payload.get("site_name", "")),
                    score=float(payload.get("score", 0.5)),
                    reason=payload.get("reason"),
                    metadata=payload.get("metadata", {}),
                    timestamp=payload.get("timestamp", event.timestamp),
                )
                if item.item_id:
                    if item.item_id not in self._recommendations:
                        self._recommendations[item.item_id] = []
                    self._recommendations[item.item_id].append(item)

    def _compute_consensus_score(
        self,
        sources: list[RecommendationItem],
        total_sites: int,
    ) -> float:
        """Consensus: normalized by number of unique sites recommending."""
        if not sources or total_sites == 0:
            return 0.0
        unique_sites = len({source.site_id for source in sources})
        return min(1.0, unique_sites / max(1, total_sites))

    def _compute_freshness_boost(self, items: list[RecommendationItem]) -> float:
        """
        Freshness: exponential decay from most recent timestamp.
        Most recent item = 1.0, older items decay towards 0.
        Formula: exp(-(hours_since_now / half_life))
        Default half-life: 7 days (168 hours).
        """
        if not items or not items[0].timestamp:
            return 0.5

        try:
            now = datetime.now(timezone.utc)
            most_recent_str = max(item.timestamp for item in items if item.timestamp)
            most_recent = datetime.fromisoformat(most_recent_str.replace("Z", "+00:00"))
            hours_ago = (now - most_recent).total_seconds() / 3600
            half_life = 168
            boost = pow(2.0, -(hours_ago / half_life))
            return min(1.0, max(0.0, boost))
        except (ValueError, AttributeError):
            return 0.5

    def _compute_affinity_boost(self, item_id: str) -> float:
        """
        Affinity: if item_id matches high-weight topics in the user's profile, boost it.
        Checks if the topic namespace or path appears in the item_id.
        For "orf:technology/python", checks for "technology", "python", and full topic.
        """
        if not self.profile.topics:
            return 0.5

        high_weight_topics = {
            topic for topic, preference in self.profile.topics.items() if preference.weight > 0.5
        }
        if not high_weight_topics:
            return 0.5

        item_lower = item_id.lower()
        matches = 0
        for topic in high_weight_topics:
            if topic.lower() in item_lower:
                matches += 1
                continue

            segments = topic.replace(":", "/").split("/")
            for segment in segments:
                if segment and segment in item_lower:
                    matches += 1
                    break

        return min(1.0, 0.5 + (matches / len(high_weight_topics)) * 0.5)

    def aggregate(
        self,
        *,
        consensus_weight: float = 0.4,
        freshness_weight: float = 0.3,
        affinity_weight: float = 0.3,
    ) -> list[AggregatedRecommendation]:
        """Aggregate and rank recommendations."""
        aggregated: list[AggregatedRecommendation] = []
        total_sites = len(
            {
                source.site_id
                for sources in self._recommendations.values()
                for source in sources
            }
        )

        for item_id, sources in self._recommendations.items():
            consensus = self._compute_consensus_score(sources, total_sites or 1)
            freshness = self._compute_freshness_boost(sources)
            affinity = self._compute_affinity_boost(item_id)
            final_score = (
                consensus_weight * consensus
                + freshness_weight * freshness
                + affinity_weight * affinity
            )
            best_source = max(sources, key=lambda source: source.score)
            aggregated.append(
                AggregatedRecommendation(
                    item_id=item_id,
                    consensus_score=consensus,
                    freshness_boost=freshness,
                    affinity_boost=affinity,
                    final_score=final_score,
                    sources=sources,
                    best_metadata=best_source.metadata or {},
                )
            )

        aggregated.sort(key=lambda recommendation: recommendation.final_score, reverse=True)
        return aggregated

    def top_n(
        self,
        n: int = 20,
        *,
        consensus_weight: float = 0.4,
        freshness_weight: float = 0.3,
        affinity_weight: float = 0.3,
    ) -> list[AggregatedRecommendation]:
        """Return top N aggregated recommendations."""
        return self.aggregate(
            consensus_weight=consensus_weight,
            freshness_weight=freshness_weight,
            affinity_weight=affinity_weight,
        )[:n]

    def to_dict(self) -> dict[str, Any]:
        """Serialize aggregated feed to dict."""
        recommendations = self.aggregate()
        return {
            "profile_id": self.profile.profile_id,
            "aggregated_at": _utc_now(),
            "total_items": len(recommendations),
            "recommendations": [recommendation.to_dict() for recommendation in recommendations],
        }
