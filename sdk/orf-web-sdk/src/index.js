/**
 * orf-web-sdk – Browser-friendly ESM client for the Open Recommender hosted service.
 *
 * Uses only the Fetch API and standard Web APIs so it works in any modern browser
 * or React / Vite app without special polyfills.
 */

export class ORFClientError extends Error {
  /**
   * @param {string} message
   * @param {number|null} status
   * @param {unknown} detail
   */
  constructor(message, status = null, detail = null) {
    super(message);
    this.name = "ORFClientError";
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Canonical encoding for ORF challenges (ASCII field names, no floating-point numbers).
 * Matches Python's compact, sorted, ensure_ascii JSON for this supported subset.
 * Not a general event-signing encoder: Python and JavaScript format floats differently.
 * @param {Record<string, unknown>} payload
 * @returns {Uint8Array}
 */
export function canonicalJsonBytes(payload) {
  const quote = (value) => JSON.stringify(value).replace(/[\u007f-\uffff]/g,
    (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
  const encode = (value) => {
    if (value === null) return "null";
    if (typeof value === "string") return quote(value);
    if (typeof value === "boolean") return String(value);
    if (typeof value === "number" && Number.isSafeInteger(value) && !Object.is(value, -0)) {
      return String(value);
    }
    if (Array.isArray(value)) {
      return `[${Array.from(value, encode).join(",")}]`;
    }
    if (value && typeof value === "object" &&
        [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
      const keys = Object.keys(value).sort();
      if (keys.some((key) => /[^\x00-\x7f]/.test(key))) {
        throw new TypeError("ORF challenge field names must be ASCII.");
      }
      return `{${keys.map((key) => `${quote(key)}:${encode(value[key])}`).join(",")}}`;
    }
    throw new TypeError("ORF challenge encoding requires JSON values and safe integers, not floats.");
  };
  return new TextEncoder().encode(encode(payload));
}

/**
 * Encode a JCS unsigned event, including its signed encoding marker.
 * Use only in a user-trusted signer; partner code must never hold the user's key.
 * @param {Record<string, unknown>} payload
 * @returns {Uint8Array}
 */
export function canonicalEventJsonBytes(payload) {
  if (payload?.signature_encoding !== "jcs-rfc8785") {
    throw new TypeError("Event signing requires signature_encoding: jcs-rfc8785.");
  }
  if ("signature" in payload) throw new TypeError("Sign the unsigned envelope, not its signature field.");
  const quote = (value) => {
    for (let index = 0; index < value.length; index += 1) {
      const code = value.charCodeAt(index);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(++index);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw new TypeError("JCS rejects lone surrogates.");
      } else if (code >= 0xdc00 && code <= 0xdfff) {
        throw new TypeError("JCS rejects lone surrogates.");
      }
    }
    return JSON.stringify(value);
  };
  const encode = (value) => {
    if (value === null || typeof value === "boolean") return JSON.stringify(value);
    if (typeof value === "string") return quote(value);
    if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
    if (Array.isArray(value)) return `[${Array.from(value, encode).join(",")}]`;
    if (value && typeof value === "object" &&
        [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
      return `{${Object.keys(value).sort().map((key) => `${quote(key)}:${encode(value[key])}`).join(",")}}`;
    }
    throw new TypeError("JCS requires finite numbers and JSON values.");
  };
  return new TextEncoder().encode(encode(payload));
}

/**
 * Encode a Uint8Array as a URL-safe base64 string (no padding).
 * @param {Uint8Array} bytes
 * @returns {string}
 */
export function encodeBase64Url(bytes) {
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  const base64 = btoa(binary);
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

/** Native gateway login. Call directly from a user-click handler, with a backend-issued nonce.
 * The response is untrusted until the site's backend verifies and consumes its challenge.
 * This does not upload a profile or contact the hosted ORF service.
 */
export function connectNativeORF({ nonce, expiresAt }) {
  if (typeof window === "undefined" || window.top !== window ||
      typeof nonce !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(nonce) || !Number.isSafeInteger(expiresAt)) {
    return Promise.reject(new ORFClientError("A top-level page and valid backend challenge are required."));
  }
  const now = Math.floor(Date.now() / 1000);
  if (expiresAt <= now || expiresAt > now + 300) {
    return Promise.reject(new ORFClientError("The challenge must expire within five minutes."));
  }
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timeout); window.removeEventListener("message", receive); };
    const receive = (event) => {
      const data = event.data;
      if (event.source !== window || event.origin !== window.location.origin ||
          data?.channel !== "orf-native-v1" || data.direction !== "response" || data.request_id !== requestId) return;
      cleanup();
      if (data.status === "approved" && data.proof) resolve(data.proof);
      else reject(new ORFClientError(["denied", "revoked", "expired"].includes(data.status)
        ? `ORF request ${data.status}.` : "ORF Gateway is unavailable or rejected the request."));
    };
    const timeout = setTimeout(() => { cleanup(); reject(new ORFClientError(
      "ORF approval timed out. Enable the ORF browser extension and open ORF Gateway."));
    }, (expiresAt - now) * 1000 + 2000);
    window.addEventListener("message", receive);
    window.postMessage({channel: "orf-native-v1", direction: "request", request_id: requestId,
      nonce, expires_at: expiresAt}, window.location.origin);
  });
}

async function _fetch(method, url, body = null, extraHeaders = {}) {
  const headers = { Accept: "application/json", ...extraHeaders };
  const init = { method, headers };
  if (body !== null) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    throw new ORFClientError(`Unable to reach ORF service at ${url}`, null, String(err));
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ORFClientError(
      `ORF API call failed for ${method} ${url}`,
      response.status,
      payload?.detail ?? null,
    );
  }
  return payload;
}

/**
 * Browser-safe client for the Open Recommender hosted service.
 *
 * All methods return plain objects matching the service JSON response shapes
 * documented in docs/pilot-integration.md.
 */
export class ORFClient {
  /**
   * @param {string} baseUrl  Base URL of the running ORF service (no trailing slash).
   * @param {{ syncToken?: string }} [options]
   */
  constructor(baseUrl, options = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.syncToken = options.syncToken ?? null;
  }

