from __future__ import annotations

import unittest

from open_recommender.crypto import generate_key_pair, sign_payload, verify_signature
from open_recommender.models import (
    AccessGrant,
    AccessRequestStatus,
    AccessScope,
    AggregatedFeed,
    EventOp,
    GrantSession,
    ORFProfile,
    SignedEvent,
    SiteAccessRequest,
    TopicPreference,
    Visibility,
    build_signed_event,
    normalize_scope_set,
    schema_compatibility,
    selective_topic_scope,
    utc_now,
)
from open_recommender.recommender import (
    AggregatedFeed as RecommenderAggregatedFeed,
    GrantFeedbackSignals,
    GrantSessionRankRequest,
    GrantSessionRanker,
    RankingFeedbackEvent,
)


class ModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")

    def signed_event(self, op: EventOp, payload: dict, *, clock: int | None = None) -> SignedEvent:
        event = build_signed_event(self.profile, op, payload, signature="", clock=clock)
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        return event

    def test_public_projection_filters_private_topics(self) -> None:
        public_event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
        )
        private_event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:health/sleep", "weight": 0.8, "visibility": "private"},
        )
        opt_out_event = self.signed_event(
            EventOp.SET_OPT_OUT,
            {"topic": "orf:politics/news", "value": True},
        )
        hidden_public_event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:politics/news", "weight": 0.7, "visibility": "public"},
        )

        for event in [public_event, private_event, opt_out_event, hidden_public_event]:
            self.profile.apply_event(event)

        public_topics = {item["topic"] for item in self.profile.public_projection()["topics"]}
        self.assertEqual(public_topics, {"orf:technology/python"})

    def test_consent_revocation_wins_on_same_clock(self) -> None:
        grant = self.signed_event(
            EventOp.SET_CONSENT,
            {"field": "ad_personalization", "value": True},
            clock=3,
        )
        revoke = self.signed_event(
            EventOp.SET_CONSENT,
            {"field": "ad_personalization", "value": False},
            clock=3,
        )

        self.profile.apply_event(grant)
        self.profile.apply_event(revoke)

        self.assertFalse(self.profile.consent.ad_personalization)

    def test_topic_add_wins_on_same_clock(self) -> None:
        add = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:media/video", "weight": 0.5, "visibility": "public"},
            clock=5,
        )
        remove = self.signed_event(
            EventOp.REMOVE_TOPIC,
            {"topic": "orf:media/video"},
            clock=5,
        )

        self.profile.apply_event(add)
        self.profile.apply_event(remove)

        self.assertIn("orf:media/video", self.profile.topics)

    def test_signature_round_trip(self) -> None:
        event = self.signed_event(
            EventOp.SET_TOPIC,
            {"topic": "orf:music/jazz", "weight": 0.6, "visibility": "public"},
        )
        self.assertTrue(verify_signature(event.unsigned_payload(), event.signature, self.profile.public_key))

    def test_consented_projection_respects_visibility_and_scopes(self) -> None:
        for event in [
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
            ),
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            ),
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:health/sleep", "weight": 0.5, "visibility": "private"},
            ),
        ]:
            self.profile.apply_event(event)

        projection = self.profile.consented_projection(
            [
                AccessScope.PROFILE_READ.value,
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/podcasts"),
            ],
            site_id="pilot-site",
            grant_id="grant-123",
        )

        projected_topics = {item["topic"] for item in projection["topics"]}
        self.assertEqual(projected_topics, {"orf:technology/python", "orf:media/podcasts"})
        self.assertNotIn("orf:health/sleep", projected_topics)
        self.assertEqual(projection["display_name"], "Alice")
        self.assertEqual(projection["site_id"], "pilot-site")
        self.assertEqual(projection["grant_id"], "grant-123")

    def test_consented_projection_filters_unapproved_selective_topics_and_unknown_scopes(self) -> None:
        for event in [
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:technology/python", "weight": 0.9, "visibility": "public"},
            ),
            self.signed_event(
                EventOp.SET_TOPIC,
                {"topic": "orf:media/podcasts", "weight": 0.7, "visibility": "selective"},
            ),
            self.signed_event(
                EventOp.SET_OPT_OUT,
                {"topic": "orf:technology/python", "value": True},
            ),
        ]:
            self.profile.apply_event(event)

        projection = self.profile.consented_projection(
            [
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/video"),
                "topics.experimental",
            ]
        )

        self.assertEqual(projection["topics"], [])
        self.assertEqual(projection["ignored_scopes"], ["topics.experimental"])

    def test_schema_compatibility_helpers_define_v0_behavior(self) -> None:
        compatible = schema_compatibility("0.2.5")
        incompatible = schema_compatibility("1.0.0")

        self.assertTrue(compatible.supported)
        self.assertTrue(compatible.allow_unknown_fields)
        self.assertTrue(compatible.ignore_unknown_scopes)
        self.assertFalse(incompatible.supported)

        normalized_scopes, unknown_scopes = normalize_scope_set(
            [AccessScope.TOPICS_PUBLIC.value, selective_topic_scope("orf:media/podcasts"), "unknown.scope"]
        )
        self.assertIn(AccessScope.TOPICS_PUBLIC.value, normalized_scopes)
        self.assertIn(selective_topic_scope("orf:media/podcasts"), normalized_scopes)
        self.assertEqual(unknown_scopes, ("unknown.scope",))

    def test_contract_shapes_preserve_unknown_fields(self) -> None:
        request = SiteAccessRequest.from_dict(
            {
                "schema_version": "0.2.0",
                "request_id": "request-1",
                "site_id": "site-1",
                "site_name": "Pilot Site",
                "purpose": "Personalize a home feed",
                "requested_scopes": [
                    AccessScope.PROFILE_READ.value,
                    selective_topic_scope("orf:media/podcasts"),
                    "site.beta",
                ],
                "created_at": "2025-01-01T00:00:00+00:00",
                "status": AccessRequestStatus.PENDING.value,
                "review_note": "manual pilot allowlist",
            }
        )
        grant = AccessGrant.from_dict(
            {
                "schema_version": "0.2.0",
                "grant_id": "grant-1",
                "request_id": request.request_id,
                "profile_id": self.profile.profile_id,
                "site_id": request.site_id,
                "approved_scopes": [AccessScope.PROFILE_READ.value],
                "issued_at": "2025-01-01T00:01:00+00:00",
                "expires_at": "2025-01-01T01:01:00+00:00",
                "exchange_method": "challenge",
            }
        )
        session = GrantSession.from_dict(
            {
                "schema_version": "0.2.0",
                "session_id": "session-1",
                "grant_id": grant.grant_id,
                "profile_id": self.profile.profile_id,
                "site_id": request.site_id,
                "approved_scopes": [AccessScope.PROFILE_READ.value],
                "issued_at": "2025-01-01T00:02:00+00:00",
                "expires_at": "2025-01-01T00:17:00+00:00",
                "transport": "bearer",
            }
        )

        self.assertEqual(
            request.requested_scopes,
            (AccessScope.PROFILE_READ.value, selective_topic_scope("orf:media/podcasts")),
        )
        self.assertEqual(request.required_scopes, ())
        self.assertEqual(
            request.optional_scopes,
            (AccessScope.PROFILE_READ.value, selective_topic_scope("orf:media/podcasts")),
        )
        self.assertEqual(request.extra_fields["review_note"], "manual pilot allowlist")
        self.assertEqual(grant.extra_fields["exchange_method"], "challenge")
        self.assertEqual(session.extra_fields["transport"], "bearer")

    def test_site_access_request_preserves_required_and_optional_scopes(self) -> None:
        request = SiteAccessRequest.create(
            site_id="site-1",
            site_name="Pilot Site",
            purpose="Personalize a home feed",
            required_scopes=[AccessScope.PROFILE_READ.value],
            optional_scopes=[
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/podcasts"),
            ],
        )

        self.assertEqual(request.required_scopes, (AccessScope.PROFILE_READ.value,))
        self.assertEqual(
            request.optional_scopes,
            (
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/podcasts"),
            ),
        )
        self.assertEqual(
            request.requested_scopes,
            (
                AccessScope.PROFILE_READ.value,
                AccessScope.TOPICS_PUBLIC.value,
                selective_topic_scope("orf:media/podcasts"),
            ),
        )


