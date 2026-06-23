from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


DEFAULT_TOP_N = 20
SITE_SCORE_WEIGHT = 0.7
TOPIC_AFFINITY_WEIGHT = 0.2
FRESHNESS_WEIGHT = 0.1
FRESHNESS_HALF_LIFE_HOURS = 24 * 7
FEEDBACK_SCORE_ADJUSTMENT_WEIGHT = 0.2
FEEDBACK_EXACT_MATCH_WEIGHT = 0.7
FEEDBACK_TOPIC_MATCH_WEIGHT = 0.3


class RankingFeedbackType(str, Enum):
    CLICK = "click"
    DISMISS = "dismiss"
    SAVE = "save"


FEEDBACK_EVENT_SIGNAL_WEIGHTS = {
    RankingFeedbackType.CLICK: 1.0,
    RankingFeedbackType.DISMISS: -2.0,
    RankingFeedbackType.SAVE: 2.0,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class RankingCandidateInput:
    """Candidate payload accepted by the grant-session reranker."""

    candidate_id: str
    site_score: float
    published_at: str | None = None
    candidate_topics: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RankingCandidateInput":
        candidate_id = str(data.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError("Each ranking candidate must include a non-empty candidate_id.")

        raw_site_score = data.get("site_score")
        if isinstance(raw_site_score, bool):
            raise ValueError("Candidate site_score must be a number between 0.0 and 1.0.")
        try:
            site_score = float(raw_site_score)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Candidate site_score must be a number between 0.0 and 1.0."
            ) from error
        if site_score < 0.0 or site_score > 1.0:
            raise ValueError("Candidate site_score must be between 0.0 and 1.0.")

        published_at = str(data["published_at"]) if data.get("published_at") is not None else None
        if published_at is not None and _parse_timestamp(published_at) is None:
            raise ValueError("Candidate published_at must be an ISO 8601 timestamp.")

        raw_candidate_topics = data.get("candidate_topics")
        if raw_candidate_topics is None:
            candidate_topics: tuple[str, ...] = ()
        elif isinstance(raw_candidate_topics, list):
            candidate_topics = tuple(
                dict.fromkeys(
                    str(topic).strip()
                    for topic in raw_candidate_topics
                    if str(topic).strip()
                )
            )
        else:
            raise ValueError("Candidate candidate_topics must be an array of topic strings.")

        raw_metadata = data.get("metadata")
        if raw_metadata is None:
            metadata: dict[str, Any] = {}
        elif isinstance(raw_metadata, Mapping):
            metadata = dict(raw_metadata)
        else:
            raise ValueError("Candidate metadata must be an object.")

        return cls(
            candidate_id=candidate_id,
            site_score=site_score,
            published_at=published_at,
            candidate_topics=candidate_topics,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class GrantSessionRankRequest:
    """Grant-session ranking request payload."""

    schema_version: str
    top_n: int
    include_debug: bool
    candidates: tuple[RankingCandidateInput, ...]

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        default_schema_version: str,
    ) -> "GrantSessionRankRequest":
        raw_candidates = data.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise ValueError("Ranking request candidates must be a non-empty array.")
        candidates = tuple(RankingCandidateInput.from_dict(item) for item in raw_candidates)

        raw_schema_version = data.get("schema_version", default_schema_version)
        schema_version = str(raw_schema_version).strip()
        if not schema_version:
            raise ValueError("Ranking request schema_version must be a non-empty string.")

        raw_top_n = data.get("top_n")
        if raw_top_n is None:
            top_n = min(DEFAULT_TOP_N, len(candidates))
        elif isinstance(raw_top_n, bool) or not isinstance(raw_top_n, int) or raw_top_n <= 0:
            raise ValueError("Ranking request top_n must be a positive integer.")
        else:
            top_n = raw_top_n

        raw_include_debug = data.get("include_debug", False)
        if not isinstance(raw_include_debug, bool):
            raise ValueError("Ranking request include_debug must be a boolean.")

        return cls(
            schema_version=schema_version,
            top_n=top_n,
            include_debug=raw_include_debug,
            candidates=candidates,
        )


@dataclass(frozen=True, slots=True)
class RankingFeedbackEvent:
    """Explicit site-local feedback recorded for future reranks."""

    event_id: str
    event_type: RankingFeedbackType
    candidate_id: str
    occurred_at: str
    candidate_topics: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RankingFeedbackEvent":
        event_id = str(data.get("event_id", "")).strip()
        if not event_id:
            raise ValueError("Each feedback event must include a non-empty event_id.")

        candidate_id = str(data.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError("Each feedback event must include a non-empty candidate_id.")

        try:
            event_type = RankingFeedbackType(str(data.get("event_type", "")).strip())
        except ValueError as error:
            allowed = ", ".join(sorted(item.value for item in RankingFeedbackType))
            raise ValueError(
                f"Feedback event_type must be one of: {allowed}."
            ) from error

        occurred_at = str(data.get("occurred_at", "")).strip() or _utc_now()
        if _parse_timestamp(occurred_at) is None:
            raise ValueError("Feedback occurred_at must be an ISO 8601 timestamp.")

        raw_candidate_topics = data.get("candidate_topics")
        if raw_candidate_topics is None:
            candidate_topics: tuple[str, ...] = ()
        elif isinstance(raw_candidate_topics, list):
            candidate_topics = tuple(
                dict.fromkeys(
                    str(topic).strip()
                    for topic in raw_candidate_topics
                    if str(topic).strip()
                )
            )
        else:
            raise ValueError("Feedback candidate_topics must be an array of topic strings.")

        raw_metadata = data.get("metadata")
        if raw_metadata is None:
            metadata: dict[str, Any] = {}
        elif isinstance(raw_metadata, Mapping):
            metadata = dict(raw_metadata)
        else:
            raise ValueError("Feedback metadata must be an object.")

        return cls(
            event_id=event_id,
            event_type=event_type,
            candidate_id=candidate_id,
            occurred_at=occurred_at,
            candidate_topics=candidate_topics,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "candidate_id": self.candidate_id,
            "occurred_at": self.occurred_at,
        }
        if self.candidate_topics:
            payload["candidate_topics"] = list(self.candidate_topics)
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True, slots=True)
class GrantSessionFeedbackRequest:
    """Feedback ingestion payload for a verified grant session."""

    schema_version: str
    events: tuple[RankingFeedbackEvent, ...]

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        default_schema_version: str,
    ) -> "GrantSessionFeedbackRequest":
        raw_events = data.get("events")
        if not isinstance(raw_events, list) or not raw_events:
            raise ValueError("Feedback request events must be a non-empty array.")

        raw_schema_version = data.get("schema_version", default_schema_version)
        schema_version = str(raw_schema_version).strip()
        if not schema_version:
            raise ValueError("Feedback request schema_version must be a non-empty string.")

        events = tuple(RankingFeedbackEvent.from_dict(item) for item in raw_events)
        return cls(schema_version=schema_version, events=events)


