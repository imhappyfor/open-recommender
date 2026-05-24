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
 * Canonical JSON encoding for signature verification payloads.
 * Mirrors the server-side canonical_json helper.
 * @param {Record<string, unknown>} payload
 * @returns {Uint8Array}
 */
export function canonicalJsonBytes(payload) {
  const normalize = (value) => {
    if (Array.isArray(value)) {
      return value.map(normalize);
    }
    if (value && typeof value === "object") {
      return Object.keys(value)
        .sort()
        .reduce((acc, key) => {
          acc[key] = normalize(value[key]);
          return acc;
        }, {});
    }
    return value;
  };
  const text = JSON.stringify(normalize(payload));
  return new TextEncoder().encode(text);
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

async function _fetch(method, url, body = null, extraHeaders = {}) {
  const headers = { Accept: "application/json", ...extraHeaders };
  const init = { method, headers };
  if (body !== null) {
    init.body = JSON.stringify(body);
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
   * @param {object} profile
   * @returns {Promise<object>}
   */
  async upsertProfile(profile) {
    return this._request("POST", "/profiles", { profile });
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
   * Read consent review data for a pending request from the localhost trust surface.
   * @param {string} requestId
   * @returns {Promise<object>}
   */
  async getConsentReview(requestId) {
    return this._request("GET", `/consent/site-access-requests/${requestId}/review-data`);
  }

  /**
   * Approve a consent request from the browser trust surface.
   * @param {{ requestId: string, approvedScopes?: string[], csrfToken: string }} params
   * @returns {Promise<object>}
   */
  async approveConsentRequest({ requestId, approvedScopes, csrfToken }) {
    const body = {};
    if (approvedScopes) {
      body.approved_scopes = approvedScopes;
    }
    return this._request(
      "POST",
      `/consent/site-access-requests/${requestId}/approve`,
      body,
      { "X-Open-Recommender-CSRF-Token": csrfToken },
    );
  }

  /**
   * Deny a consent request from the browser trust surface.
   * @param {{ requestId: string, reason?: string, csrfToken: string }} params
   * @returns {Promise<object>}
   */
  async denyConsentRequest({ requestId, reason, csrfToken }) {
    const body = {};
    if (reason) {
      body.reason = reason;
    }
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
   * Pull signed events from the hosted sync store.
   * Requires a syncToken when the service enforces auth.
   *
   * @param {string} profileId
   * @param {{ afterClock?: number }} [options]
   * @returns {Promise<object>}
   */
  async pullEvents(profileId, { afterClock = 0 } = {}) {
    const qs = afterClock ? `?after_clock=${afterClock}` : "";
    return this._syncRequest("GET", `/profiles/${profileId}/events${qs}`);
  }
}
