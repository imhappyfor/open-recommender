# Phase 2 Integration Guide: Cross-Site Feeds & Delta Sync

This guide is for developers integrating Open Recommender Phase 2 into their sites. It covers:

1. **Delta-Sync Contract**: How to pull user preference updates from the `.orf` file
2. **Feed Aggregation**: How to fetch and display the aggregated cross-site feed
3. **Practical Examples**: Reference implementations in Python and Node.js

---

## Part 1: Delta-Sync Contract

The delta-sync contract is how a site stays in sync with a user's portable preferences.

### Five-Step Flow

When a user with an Open Recommender profile visits your site:

1. **Detect ORF file**: Check if user has imported a `.orf` file (browser upload or QR code)
2. **Pull delta**: Call `GET /profiles/{profile_id}/events?after_clock={last_sync_clock}`
3. **Apply events**: Merge recommendation events into your local recommendation engine
4. **Render**: Use aggregated recommendations to show personalized content
5. **Save clock**: Store the sync clock to avoid re-fetching events

### API Endpoint: GET `/profiles/{profile_id}/events`

**Parameters:**
- `profile_id` (path): The user's profile ID (from `.orf` file)
- `after_clock` (query): Last sync clock value (0 on first pull)

**Response:**
```json
{
  "profile_id": "...",
  "events": [
    {
      "event_id": "...",
      "clock": 1,
      "timestamp": "2025-01-21T10:00:00+00:00",
      "op": "recommend",
      "payload": {
        "item_id": "movie-123",
        "site_id": "your-site",
        "site_name": "Your Site",
        "score": 0.85,
        "metadata": {...}
      }
    },
    ...
  ],
  "last_clock": 5
}
```