@dataclass(frozen=True, slots=True)
class GrantSessionFeedbackIngestResult:
    """Summary returned after feedback ingestion."""

    schema_version: str
    submitted_events: int
    accepted_events: int
    ingested_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "submitted_events": self.submitted_events,
            "accepted_events": self.accepted_events,
            "ingested_at": self.ingested_at,
        }


@dataclass(frozen=True, slots=True)
class GrantFeedbackSignals:
    """Internal site-local feedback summary used during reranking."""

    candidate_scores: dict[str, float] = field(default_factory=dict)
    topic_scores: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_events(cls, events: tuple[RankingFeedbackEvent, ...]) -> "GrantFeedbackSignals":
        candidate_scores: dict[str, float] = {}
        topic_scores: dict[str, float] = {}
        for event in events:
            signal = FEEDBACK_EVENT_SIGNAL_WEIGHTS[event.event_type]
            candidate_scores[event.candidate_id] = (
                candidate_scores.get(event.candidate_id, 0.0) + signal
            )
            for topic in set(event.candidate_topics):
                topic_scores[topic] = topic_scores.get(topic, 0.0) + signal
        return cls(candidate_scores=candidate_scores, topic_scores=topic_scores)

    def score(self, *, candidate_id: str, candidate_topics: tuple[str, ...]) -> float:
        if not self.candidate_scores and not self.topic_scores:
            return 0.5

        candidate_signal = self.candidate_scores.get(candidate_id, 0.0)
        matching_topic_scores = [
            self.topic_scores.get(topic, 0.0)
            for topic in set(candidate_topics)
            if topic in self.topic_scores
        ]
        topic_signal = (
            sum(matching_topic_scores) / len(matching_topic_scores)
            if matching_topic_scores
            else 0.0
        )
        raw_signal = (
            FEEDBACK_EXACT_MATCH_WEIGHT * candidate_signal
            + FEEDBACK_TOPIC_MATCH_WEIGHT * topic_signal
        )
        return min(1.0, max(0.0, 0.5 + 0.5 * math.tanh(raw_signal / 3.0)))


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """Site candidate after grant-session reranking."""

    candidate_id: str
    score: float
    rank: int
    reason_codes: tuple[str, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self, *, include_debug: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "score": round(self.score, 6),
            "rank": self.rank,
            "reason_codes": list(self.reason_codes),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        if include_debug:
            payload["breakdown"] = {
                key: round(value, 6)
                for key, value in self.breakdown.items()
            }
        return payload


@dataclass(frozen=True, slots=True)
class GrantSessionRankingResult:
    """Serialized reranking result for one verified grant session."""

    schema_version: str
    site_id: str
    grant_id: str
    candidate_count: int
    top_n: int
    ranked_candidates: tuple[RankedCandidate, ...]
    reranked_at: str

    def to_dict(self, *, include_debug: bool) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "site_id": self.site_id,
            "grant_id": self.grant_id,
            "candidate_count": self.candidate_count,
            "top_n": self.top_n,
            "reranked_at": self.reranked_at,
            "ranked_candidates": [
                candidate.to_dict(include_debug=include_debug)
                for candidate in self.ranked_candidates
            ],
        }


