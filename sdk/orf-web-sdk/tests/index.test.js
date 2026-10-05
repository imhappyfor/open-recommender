import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash, createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import { readFileSync } from "node:fs";

import { canonicalEventJsonBytes, canonicalJsonBytes, encodeBase64Url, ORFClient } from "../src/index.js";

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

test("shared Python challenge vectors match bytes, identity, and Ed25519 signatures", () => {
  const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/signing-vectors.json", import.meta.url)));
  const rawKey = Buffer.from(fixture.public_key, "base64url");
  const key = createPublicKey({
    key: { kty: "OKP", crv: "Ed25519", x: rawKey.toString("base64url") }, format: "jwk",
  });
  assert.equal(`orf:profile:${createHash("sha256").update(rawKey).digest("hex").slice(0, 32)}`, fixture.profile_id);
  for (const vector of fixture.vectors) {
    const bytes = canonicalJsonBytes(vector.payload);
    assert.equal(Buffer.from(bytes).toString("hex"), vector.canonical_utf8_hex, vector.name);
    assert.equal(verify(null, bytes, key, Buffer.from(vector.signature, "base64url")), true, vector.name);
  }
  const digest = createHash("sha256").update(canonicalJsonBytes(fixture.owner_action)).digest("hex");
  assert.equal(fixture.vectors[1].payload.challenge_type, `owner-deny:${digest}`);
});

test("challenge encoder rejects values it cannot encode compatibly", () => {
  for (const value of [0.5, -0, NaN, Infinity, 9007199254740992, undefined, new Date(), { é: "value" }, [,]]) {
    assert.throws(() => canonicalJsonBytes({ value }), TypeError);
  }
});

test("JCS event vectors match Python bytes and signatures after JavaScript serialization", () => {
  const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/event-signing-vectors.json", import.meta.url), "utf8"));
  const publicKey = createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: fixture.public_key.replace(/=+$/, "") }, format: "jwk" });
  const privateKey = createPrivateKey({ key: Buffer.concat([
    Buffer.from("302e020100300506032b657004220420", "hex"), Buffer.from(Array.from({ length: 32 }, (_, index) => index)),
  ]), format: "der", type: "pkcs8" });
  for (const vector of fixture.vectors) {
    const payload = JSON.parse(JSON.stringify(vector.payload));
    const bytes = canonicalEventJsonBytes(payload);
    assert.equal(Buffer.from(bytes).toString("utf8"), vector.canonical_utf8, vector.name);
    assert.equal(sign(null, bytes, privateKey).toString("base64url"), vector.signature.replace(/=+$/, ""), vector.name);
    assert.ok(verify(null, bytes, publicKey, Buffer.from(vector.signature, "base64url")), vector.name);
  }
});

test("JCS encoder rejects unknown encoding, non-JSON values and invalid Unicode", () => {
  assert.throws(() => canonicalEventJsonBytes({}), TypeError);
  assert.throws(() => canonicalEventJsonBytes({ signature_encoding: "jcs-rfc8785", signature: "sig" }), TypeError);
  for (const value of [NaN, Infinity, undefined, new Date(), 1n, [,], "\ud800", "\udc00", { "\ud800": 1 }]) {
    assert.throws(() => canonicalEventJsonBytes({ signature_encoding: "jcs-rfc8785", value }), TypeError);
  }
  const bytes = canonicalEventJsonBytes({ signature_encoding: "jcs-rfc8785", numbers: [-0, 1.0, 1e-7, 1e20] });
  assert.match(new TextDecoder().decode(bytes), /\[0,1,1e-7,100000000000000000000\]/);
});

test("denial preserves an explicitly empty reason for owner-proof binding", async () => {
  await withMockFetch(() => jsonResponse({ ok: true }), async (calls) => {
    await new ORFClient("http://127.0.0.1:8000").denyConsentRequest({
      requestId: "req-1", reason: "", csrfToken: "csrf-1", ownerProof: { challenge_id: "owner-1", signature: "sig" },
    });
    assert.equal(JSON.parse(calls[0].init.body).reason, "");
  });
});