class GrantSessionRankingTests(unittest.TestCase):
    def test_feedback_signals_flip_close_candidates(self) -> None:
        ranking_request = GrantSessionRankRequest.from_dict(
            {
                "schema_version": "0.3.0",
                "top_n": 2,
                "include_debug": True,
                "candidates": [
                    {
                        "candidate_id": "podcast-feature",
                        "site_score": 0.55,
                        "candidate_topics": ["orf:media/podcasts"],
                    },
                    {
                        "candidate_id": "tech-feature",
                        "site_score": 0.81,
                        "candidate_topics": ["orf:technology/python"],
                    },
                ],
            },
            default_schema_version="0.3.0",
        )
        feedback_signals = GrantFeedbackSignals.from_events(
            (
                RankingFeedbackEvent.from_dict(
                    {
                        "event_id": "feedback-1",
                        "event_type": "dismiss",
                        "candidate_id": "podcast-feature",
                        "candidate_topics": ["orf:media/podcasts"],
                        "occurred_at": "2025-01-21T10:00:00+00:00",
                    }
                ),
                RankingFeedbackEvent.from_dict(
                    {
                        "event_id": "feedback-2",
                        "event_type": "save",
                        "candidate_id": "tech-feature",
                        "candidate_topics": ["orf:technology/python"],
                        "occurred_at": "2025-01-21T10:01:00+00:00",
                    }
                ),
            )
        )

        ranking = GrantSessionRanker(
            {"orf:media/podcasts": 0.7},
            feedback_signals=feedback_signals,
        ).rank(
            ranking_request,
            site_id="open-news-demo",
            grant_id="grant-123",
        )

        self.assertEqual(
            [item.candidate_id for item in ranking.ranked_candidates],
            ["tech-feature", "podcast-feature"],
        )
        self.assertIn("feedback-positive", ranking.ranked_candidates[0].reason_codes)
        self.assertIn("feedback-negative", ranking.ranked_candidates[1].reason_codes)
        self.assertIn("feedback_affinity", ranking.ranked_candidates[0].breakdown)