class GrantSessionRanker:
    """Reranks site candidates using consented grant-session topic signals."""

    def __init__(
        self,
        granted_topic_weights: Mapping[str, float],
        *,
        feedback_signals: GrantFeedbackSignals | None = None,
    ) -> None:
        self.granted_topic_weights = {
            str(topic): max(0.0, float(weight))
            for topic, weight in granted_topic_weights.items()
        }
        self.feedback_signals = feedback_signals or GrantFeedbackSignals()

    def _topic_affinity(self, candidate_topics: tuple[str, ...]) -> float:
        if not candidate_topics or not self.granted_topic_weights:
            return 0.5

        total_weight = sum(self.granted_topic_weights.values())
        if total_weight <= 0.0:
            return 0.5

        overlap_weight = sum(
            self.granted_topic_weights.get(topic, 0.0)
            for topic in set(candidate_topics)
        )
        return min(1.0, max(0.0, overlap_weight / total_weight))

    def _freshness_score(self, published_at: str | None) -> float:
        if published_at is None:
            return 0.5

        parsed = _parse_timestamp(published_at)
        if parsed is None:
            return 0.5

        now = datetime.now(timezone.utc)
        hours_ago = max(0.0, (now - parsed).total_seconds() / 3600)
        boost = pow(2.0, -(hours_ago / FRESHNESS_HALF_LIFE_HOURS))
        return min(1.0, max(0.0, boost))

    def _reason_codes(
        self,
        *,
        candidate: RankingCandidateInput,
        topic_affinity: float,
        freshness: float,
        feedback_affinity: float,
    ) -> tuple[str, ...]:
        codes: list[str] = []
        if candidate.site_score >= 0.75:
            codes.append("site-score-strong")
        elif candidate.site_score >= 0.45:
            codes.append("site-score-medium")
        else:
            codes.append("site-score-weak")

        if candidate.candidate_topics:
            if topic_affinity >= 0.75:
                codes.append("topic-affinity-strong")
            elif topic_affinity >= 0.3:
                codes.append("topic-affinity-some")
            else:
                codes.append("topic-affinity-none")
        else:
            codes.append("candidate-topics-missing")

        if candidate.published_at is not None:
            if freshness >= 0.75:
                codes.append("freshness-recent")
            elif freshness <= 0.35:
                codes.append("freshness-stale")

        if feedback_affinity >= 0.65:
            codes.append("feedback-positive")
        elif feedback_affinity <= 0.35:
            codes.append("feedback-negative")

        return tuple(codes)

    def rank(
        self,
        ranking_request: GrantSessionRankRequest,
        *,
        site_id: str,
        grant_id: str,
    ) -> GrantSessionRankingResult:
        ranked_candidates: list[tuple[RankingCandidateInput, float, float, float, float]] = []
        for candidate in ranking_request.candidates:
            topic_affinity = self._topic_affinity(candidate.candidate_topics)
            freshness = self._freshness_score(candidate.published_at)
            feedback_affinity = self.feedback_signals.score(
                candidate_id=candidate.candidate_id,
                candidate_topics=candidate.candidate_topics,
            )
            base_score = (
                SITE_SCORE_WEIGHT * candidate.site_score
                + TOPIC_AFFINITY_WEIGHT * topic_affinity
                + FRESHNESS_WEIGHT * freshness
            )
            score = min(
                1.0,
                max(
                    0.0,
                    base_score
                    + FEEDBACK_SCORE_ADJUSTMENT_WEIGHT * ((2.0 * feedback_affinity) - 1.0),
                ),
            )
            ranked_candidates.append(
                (candidate, score, topic_affinity, freshness, feedback_affinity)
            )

        ranked_candidates.sort(
            key=lambda item: (-item[1], -item[0].site_score, item[0].candidate_id)
        )

        effective_top_n = min(ranking_request.top_n, len(ranked_candidates))
        serialized_candidates: list[RankedCandidate] = []
        for index, (candidate, score, topic_affinity, freshness, feedback_affinity) in enumerate(
            ranked_candidates[:effective_top_n],
            start=1,
        ):
            serialized_candidates.append(
                RankedCandidate(
                    candidate_id=candidate.candidate_id,
                    score=score,
                    rank=index,
                    reason_codes=self._reason_codes(
                        candidate=candidate,
                        topic_affinity=topic_affinity,
                        freshness=freshness,
                        feedback_affinity=feedback_affinity,
                    ),
                    metadata=dict(candidate.metadata),
                    breakdown={
                        "site_score": candidate.site_score,
                        "topic_affinity": topic_affinity,
                        "freshness": freshness,
                        "feedback_affinity": feedback_affinity,
                    },
                )
            )

        return GrantSessionRankingResult(
            schema_version=ranking_request.schema_version,
            site_id=site_id,
            grant_id=grant_id,
            candidate_count=len(ranking_request.candidates),
            top_n=effective_top_n,
            ranked_candidates=tuple(serialized_candidates),
            reranked_at=_utc_now(),
        )