  _url(path) {
    return `${this.baseUrl}${path}`;
  }

  async _request(method, path, body = null, extraHeaders = {}) {
    return _fetch(method, this._url(path), body, extraHeaders);
  }

  async _syncRequest(method, path, body = null) {
    const extra = this.syncToken
      ? { Authorization: `Bearer ${this.syncToken}` }
      : {};
    return _fetch(method, this._url(path), body, extra);
  }

  // ─── Access request flow ────────────────────────────────────────────────────

  /**
   * Create a site access request for a profile.
   *
   * @param {{ profileId: string, siteId: string, purpose: string, requestedScopes?: string[], requiredScopes?: string[], optionalScopes?: string[], expiresAt?: string }} params
   * @returns {Promise<object>}  { access_request, consent_review_url, … }
   */
  async createAccessRequest({
    profileId,
    siteId,
    purpose,
    requestedScopes,
    requiredScopes,
    optionalScopes,
    expiresAt,
  }) {
    const hasExplicitScopeTiers = requiredScopes !== undefined || optionalScopes !== undefined;
    if (hasExplicitScopeTiers && requestedScopes !== undefined) {
      throw new ORFClientError(
        "Use either requestedScopes or requiredScopes/optionalScopes, not both.",
      );
    }
    const body = { site_id: siteId, purpose };
    if (hasExplicitScopeTiers) {
      if (requiredScopes !== undefined) {
        body.required_scopes = requiredScopes;
      }
      if (optionalScopes !== undefined) {
        body.optional_scopes = optionalScopes;
      }
    } else {
      body.requested_scopes = requestedScopes ?? [];
    }
    if (expiresAt) body.expires_at = expiresAt;
    return this._request("POST", `/profiles/${profileId}/site-access-requests`, body);
  }

  /**
   * Register or update a profile document in the ORF service.
   * Pass original file text to preserve signed event numbers such as 1.0.
   * @param {object|string} profile
   * @returns {Promise<object>}
   */
  async upsertProfile(profile) {
    if (typeof profile === "string") {
      const parsed = JSON.parse(profile);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new ORFClientError("Profile JSON must be an object.");
      }
      return this._request("POST", "/profiles", `{"profile":${profile}}`);
    }
    return this._request("POST", "/profiles", { profile });
  }

  /** User-side client: sign the returned challenge outside site-controlled code. */
  async createOwnerActionChallenge({ profileId, action, targetId, parameters = {} }) {
    return this._request("POST", `/profiles/${profileId}/owner-action-challenges`, {
      action, target_id: targetId, parameters,
    });
  }

  /** Delete live hosted rows only; requires proof for action delete with parameters {}. */
  async deleteProfile(profileId, ownerProof) {
    return this._request("POST", `/profiles/${profileId}/delete`, { owner_proof: ownerProof });
  }

  async revokeGrant(grantId, { ownerProof, reason } = {}) {
    const body = { owner_proof: ownerProof };
    if (reason !== undefined) body.reason = reason;
    return this._request("POST", `/grants/${grantId}/revoke`, body);
  }

  /**
   * Get the current state of an access request.
   * @param {string} requestId
   * @returns {Promise<object>}
   */
  async getAccessRequest(requestId) {
    return this._request("GET", `/site-access-requests/${requestId}`);
  }

