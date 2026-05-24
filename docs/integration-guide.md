# Integration guide

This guide shows how a site integrates with Open Recommender using the browser SDK or the Python partner SDK.

## Site flow

1. Create a scoped access request for a profile.
2. Send the user to the consent review surface.
3. Poll the request until it is approved.
4. Start the exchange and receive `challenge_payload`.
5. Let the user's ORF client sign that payload with the private key.
6. Verify the signature and fetch the consented projection.

## Browser SDK

For React, Vite, and other modern web apps, use `sdk/orf-web-sdk/`.

```js
import { ORFClient } from "@open-recommender/orf-web-sdk";

const client = new ORFClient("http://127.0.0.1:8000");
const created = await client.createAccessRequest({
  profileId: "orf:profile:…",
  siteId: "open-news-demo",
  purpose: "Personalize the feed.",
  requestedScopes: ["profile.read", "topics.public"],
});
```

## Python partner SDK

For server-side integration code, use `open_recommender.partner_sdk.PartnerClient`.
It exposes the same request, exchange, verify, projection, and sync operations.

## Scope boundary

- The site never holds the user's ORF private key.
- `challenge_payload` must be signed by the user's own client.
- Public projections only include the scopes the user approved.

See `docs/pilot-integration.md` for the localhost reference flow and `sdk/README.md` for the browser SDK quick start.