**Event Types Relevant to Feeds:**
- `recommend`: A recommendation from this site (site-generated, pushed to user's profile)
- `set_topic`: User preference for a topic (updated weight/visibility)
- `remove_topic`: User removed a topic preference
- `set_opt_out`: User opted out of a topic

### Sync Strategy

```python
# Pseudocode
def sync_with_orf(profile: ORFProfile, site_id: str) -> None:
    """Apply user's portable preferences to personalization engine."""
    
    # Fetch delta since last sync
    events = fetch_delta(
        profile_id=profile.profile_id,
        after_clock=profile.sync.last_clock  # stored from last sync
    )
    
    for event in events:
        if event.op == "recommend":
            # Apply recommendation from user's other sites
            apply_recommendation_to_feed(event.payload)
        
        elif event.op in ("set_topic", "remove_topic"):
            # Update user preference weights
            update_recommendation_weights(event.payload)
        
        elif event.op == "set_opt_out":
            # User opted out of topic; filter from feed
            apply_opt_out_filter(event.payload)
    
    # Save sync clock for next pull
    profile.sync.last_clock = events[-1]["clock"]
```

### When to Sync

**Best practices:**

- **On page load**: Pull latest delta before rendering feed
- **On navigation**: Re-pull if user navigates to recommendation-heavy section
- **On background task**: Periodic sync (e.g., every hour) for always-on services
- **Do NOT sync on every interaction**: Batch events and sync once per session

---

## Part 2: Fetching the Aggregated Feed

Open Recommender's hosted service provides an aggregated feed endpoint that combines recommendations from all synced sites.

### API Endpoint: GET `/profiles/{profile_id}/aggregated-feed`

**Parameters:**
- `profile_id` (path): User's profile ID
- `top_n` (query, optional): Number of items to return (default: 20, max: 100)

**Response:**
```json
{
  "profile_id": "...",
  "aggregated_at": "2025-01-21T10:05:00+00:00",
  "total_items": 47,
  "recommendations": [
    {
      "item_id": "movie-123",
      "consensus_score": 0.67,
      "freshness_boost": 0.95,
      "affinity_boost": 0.8,
      "final_score": 0.81,
      "sources": [
        {
          "item_id": "movie-123",
          "site_id": "netflix",
          "site_name": "Netflix",
          "score": 0.9,
          "reason": "Trending in your genre",
          "metadata": {
            "title": "The Matrix",
            "description": "...",
            "thumbnail": "https://..."
          },
          "timestamp": "2025-01-21T10:00:00+00:00"
        },
        {
          "item_id": "movie-123",
          "site_id": "imdb",
          "site_name": "IMDb",
          "score": 0.85,
          "reason": "Top-rated in sci-fi",
          "metadata": {...},
          "timestamp": "2025-01-21T09:30:00+00:00"
        }
      ],
      "best_metadata": {...}  // Metadata from highest-scoring source
    },
    ...
  ]
}
```

### Ranking Algorithm

Each recommendation is scored by:

- **Consensus** (40% weight, default): How many unique sites recommend this item, normalized to 0-1
- **Freshness** (30% weight): Exponential decay from most recent timestamp (7-day half-life)
- **Affinity** (30% weight): How well the item matches the user's high-weight topics

**Formula:**
```
final_score = 0.4 * consensus + 0.3 * freshness + 0.3 * affinity
```

### Handling Conflicting Scores

When multiple sites disagree on an item's quality (e.g., Netflix 9/10 vs niche site 3/10):

**Option 1: Show both scores** (recommended)
```json
{
  "item_id": "movie-123",
  "sources": [
    {"site_id": "netflix", "score": 0.9},
    {"site_id": "niche", "score": 0.3}
  ]
}
```

**Option 2: Average** (simple but loses signal)
```
average_score = (0.9 + 0.3) / 2 = 0.6
```

**Option 3: Weighted by site authority** (advanced)
```
weighted = (0.9 * netflix_authority + 0.3 * niche_authority) / (authority_sum)
```

We recommend **Option 1** to preserve transparency. Users should see why they're seeing a recommendation.

---

## Part 3: Example Implementations

### Python Example: Minimal Integration

```python
import requests
import json
from pathlib import Path

class ORFIntegration:
    """Minimal example of Phase 2 delta-sync + feed integration."""
    
    def __init__(self, server: str = "http://localhost:8000"):
        self.server = server
        self.sync_clock = 0  # Stored in your DB per-user
    
    def pull_and_apply_delta(self, profile_id: str) -> dict:
        """Fetch delta from user's portable profile and apply to local engine."""
        
        response = requests.get(
            f"{self.server}/profiles/{profile_id}/events",
            params={"after_clock": self.sync_clock}
        )
        response.raise_for_status()
        data = response.json()
        
        for event in data["events"]:
            self._apply_event(event)
        
        # Update sync clock
        self.sync_clock = data["last_clock"]
        
        return {"synced_events": len(data["events"]), "new_clock": self.sync_clock}
    
    def _apply_event(self, event: dict) -> None:
        """Apply a single event to your recommendation engine."""
        
        if event["op"] == "recommend":
            # User's other sites recommend this item
            payload = event["payload"]
            print(f"Add recommendation: {payload['item_id']} from {payload['site_name']}")
            # TODO: Update your recommendation engine
        
        elif event["op"] == "set_topic":
            # User updated topic weight
            payload = event["payload"]
            print(f"Update topic weight: {payload['topic']} = {payload['weight']}")
            # TODO: Re-rank recommendations for this topic
        
        elif event["op"] == "set_opt_out":
            # User opted out of topic
            payload = event["payload"]
            print(f"Filter out topic: {payload['topic']}")
            # TODO: Remove recommendations tagged with this topic
    
    def fetch_aggregated_feed(self, profile_id: str, top_n: int = 20) -> dict:
        """Fetch the cross-site aggregated feed."""
        
        response = requests.get(
            f"{self.server}/profiles/{profile_id}/aggregated-feed",
            params={"top_n": top_n}
        )
        response.raise_for_status()
        return response.json()
    
    def render_feed(self, profile_id: str) -> None:
        """Sync user's preferences and render aggregated feed."""
        
        # Step 1: Sync delta
        sync_result = self.pull_and_apply_delta(profile_id)
        print(f"Synced {sync_result['synced_events']} events")
        
        # Step 2: Fetch aggregated feed
        feed = self.fetch_aggregated_feed(profile_id, top_n=10)
        
        # Step 3: Render
        for rec in feed["recommendations"]:
            print(f"\n{rec['best_metadata'].get('title', rec['item_id'])}")
            print(f"  Score: {rec['final_score']:.2f} (consensus={rec['consensus_score']:.2f})")
            print(f"  From: {', '.join(s['site_name'] for s in rec['sources'])}")


# Usage
if __name__ == "__main__":
    integration = ORFIntegration()
    
    # Simulate user login with .orf file
    profile_id = "abc123def456"  # From user's .orf file
    
    # Render feed on page load
    integration.render_feed(profile_id)
```

### Node.js Example: Express Integration

```javascript
// phase-2-integration.js
const express = require('express');
const axios = require('axios');

const app = express();
const SERVER = process.env.ORF_SERVER || 'http://localhost:8000';

// Store sync clock per-user (in real app, use DB)
const userSyncState = {};

async function pullAndApplyDelta(profileId) {
  const lastClock = userSyncState[profileId]?.lastClock || 0;
  
  try {
    const response = await axios.get(
      `${SERVER}/profiles/${profileId}/events`,
      { params: { after_clock: lastClock } }
    );
    
    for (const event of response.data.events) {
      applyEvent(profileId, event);
    }
    
    // Update sync clock
    userSyncState[profileId] = {
      lastClock: response.data.last_clock,
      lastSync: new Date()
    };
    
    return response.data.events.length;
  } catch (error) {
    console.error('Delta pull failed:', error.message);
    return 0;
  }
}

function applyEvent(profileId, event) {
  switch (event.op) {
    case 'recommend':
      console.log(`Recommendation from ${event.payload.site_name}: ${event.payload.item_id}`);
      // TODO: Update your recommendation engine
      break;
    
    case 'set_topic':
      console.log(`Topic weight updated: ${event.payload.topic} = ${event.payload.weight}`);
      // TODO: Re-rank based on new weights
      break;
    
    case 'set_opt_out':
      console.log(`User opted out of: ${event.payload.topic}`);
      // TODO: Filter recommendations
      break;
  }
}

async function fetchAggregatedFeed(profileId, topN = 20) {
  try {
    const response = await axios.get(
      `${SERVER}/profiles/${profileId}/aggregated-feed`,
      { params: { top_n: topN } }
    );
    return response.data;
  } catch (error) {
    console.error('Feed fetch failed:', error.message);
    return null;
  }
}

app.get('/api/feed/:profileId', async (req, res) => {
  const { profileId } = req.params;
  const topN = parseInt(req.query.top_n || '20');
  
  try {
    // Step 1: Sync user's portable preferences
    const syncedCount = await pullAndApplyDelta(profileId);
    console.log(`Synced ${syncedCount} events`);
    
    // Step 2: Fetch aggregated feed
    const feed = await fetchAggregatedFeed(profileId, topN);
    
    if (!feed) {
      return res.status(500).json({ error: 'Feed fetch failed' });
    }
    
    // Step 3: Render (return JSON for frontend to render)
    res.json({
      profileId,
      feedSize: feed.total_items,
      lastSync: userSyncState[profileId]?.lastSync,
      recommendations: feed.recommendations.map(rec => ({
        itemId: rec.item_id,
        title: rec.best_metadata.title || rec.item_id,
        score: rec.final_score,
        consensus: rec.consensus_score,
        sources: rec.sources.map(s => ({
          siteName: s.site_name,
          siteScore: s.score,
          reason: s.reason
        }))
      }))
    });
  } catch (error) {
    console.error('Recommendation feed error:', error);
    res.status(500).json({ error: error.message });
  }
});

app.listen(3000, () => {
  console.log('Integration server listening on port 3000');
  console.log('Try: curl http://localhost:3000/api/feed/YOUR_PROFILE_ID');
});
```

### CLI Example: Using Partner SDK

```python
#!/usr/bin/env python3
"""
Phase 2 CLI example: Create a profile, add recommendations, fetch aggregated feed.
"""

import json
from pathlib import Path
from open_recommender.cli import load_profile, save_profile
from open_recommender.crypto import load_private_key, sign_payload, generate_key_pair, save_private_key
from open_recommender.models import ORFProfile, build_signed_event, EventOp, AggregatedFeed

# Create a profile
private_key, public_key = generate_key_pair()
profile = ORFProfile.create("Alice", public_key, "demo-device")

# Add recommendations from multiple sites
sites = [
    ("netflix", "Netflix", [("movie-1", 0.9), ("movie-2", 0.8)]),
    ("imdb", "IMDb", [("movie-1", 0.85), ("movie-3", 0.75)]),
    ("youtube", "YouTube", [("video-1", 0.88), ("movie-2", 0.7)]),
]

for site_id, site_name, items in sites:
    for item_id, score in items:
        payload = {
            "item_id": item_id,
            "site_id": site_id,
            "site_name": site_name,
            "score": score,
            "metadata": {"title": f"{item_id} from {site_name}"}
        }
        event = build_signed_event(profile, EventOp.RECOMMEND, payload, signature="")
        event.signature = sign_payload(event.unsigned_payload(), private_key)
        profile.apply_event(event)

# Save profile and key
profile_path = Path("/tmp/alice.orf")
key_path = Path("/tmp/alice.orf.key")
save_private_key(key_path, private_key)

# Create and display aggregated feed
feed = AggregatedFeed(profile)
top_recs = feed.top_n(5)

print("=== Aggregated Feed ===\n")
for i, rec in enumerate(top_recs, 1):
    print(f"{i}. {rec.item_id}")
    print(f"   Score: {rec.final_score:.3f} (consensus={rec.consensus_score:.2f})")
    print(f"   Sources: {', '.join(s.site_name for s in rec.sources)}")
    print()
```

---

## Part 4: Checklist for Integration

### Pre-Launch Checklist

- [ ] **API connectivity**: Test delta-sync endpoint (`GET /profiles/{id}/events`)
- [ ] **Event application**: Verify you apply all event types (recommend, set_topic, set_opt_out)
- [ ] **Sync clock storage**: Persist sync clock per user in your database
- [ ] **Feed endpoint**: Verify aggregated feed endpoint returns correct rankings
- [ ] **De-duplication**: Same item from multiple sites appears once in feed
- [ ] **Conflict handling**: Test when multiple sites disagree on item quality
- [ ] **Cold start**: Test integration with new user (no prior recommendations)
- [ ] **Load testing**: Verify endpoint handles burst traffic during peak hours
- [ ] **Error handling**: Gracefully handle network failures, malformed events
- [ ] **Privacy**: Ensure you don't log user profile IDs or event content

### Operational Checklist

- [ ] **Monitoring**: Track delta-sync latency (should be <100ms)
- [ ] **Alerts**: Alert if sync fails for user >1 hour
- [ ] **Logs**: Log (anonymized) event counts per site to understand data flow
- [ ] **Backup**: Store sync clock in two places (memory + DB) for resilience
- [ ] **Documentation**: Document your event handling logic for future engineers

---

## Part 5: FAQ & Troubleshooting

### Q: My recommendations aren't updating from user's other sites

**A:** Check:
1. Are you calling `GET /profiles/{id}/events` on page load?
2. Are you persisting `last_clock` correctly?
3. Is the user's `.orf` file properly registered (they approved access to your site)?

### Q: The aggregated feed has stale recommendations

**A:** Feeds are computed on-demand and can lag if sites push recommendations infrequently. Consider:
1. Pushing recommendations within 1 hour of user interaction
2. Caching aggregated feed for 5 minutes per user

### Q: How do I handle recommendations from competing sites?

**A:** Show both scores transparently. Don't average or hide disagreement. Users should know why they're seeing something.

### Q: Should I pull delta on every page load?

**A:** Yes for recommendation-heavy pages. For light pages, sync once per session or every 10 minutes. Don't sync on every interaction (causes thundering herd).

### Q: What if a user opts out of a topic?

**A:** The `set_opt_out` event tells you to filter. Remove that topic from recommendations immediately. Don't show the user anything tagged with that topic.

---

## Support & Questions

For integration questions:
- Check `examples/sample_site.py` for a working reference
- Review `partner_sdk.py` for Python SDK
- Open an issue on GitHub with "[Phase 2]" in the title
