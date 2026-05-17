# Phase 2: Cross-Site Recommendation Feed

**Phase goal:** Prove that portability is not overhead—it is the competitive advantage. Users come back daily because the feed proves the `.orf` format delivers value.

**Timeline:** 4-6 weeks (implementation) + 1-2 weeks (pilot validation).

**Definition of done:** At least one pilot site integrates; users see a unified feed aggregating recommendations from 2+ platforms; retention metrics show daily-active-user improvement over baseline.

---

## What We're Building

A **cross-site recommendation feed** that aggregates, de-duplicates, and ranks recommendations from all sites a user has synced with.

### User experience

1. **User syncs two sites:** signs into Platform A with their `.orf` file, approves access to music/tech topics. Then signs into Platform B, approves access to the same topics.
2. **Opens the feed dashboard:** Open Recommender CLI or trust app shows a unified feed: "Here's what Netflix, your indie podcast app, and Medium all recommend."
3. **Sees de-duplication:** The same movie recommended by both Netflix and IMDb appears once, ranked higher (consensus).
4. **Sees ranking transparency:** Each item shows which sites recommended it, and optionally the recommendation reason.
5. **Navigates to a site:** Clicks on a recommendation, goes to that site to consume it. The site's local recommendations are enhanced by the same logic.

### Technical architecture

**Data flow:**

1. **Event sync (existing):** Sites push events to the user's `.orf` file (via hosted service or local CLI). The `.orf` file contains all events from all sites in a single append-only log.

2. **Feed aggregation (new):** When the user opens the feed dashboard:
   - Read all events from the `.orf` file
   - Group events by site and event type (recommendations, rating, etc.)
   - Aggregate recommendations: if multiple sites recommend the same item, collect their confidence scores
   - Rank by:
     - **Consensus factor**: number of sites that recommend this item (normalized 0-1)
     - **Freshness**: newer events rank higher
     - **User preference affinity**: if the item matches a high-weight topic in the user's profile, boost it
   - De-duplicate: same item ID across sites = one feed entry
   - Return top N items (e.g., 20)

3. **Feed rendering:** CLI or web UI displays the aggregated feed with:
   - Item metadata (title, description, thumbnail if available)
   - Which sites recommended it + their reason (if available)
   - One-click navigation to any synced site

---

## Phase 2 Features (Priority Order)

### 🥇 Core: Unified Feed Aggregation

**Description:** Aggregate recommendations from synced sites into one ranked feed.

**Acceptance criteria:**
- [ ] User syncs 2+ sites
- [ ] CLI command `open-recommender feed show profile.orf [--top-n=20]` returns JSON list of aggregated recommendations
- [ ] Each item shows which sites recommended it + consensus score
- [ ] De-duplicates same item recommended by multiple sites
- [ ] Ranks by consensus + freshness + user affinity
- [ ] Tests cover merge rules and ranking logic

**Implementation notes:**
- Feed aggregation is stateless (reads from `.orf` events only)
- No new database needed for the reference implementation
- SDK sites don't need to change; they already push events
- Feed logic can live in `models.py` as a new `AggregatedFeed` class

**Estimated effort:** 2 weeks

---

### 🥈 Dashboard UI (Web or CLI)

**Description:** Visual interface to browse the feed.

**Acceptance criteria:**
- [ ] Web or CLI UI shows top recommendations
- [ ] Each recommendation shows which sites suggested it + metadata
- [ ] One-click link to open the item on a synced site
- [ ] User can filter by site or topic
- [ ] Sync status shows last pull from each site

**Implementation notes:**
- Start with CLI version (simpler, testable)
- Web dashboard is nice-to-have for v1
- Use the existing `partner_sdk` to fetch site metadata

**Estimated effort:** 1-2 weeks

---

### 🥉 Site Integration Template

**Description:** Example code for a partner site to honor the delta-sync contract and send recommendation events.

**Acceptance criteria:**
- [ ] Published Node.js / Python starter showing: detect ORF → pull delta → rank feed → publish recommendation event
- [ ] Documented for junior developers (<5 years experience)
- [ ] Works with the reference service
- [ ] One minimal test showing the flow end-to-end

**Implementation notes:**
- This is mostly documentation + example code
- Demonstrates the delta-sync contract in action
- Lowers friction for partner integration