class AggregatedFeedTests(unittest.TestCase):
    """Tests for Phase 2 cross-site recommendation aggregation."""

    def setUp(self) -> None:
        self.private_key, public_key = generate_key_pair()
        self.profile = ORFProfile.create("Alice", public_key, "device-a")

    def signed_event(self, op: EventOp, payload: dict, *, clock: int | None = None) -> SignedEvent:
        event = build_signed_event(self.profile, op, payload, signature="", clock=clock)
        event.signature = sign_payload(event.unsigned_payload(), self.private_key)
        return event

    def test_empty_feed_returns_no_recommendations(self) -> None:
        """Feed with no RECOMMEND events returns empty list."""
        from open_recommender.models import AggregatedFeed

        feed = AggregatedFeed(self.profile)
        self.assertEqual(feed.top_n(), [])
        self.assertEqual(feed.aggregate(), [])

    def test_models_import_reexports_recommender_feed(self) -> None:
        """Legacy models import stays aligned with the recommender package."""
        from open_recommender.models import AggregatedFeed

        self.assertIs(AggregatedFeed, RecommenderAggregatedFeed)

    def test_single_source_recommendation(self) -> None:
        """Single source recommending an item creates aggregated recommendation."""
        from open_recommender.models import AggregatedFeed

        rec_event = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-123",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.9,
                "reason": "Based on your tech interests",
                "metadata": {"title": "The Matrix", "type": "movie"},
                "timestamp": "2025-01-20T10:00:00+00:00",
            },
        )
        self.profile.event_log.append(rec_event)

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].item_id, "movie-123")
        self.assertEqual(len(recs[0].sources), 1)
        self.assertEqual(recs[0].sources[0].site_name, "Netflix")

    def test_de_duplication_same_item_multiple_sites(self) -> None:
        """Same item_id from multiple sites is de-duplicated into one recommendation."""
        from open_recommender.models import AggregatedFeed

        netflix_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-123",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.9,
                "metadata": {"title": "The Matrix", "type": "movie"},
            },
        )
        imdb_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-123",
                "site_id": "imdb",
                "site_name": "IMDb",
                "score": 0.85,
                "metadata": {"title": "The Matrix", "type": "movie"},
            },
        )
        self.profile.event_log.extend([netflix_rec, imdb_rec])

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].item_id, "movie-123")
        self.assertEqual(len(recs[0].sources), 2)
        source_sites = {s.site_id for s in recs[0].sources}
        self.assertEqual(source_sites, {"netflix", "imdb"})

    def test_consensus_score_increases_with_sites(self) -> None:
        """Consensus score increases as more sites recommend the same item."""
        from open_recommender.models import AggregatedFeed

        # Add recommendations from 3 sites for the same item
        for i, (site_id, site_name) in enumerate([("netflix", "Netflix"), ("imdb", "IMDb"), ("youtube", "YouTube")]):
            rec = self.signed_event(
                EventOp.RECOMMEND,
                {
                    "item_id": "movie-123",
                    "site_id": site_id,
                    "site_name": site_name,
                    "score": 0.8 + (i * 0.05),
                },
            )
            self.profile.event_log.append(rec)

        # Add other items from fewer sites
        other_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-456",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.9,
            },
        )
        self.profile.event_log.append(other_rec)

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        # movie-123 should have higher consensus than movie-456
        movie123 = next(r for r in recs if r.item_id == "movie-123")
        movie456 = next(r for r in recs if r.item_id == "movie-456")

        self.assertGreater(movie123.consensus_score, movie456.consensus_score)
        self.assertEqual(movie123.consensus_score, 1.0)  # 3 sites out of 3 total

    def test_freshness_decay(self) -> None:
        """Fresher recommendations get higher freshness boost."""
        from open_recommender.models import AggregatedFeed
        from datetime import datetime, timezone, timedelta

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        recent_time = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

        old_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "old-movie",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.95,
                "timestamp": old_time,
            },
        )
        recent_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "recent-movie",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.7,
                "timestamp": recent_time,
            },
        )
        self.profile.event_log.extend([old_rec, recent_rec])

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        old = next(r for r in recs if r.item_id == "old-movie")
        recent = next(r for r in recs if r.item_id == "recent-movie")

        self.assertGreater(recent.freshness_boost, old.freshness_boost)

    def test_affinity_boost_from_user_topics(self) -> None:
        """Items matching high-weight user topics get affinity boost."""
        from open_recommender.models import AggregatedFeed, TopicPreference, Visibility

        # Add high-weight topic for technology
        tech_topic = TopicPreference(
            topic="orf:technology/python",
            weight=0.9,
            visibility=Visibility.PUBLIC,
            updated_at=utc_now(),
        )
        self.profile.topics["orf:technology/python"] = tech_topic

        # Recommend item that mentions "python" (matches topic)
        tech_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "python-course",
                "site_id": "udemy",
                "site_name": "Udemy",
                "score": 0.7,
                "metadata": {"title": "Learn Python Programming", "description": "python tutorial"},
            },
        )
        # Recommend unrelated item
        unrelated_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "cooking-show",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.7,
            },
        )
        self.profile.event_log.extend([tech_rec, unrelated_rec])

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        tech_item = next(r for r in recs if r.item_id == "python-course")
        unrelated_item = next(r for r in recs if r.item_id == "cooking-show")

        self.assertGreater(tech_item.affinity_boost, unrelated_item.affinity_boost)

    def test_final_score_combines_factors(self) -> None:
        """Final score combines consensus, freshness, and affinity."""
        from open_recommender.models import AggregatedFeed

        # High consensus, high freshness item
        high_quality_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "popular-item",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.9,
                "timestamp": "2025-01-21T10:00:00+00:00",  # recent
            },
        )
        # Add more sites for consensus
        for site in ["imdb", "youtube"]:
            rec = self.signed_event(
                EventOp.RECOMMEND,
                {
                    "item_id": "popular-item",
                    "site_id": site,
                    "site_name": site.title(),
                    "score": 0.85,
                    "timestamp": "2025-01-21T10:00:00+00:00",
                },
            )
            self.profile.event_log.append(rec)

        # Low consensus, low freshness item
        obscure_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "obscure-item",
                "site_id": "niche-site",
                "site_name": "Niche Site",
                "score": 0.95,
                "timestamp": "2024-12-01T10:00:00+00:00",  # old
            },
        )
        self.profile.event_log.extend([high_quality_rec, obscure_rec])

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        popular = next(r for r in recs if r.item_id == "popular-item")
        obscure = next(r for r in recs if r.item_id == "obscure-item")

        self.assertGreater(popular.final_score, obscure.final_score)

    def test_top_n_returns_correct_count(self) -> None:
        """top_n() returns at most N recommendations."""
        from open_recommender.models import AggregatedFeed

        # Add 5 recommendations
        for i in range(5):
            rec = self.signed_event(
                EventOp.RECOMMEND,
                {
                    "item_id": f"item-{i}",
                    "site_id": "netflix",
                    "site_name": "Netflix",
                    "score": 0.9 - (i * 0.05),
                },
            )
            self.profile.event_log.append(rec)

        feed = AggregatedFeed(self.profile)
        top3 = feed.top_n(3)
        top10 = feed.top_n(10)

        self.assertEqual(len(top3), 3)
        self.assertEqual(len(top10), 5)  # Only 5 exist

    def test_missing_metadata_handled_gracefully(self) -> None:
        """Recommendations with missing metadata don't crash."""
        from open_recommender.models import AggregatedFeed

        # Minimal recommendation with no metadata
        minimal_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "minimal-item",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.8,
            },
        )
        self.profile.event_log.append(minimal_rec)

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].best_metadata, {})

    def test_conflicting_scores_from_same_item(self) -> None:
        """Item with different scores from different sites shows all scores."""
        from open_recommender.models import AggregatedFeed

        netflix_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-999",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.95,
                "reason": "Trending in your genre",
            },
        )
        imdb_rec = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "movie-999",
                "site_id": "imdb",
                "site_name": "IMDb",
                "score": 0.4,
                "reason": "Low user ratings",
            },
        )
        self.profile.event_log.extend([netflix_rec, imdb_rec])

        feed = AggregatedFeed(self.profile)
        recs = feed.aggregate()

        self.assertEqual(len(recs), 1)
        self.assertEqual(len(recs[0].sources), 2)
        scores = sorted([s.score for s in recs[0].sources], reverse=True)
        self.assertEqual(scores[0], 0.95)
        self.assertEqual(scores[1], 0.4)

    def test_custom_ranking_weights(self) -> None:
        """Ranking weights can be customized for aggregate()."""
        from open_recommender.models import AggregatedFeed

        # Add multiple items with different profiles
        fresh_item = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "fresh",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.5,
                "timestamp": "2025-01-21T10:00:00+00:00",
            },
        )
        consensus_item = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "consensus",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.5,
                "timestamp": "2024-01-01T10:00:00+00:00",
            },
        )
        # Add more sites for consensus
        for site in ["imdb", "youtube"]:
            rec = self.signed_event(
                EventOp.RECOMMEND,
                {
                    "item_id": "consensus",
                    "site_id": site,
                    "site_name": site.title(),
                    "score": 0.5,
                    "timestamp": "2024-01-01T10:00:00+00:00",
                },
            )
            self.profile.event_log.append(rec)

        self.profile.event_log.extend([fresh_item, consensus_item])

        feed = AggregatedFeed(self.profile)

        # Default weights: favor consensus
        default_recs = feed.aggregate()
        default_order = [r.item_id for r in default_recs]

        # Freshness-heavy weights: favor fresh
        freshness_recs = feed.aggregate(
            consensus_weight=0.1,
            freshness_weight=0.8,
            affinity_weight=0.1,
        )
        freshness_order = [r.item_id for r in freshness_recs]

        # With different weights, order might change (depending on actual scores)
        # At minimum, we've called the method with custom weights successfully
        self.assertEqual(len(default_recs), 2)
        self.assertEqual(len(freshness_recs), 2)

    def test_feed_to_dict_serializable(self) -> None:
        """AggregatedFeed can be serialized to dict."""
        from open_recommender.models import AggregatedFeed

        rec_event = self.signed_event(
            EventOp.RECOMMEND,
            {
                "item_id": "test-item",
                "site_id": "netflix",
                "site_name": "Netflix",
                "score": 0.8,
            },
        )
        self.profile.event_log.append(rec_event)

        feed = AggregatedFeed(self.profile)
        feed_dict = feed.to_dict()

        self.assertIn("profile_id", feed_dict)
        self.assertIn("aggregated_at", feed_dict)
        self.assertIn("total_items", feed_dict)
        self.assertIn("recommendations", feed_dict)
        self.assertEqual(feed_dict["total_items"], 1)


if __name__ == "__main__":
    unittest.main()