test("profile file upload preserves signed numeric JSON without reserialization", async () => {
  await withMockFetch(() => jsonResponse({ ok: true }), async (calls) => {
    const raw = '{"event_log":[{"payload":{"weight":1.0,"small":1e-07,"precise":9007199254740993}}]}';
    const client = new ORFClient("http://127.0.0.1:8000");
    await client.upsertProfile(raw);
    assert.equal(calls[0].init.body, `{"profile":${raw}}`);
    assert.equal(calls[0].init.headers["Content-Type"], "application/json");
    await assert.rejects(client.upsertProfile("[]"), /must be an object/);
    await assert.rejects(client.upsertProfile('{} , "extra":{}'), SyntaxError);
    assert.equal(calls.length, 1);
  });
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
        ownerProof: { challenge_id: "owner-1", signature: "approve-signature" },
      });
      await client.denyConsentRequest({
        requestId: "req-1",
        reason: "No thanks.",
        csrfToken: "csrf-1",
        ownerProof: { challenge_id: "owner-2", signature: "deny-signature" },
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
        owner_proof: { challenge_id: "owner-1", signature: "approve-signature" },
      });

      assert.equal(calls[3].url, "http://127.0.0.1:8000/consent/site-access-requests/req-1/deny");
      assert.equal(calls[3].init.headers["X-Open-Recommender-CSRF-Token"], "csrf-1");
      assert.deepEqual(JSON.parse(calls[3].init.body), {
        reason: "No thanks.",
        owner_proof: { challenge_id: "owner-2", signature: "deny-signature" },
      });
    },
  );
});

test("owner challenge, revocation and deletion preserve proof and action parameters", async () => {
  await withMockFetch(() => jsonResponse({ ok: true }), async (calls) => {
    const client = new ORFClient("http://127.0.0.1:8000");
    const proof = { challenge_id: "owner-1", signature: "signed-challenge" };
    await client.createOwnerActionChallenge({
      profileId: "orf:profile:abc", action: "revoke", targetId: "grant-1", parameters: { reason: "Finished" },
    });
    await client.revokeGrant("grant-1", { ownerProof: proof, reason: "Finished" });
    await client.deleteProfile("orf:profile:abc", proof);
    assert.equal(calls[0].url, "http://127.0.0.1:8000/profiles/orf:profile:abc/owner-action-challenges");
    assert.deepEqual(JSON.parse(calls[0].init.body), {
      action: "revoke", target_id: "grant-1", parameters: { reason: "Finished" },
    });
    assert.equal(calls[1].url, "http://127.0.0.1:8000/grants/grant-1/revoke");
    assert.deepEqual(JSON.parse(calls[1].init.body), { reason: "Finished", owner_proof: proof });
    assert.equal(calls[2].url, "http://127.0.0.1:8000/profiles/orf:profile:abc/delete");
    assert.deepEqual(JSON.parse(calls[2].init.body), { owner_proof: proof });
  });
});

test("raw sync reads require owner proof and keep the shared token as an extra gate", async () => {
  await withMockFetch(() => jsonResponse({ events: [] }), async (calls) => {
    const client = new ORFClient("http://127.0.0.1:8000", { syncToken: "extra-gate" });
    await assert.rejects(client.pullEvents("orf:profile:abc"), /owner proof/);
    assert.equal(calls.length, 0);
    const proof = { challenge_id: "read-1", signature: "signed-owner-challenge" };
    await client.pullEvents("orf:profile:abc", { afterClock: 7, ownerProof: proof });
    assert.equal(calls[0].url, "http://127.0.0.1:8000/profiles/orf:profile:abc/events/read");
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.headers.Authorization, "Bearer extra-gate");
    assert.deepEqual(JSON.parse(calls[0].init.body), { after_clock: 7, owner_proof: proof });
  });
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
