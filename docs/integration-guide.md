# Integration guide

This guide shows how a site integrates with Open Recommender using the browser SDK or the Python partner SDK.

## Site flow

1. Create a scoped access request for a profile.
2. Send the user to the consent review surface.
3. The user's own client signs an action-bound consent challenge; the site polls until approval.
4. Start the exchange and receive `challenge_payload`.
5. Let the user's ORF client sign that payload with the private key.
6. Verify the signature and fetch the consented projection.
7. Optionally rerank site-generated candidates inside the verified grant session.

## Browser SDK

For React, Vite, and other modern web apps, use `sdk/orf-web-sdk/`.
The direct partner calls below are for the unauthenticated localhost preview.
With [backend credentials](pilot-integration.md#backend-credentials) enabled,
send partner operations through your site's backend instead. Never embed its
token in browser code; user signing remains in a separate trusted client.

```js
import { ORFClient } from "@open-recommender/orf-web-sdk";

const client = new ORFClient("http://127.0.0.1:8000");
const created = await client.createAccessRequest({
  profileId: "orf:profile:…",
  siteId: "open-news-demo",
  purpose: "Personalize the feed.",
  requestedScopes: ["profile.read", "topics.public"],
});

const ranking = await client.rankCandidates("grant-session-id", {
  candidates: [
    {
      candidate_id: "story-123",
      site_score: 0.78,
      candidate_topics: ["orf:media/podcasts"],
    },
  ],
});
```

## Python partner SDK

For server-side integration code, use `open_recommender.partner_sdk.PartnerClient`.
It exposes the same request, exchange, verify, projection, reranking, and sync operations.
Configure `site_id` and `site_token` for authenticated partner calls. Those
credentials cannot authorize owner actions or raw history reads.

## Scope boundary

- The site never holds the user's ORF private key.
- Raw sync history is owner-only. A shared service token is not authority to
  read it; site integrations use projections or ranking instead.
- `challenge_payload` must be signed by the user's own client.
- Consented projections include only approved signals; the public projection is a separate read surface.
- Approval/denial and revocation/deletion require a one-time owner proof over an action-specific challenge. See [Owner actions](architecture.md#owner-actions) for exact parameters and endpoints.
- Grant-session ranking uses those approved signals internally but does not expose raw profile topics or
  topic weights in the response.

The wire rules and signing fixtures are in the [protocol](protocol.md). See the
[pilot walkthrough](pilot-integration.md) for a complete localhost integration
and the [SDK guide](../sdk/README.md) for browser setup.

Before making recommendation-quality claims, compare with the site's own order
using [offline ranking evaluation](ranking-evaluation.md). The included examples
test mechanics, not real-world relevance or a production deployment.