**Estimated effort:** 1 week

---

### 📊 Feed Metrics & Debug Page

**Description:** Admin view showing feed quality metrics for debugging.

**Acceptance criteria:**
- [ ] `GET /admin/feed-metrics` returns:
   - Number of events per site
   - Number of deduplicated items
   - Top recommended items (consensus ranking)
   - Feed latency (time to aggregate)
- [ ] Helps validate ranking logic works

**Implementation notes:**
- Reference service only, not core spec
- Helps us debug ranking bugs

**Estimated effort:** 3 days

---

### ⭐ Future (Post-v1): Collections & Curation

**Description:** Users create "mood-based" or "context-based" sub-feeds (e.g., "weekend chill," "work focus," "family safe").

**Acceptance criteria:** Not in Phase 2 scope.

**Why it's valuable:** Solves multi-context households and devices. Feed becomes even more personalized.

**Estimated effort:** 2-3 weeks (Phase 2.5)

---

## Sequencing & Dependencies

```
Week 1-2: Core aggregation + tests
    ↓
Week 3: CLI dashboard + integration template
    ↓
Week 4: Pilot site integration + validation
    ↓
Week 5-6: Metrics + fixes + hardening
```

**Blockers:**
- None. This builds on top of existing event sync infrastructure.

**Pilot sites:**
- Need at least 1 partner to integrate and validate (e.g., indie music discovery, niche news site).
- They should implement the site integration template.
- We measure: users active daily with feed? Retention improvement? Recommendation quality?

---

## Success Criteria

### User-level:
- [ ] Users with synced `.orf` files see cross-site recommendations
- [ ] At least 20% DAU improvement for users with 2+ synced sites
- [ ] Users report "I didn't have to scroll separately through each app" sentiment

### Developer-level:
- [ ] Pilot sites can integrate in <4 hours using the template
- [ ] Zero data model changes needed (events are the substrate)
- [ ] Feed aggregation algorithm is clearly documented

### Business-level:
- [ ] First pilot site goes live (proves adoption interest)
- [ ] Recommendation quality is better than single-site baseline (if we have metrics)
- [ ] Story for Phase 3 (narratives + investor pitch) has live demo

---

## Risk Mitigation

### Risk: Feed ranking is bad / recommendations don't feel better

**Mitigation:**
- Start with simple consensus + freshness ranking
- A/B test with single-site baseline
- Iterate quickly; ranking is not a hard requirement for v1

### Risk: Pilot site can't integrate in time

**Mitigation:**
- Have a second backup pilot site lined up
- Fallback: build a minimal demo site ourselves (proof of concept)

### Risk: Event duplication / de-duplication bugs

**Mitigation:**
- Write comprehensive tests for merge/ranking logic
- Use the rubber-duck agent to review ranking code before merging

### Risk: Privacy leakage through feed metadata

**Mitigation:**
- Feed is generated locally (in CLI or browser), never sent to server
- Sites can't see other sites' recommendations (feed doesn't sync to service)
- Privacy review: use `critique-privacy-paranoid` on the metadata exposure

---

## Open Questions

1. **What do we rank by besides consensus + freshness?** Candidate signals: user preference affinity (topics), item recency, site trustworthiness (don't boost from spam sites), user's past engagement with that site's recommendations.

2. **How do we avoid cold-start for new sites?** If a user just synced a site with zero events, the feed will be sparse. Answer: fall back to single-site personalization from the site itself; the feed just layers additional signal.

3. **How do we handle recommendation conflicts?** If Netflix suggests a movie as a 9/10 and another site suggests it as 3/10, how do we resolve? Answer: show both scores, let the UI decide to average or show the range.

4. **Should the feed sync back to the service?** No, initially. Feed is generated locally. Future: users could opt-in to backing up their "feed clicks" (which recommendations they followed) to improve cold-start.

---

## Next Step

When approved, we will:
1. Spec out the aggregation algorithm in detail (ranking weights, de-duplication rules)
2. Implement core aggregation + tests
3. Reach out to 2-3 potential pilot sites for parallel integration
4. Ship an MVP dashboard (CLI first) by week 4

The feed is the bridge between portability (technical promise) and delight (user experience). It answers "why should I bother?" with "because you get better recommendations without the friction of separate apps."
