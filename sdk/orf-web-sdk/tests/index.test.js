import assert from "node:assert/strict";
import { test } from "node:test";

import { canonicalJsonBytes, encodeBase64Url, ORFClient } from "../src/index.js";

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: async () => payload,
  };
}

async function withMockFetch(handler, callback) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({
      url,
      init: {
        method: init.method,
        headers: { ...init.headers },
        body: init.body,
      },
    });
    return handler(url, init, calls);
  };

  try {
    await callback(calls);
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test("canonicalJsonBytes sorts nested object keys", () => {
  const text = new TextDecoder().decode(
    canonicalJsonBytes({
      z: 1,
      nested: { b: 2, a: 1 },
      items: [{ y: 2, x: 1 }],
    }),
  );

  assert.equal(
    text,
    JSON.stringify({ items: [{ x: 1, y: 2 }], nested: { a: 1, b: 2 }, z: 1 }),
  );
});

test("encodeBase64Url handles large payloads without stack overflow", () => {
  const bytes = new Uint8Array(70000);
  bytes.fill(1);

  assert.doesNotThrow(() => encodeBase64Url(bytes));
});

test("ORFClient createAccessRequest supports requested scopes", async () => {
  await withMockFetch(
    () =>
      jsonResponse({
        access_request: { request_id: "req-1" },
        consent_review_url: "http://127.0.0.1:8000/consent/site-access-requests/req-1",
      }),
    async (calls) => {
      const client = new ORFClient("http://127.0.0.1:8000");
      const response = await client.createAccessRequest({
        profileId: "orf:profile:abc123",
        siteId: "open-news-demo",
        purpose: "Personalize the feed.",
        requestedScopes: ["profile.read", "topics.public"],
      });

      assert.equal(response.access_request.request_id, "req-1");
      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, "http://127.0.0.1:8000/profiles/orf:profile:abc123/site-access-requests");
      assert.equal(calls[0].init.method, "POST");
      assert.equal(calls[0].init.headers.Accept, "application/json");
      assert.equal(calls[0].init.headers["Content-Type"], "application/json");
      assert.deepEqual(JSON.parse(calls[0].init.body), {
        site_id: "open-news-demo",
        purpose: "Personalize the feed.",
        requested_scopes: ["profile.read", "topics.public"],
      });
    },
  );
});

test("ORFClient createAccessRequest supports required and optional scopes", async () => {
  await withMockFetch(
    () =>
      jsonResponse({
        access_request: { request_id: "req-2" },
        consent_review_url: "http://127.0.0.1:8000/consent/site-access-requests/req-2",
      }),
    async (calls) => {
      const client = new ORFClient("http://127.0.0.1:8000");
      await client.createAccessRequest({
        profileId: "orf:profile:abc123",
        siteId: "open-news-demo",
        purpose: "Personalize the feed.",
        requiredScopes: ["profile.read", "topics.public"],
        optionalScopes: ["topics.selective:orf:media/podcasts"],
      });

      assert.deepEqual(JSON.parse(calls[0].init.body), {
        site_id: "open-news-demo",
        purpose: "Personalize the feed.",
        required_scopes: ["profile.read", "topics.public"],
        optional_scopes: ["topics.selective:orf:media/podcasts"],
      });
    },
  );
});

test("ORFClient upsertProfile and consent methods shape browser requests", async () => {
  await withMockFetch(
    () => jsonResponse({ ok: true }),
    async (calls) => {
      const client = new ORFClient("http://127.0.0.1:8000");
      await client.upsertProfile({ profile_id: "orf:profile:abc123", display_name: "Alice" });
      await client.getConsentReview("req-1");
      await client.approveConsentRequest({
        requestId: "req-1",
        approvedScopes: ["profile.read"],
        csrfToken: "csrf-1",
      });
      await client.denyConsentRequest({
        requestId: "req-1",
        reason: "No thanks.",
        csrfToken: "csrf-1",
      });

      assert.equal(calls[0].url, "http://127.0.0.1:8000/profiles");
      assert.equal(calls[0].init.method, "POST");
      assert.deepEqual(JSON.parse(calls[0].init.body), {
        profile: { profile_id: "orf:profile:abc123", display_name: "Alice" },
      });

      assert.equal(calls[1].url, "http://127.0.0.1:8000/consent/site-access-requests/req-1/review-data");
      assert.equal(calls[1].init.method, "GET");

      assert.equal(calls[2].url, "http://127.0.0.1:8000/consent/site-access-requests/req-1/approve");
      assert.equal(calls[2].init.headers["X-Open-Recommender-CSRF-Token"], "csrf-1");
      assert.deepEqual(JSON.parse(calls[2].init.body), {
        approved_scopes: ["profile.read"],
      });

      assert.equal(calls[3].url, "http://127.0.0.1:8000/consent/site-access-requests/req-1/deny");
      assert.equal(calls[3].init.headers["X-Open-Recommender-CSRF-Token"], "csrf-1");
      assert.deepEqual(JSON.parse(calls[3].init.body), {
        reason: "No thanks.",
      });
    },
  );
});

test("ORFClient rankCandidates uses the grant-session ranking endpoint", async () => {
  await withMockFetch(
    () =>
      jsonResponse({
        session: { session_id: "session-1" },
        ranking: { ranked_candidates: [] },
      }),
    async (calls) => {
      const client = new ORFClient("http://127.0.0.1:8000");
      await client.rankCandidates("session-1", {
        topN: 2,
        includeDebug: true,
        schemaVersion: "0.3.0",
        candidates: [
          {
            candidate_id: "story-123",
            site_score: 0.78,
            candidate_topics: ["orf:media/podcasts"],
            metadata: { slot: "hero" },
          },
        ],
      });

      assert.equal(calls[0].url, "http://127.0.0.1:8000/grant-sessions/session-1/rank");
      assert.equal(calls[0].init.method, "POST");
      assert.deepEqual(JSON.parse(calls[0].init.body), {
        schema_version: "0.3.0",
        top_n: 2,
        include_debug: true,
        candidates: [
          {
            candidate_id: "story-123",
            site_score: 0.78,
            candidate_topics: ["orf:media/podcasts"],
            metadata: { slot: "hero" },
          },
        ],
      });
    },
  );
});