  /**
   * Begin the challenge exchange for an approved request.
   * @param {string} requestId
   * @returns {Promise<object>}  { challenge, challenge_payload, grant, … }
   */
  async startExchange(requestId) {
    return this._request("POST", `/site-access-requests/${requestId}/exchange`);
  }

  /**
   * Verify a signed challenge to obtain a grant session.
   *
   * @param {{ requestId: string, challengeId: string, signature: string, sessionExpiresAt?: string }} params
   * @returns {Promise<object>}  { verified, grant, session }
   */
  async verifySignature({ requestId, challengeId, signature, sessionExpiresAt }) {
    const body = { challenge_id: challengeId, signature };
    if (sessionExpiresAt) body.session_expires_at = sessionExpiresAt;
    return this._request("POST", `/site-access-requests/${requestId}/verify`, body);
  }

  /**
   * Fetch the consented projection for a grant session.
   * @param {string} sessionId
   * @returns {Promise<object>}  { projection }
   */
  async getProjection(sessionId) {
    return this._request("GET", `/grant-sessions/${sessionId}/projection`);
  }

  /**
   * Rerank site-generated candidates inside a verified grant session.
   * @param {string} sessionId
   * @param {{ candidates: Array<object>, topN?: number, includeDebug?: boolean, schemaVersion?: string }} params
   * @returns {Promise<object>}  { session, ranking }
   */
  async rankCandidates(sessionId, { candidates, topN, includeDebug = false, schemaVersion } = {}) {
    const body = {
      candidates,
      include_debug: includeDebug,
    };
    if (topN !== undefined) {
      body.top_n = topN;
    }
    if (schemaVersion) {
      body.schema_version = schemaVersion;
    }
    return this._request("POST", `/grant-sessions/${sessionId}/rank`, body);
  }

  /**
   * Read consent review data for a pending request from the localhost trust surface.
   * @param {string} requestId
   * @returns {Promise<object>}
   */
  async getConsentReview(requestId) {
    return this._request("GET", `/consent/site-access-requests/${requestId}/review-data`);
  }

  /**
   * Approve a consent request from the browser trust surface.
   * @param {{ requestId: string, approvedScopes?: string[], csrfToken: string, ownerProof: object }} params
   * @returns {Promise<object>}
   */
  async approveConsentRequest({ requestId, approvedScopes, csrfToken, ownerProof }) {
    const body = {};
    if (approvedScopes) {
      body.approved_scopes = approvedScopes;
    }
    body.owner_proof = ownerProof;
    return this._request(
      "POST",
      `/consent/site-access-requests/${requestId}/approve`,
      body,
      { "X-Open-Recommender-CSRF-Token": csrfToken },
    );
  }

  /**
   * Deny a consent request from the browser trust surface.
   * @param {{ requestId: string, reason?: string, csrfToken: string, ownerProof: object }} params
   * @returns {Promise<object>}
   */
  async denyConsentRequest({ requestId, reason, csrfToken, ownerProof }) {
    const body = {};
    if (reason !== undefined) {
      body.reason = reason;
    }
    body.owner_proof = ownerProof;
    return this._request(
      "POST",
      `/consent/site-access-requests/${requestId}/deny`,
      body,
      { "X-Open-Recommender-CSRF-Token": csrfToken },
    );
  }

  // ─── Public profile ─────────────────────────────────────────────────────────

  /**
   * Read the public projection for a profile (no auth required).
   * @param {string} profileId
   * @returns {Promise<object>}
   */
  async getPublicProfile(profileId) {
    return this._request("GET", `/profiles/${profileId}/public`);
  }

  // ─── Sync push / pull ───────────────────────────────────────────────────────

  /**
   * Push signed events to the hosted sync store.
   * Requires a syncToken when the service enforces auth.
   *
   * @param {string} profileId
   * @param {Array<object>} events
   * @returns {Promise<object>}
   */
  async pushEvents(profileId, events) {
    return this._syncRequest("POST", `/profiles/${profileId}/events`, { events });
  }

  /**
   * Owner-side only: raw history requires a one-time sync-read ownerProof
   * bound to this profile and exact after_clock; the token is an extra gate.
   *
   * @param {string} profileId
   * @param {{ afterClock?: number, ownerProof: object }} options
   * @returns {Promise<object>}
   */
  async pullEvents(profileId, { afterClock = 0, ownerProof } = {}) {
    if (!ownerProof) throw new ORFClientError("Raw history requires a sync-read owner proof.");
    return this._syncRequest("POST", `/profiles/${profileId}/events/read`, {
      after_clock: afterClock, owner_proof: ownerProof,
    });
  }
}
