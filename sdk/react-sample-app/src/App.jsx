import { useState, useCallback, useRef } from "react";
import {
  ORFClient,
  ORFClientError,
  canonicalJsonBytes,
  encodeBase64Url,
} from "@open-recommender/orf-web-sdk";

const DEFAULT_SERVICE_URL = "http://127.0.0.1:8000";
const DEFAULT_SITE_ID = "open-news-demo";
const DEFAULT_REQUIRED_SCOPES = ["profile.read", "topics.public"];
const DEFAULT_OPTIONAL_SCOPES = ["topics.selective:orf:media/podcasts"];
const DEMO_RANKING_CANDIDATES = [
  {
    candidateId: "podcast-spotlight",
    siteScore: 0.8,
    candidateTopics: ["orf:media/podcasts"],
    metadata: {
      headline: "Podcast spotlight: interviews worth queueing",
      slot: "hero",
      surface: "sample-feed",
    },
  },
  {
    candidateId: "python-roundup",
    siteScore: 0.74,
    candidateTopics: ["orf:technology/python"],
    metadata: {
      headline: "Python roundup for builders",
      slot: "secondary",
      surface: "sample-feed",
    },
  },
  {
    candidateId: "privacy-briefing",
    siteScore: 0.71,
    candidateTopics: ["orf:policy/privacy"],
    metadata: {
      headline: "Privacy briefing for the week",
      slot: "tertiary",
      surface: "sample-feed",
    },
  },
];

function buildDemoRankingCandidates() {
  return DEMO_RANKING_CANDIDATES.map((candidate) => ({
    candidate_id: candidate.candidateId,
    site_score: candidate.siteScore,
    candidate_topics: candidate.candidateTopics,
    metadata: candidate.metadata,
  }));
}

function browserSigninErrorMessage(err) {
  const wrapped = wrapError(err);
  if (err instanceof ORFClientError && err.status === 400 && err.detail === "Signature verification failed.") {
    return "Could not complete browser sign-in. The uploaded .orf.key does not match this profile, or the challenge is stale. Refresh the request status, start a new exchange, and try again.";
  }
  return `Could not complete browser sign-in. Make sure the uploaded .orf.key matches this profile. ${wrapped}`;
}

function pemToPkcs8Bytes(pemText) {
  const normalized = pemText.replace(/\r/g, "").trim();
  if (normalized.includes("ENCRYPTED PRIVATE KEY")) {
    throw new Error("Encrypted .orf.key files are not supported in this browser flow yet.");
  }
  const match = normalized.match(/-----BEGIN PRIVATE KEY-----([\s\S]+?)-----END PRIVATE KEY-----/);
  if (!match) {
    throw new Error("Expected an unencrypted PKCS#8 PEM private key.");
  }
  const base64 = match[1].replace(/\s+/g, "");
  const binary = atob(base64);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function importBrowserSigningKey(pemText) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("This browser does not expose Web Crypto signing APIs.");
  }
  return globalThis.crypto.subtle.importKey(
    "pkcs8",
    pemToPkcs8Bytes(pemText),
    { name: "Ed25519" },
    false,
    ["sign"],
  );
}

function RankingCard({ ranking, rankingError, onRank, loading }) {
  const rankingPayload = ranking?.ranking ?? null;
  const rankedCandidates = rankingPayload?.ranked_candidates ?? [];

  return (
    <div id="ranking-card" className="card" style={{ marginTop: 14 }}>
      <h2>Site reranking demo</h2>
      <p className="muted">
        This uses the verified grant session to rerank a fixed set of site-owned candidates. The UI
        only shows the ranking payload returned by the service plus site metadata echoed back per
        candidate.
      </p>

      <div style={{ marginTop: 16 }}>
        <h3>Sample site candidates</h3>
        <ul id="ranking-source-candidates">
          {DEMO_RANKING_CANDIDATES.map((candidate) => (
            <li key={candidate.candidateId}>
              <strong>{candidate.metadata.headline}</strong>
              <span className="muted">
                {" "}
                — site score {candidate.siteScore.toFixed(2)} · slot {candidate.metadata.slot}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <button id="rerank-candidates" onClick={onRank} disabled={loading}>
        {loading ? "Reranking…" : "Rerank sample feed"}
      </button>

      {rankingError && (
        <div className="banner banner-error" style={{ marginTop: 14 }}>
          <strong>Ranking failed:</strong> {rankingError}
        </div>
      )}

      {rankingPayload && (
        <div style={{ marginTop: 16 }}>
          <p className="muted">
            Grant session <code>{ranking.session?.session_id}</code> returned{" "}
            {rankedCandidates.length} of {rankingPayload.candidate_count} candidates.
          </p>
          <ol id="ranked-candidate-list">
            {rankedCandidates.map((candidate) => (
              <li key={candidate.candidate_id} style={{ marginBottom: 12 }}>
                <strong>{candidate.metadata?.headline ?? candidate.candidate_id}</strong>
                <div className="muted">
                  Score {candidate.score.toFixed(3)} · rank {candidate.rank}
                  {candidate.metadata?.slot ? ` · slot ${candidate.metadata.slot}` : ""}
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
                  {(candidate.reason_codes ?? []).map((reasonCode) => (
                    <span key={reasonCode} className="scope-tag">
                      {reasonCode}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

async function signChallengePayloadInBrowser(challengePayload, signingKey) {
  const signature = await globalThis.crypto.subtle.sign(
    { name: "Ed25519" },
    signingKey,
    canonicalJsonBytes(challengePayload),
  );
  return encodeBase64Url(new Uint8Array(signature));
}

function StatusBadge({ status }) {
  const cls = {
    pending: "badge-pending",
    approved: "badge-approved",
    denied: "badge-denied",
    exchange: "badge-exchange",
    done: "badge-done",
  }[status] ?? "badge-pending";
  return (
    <span id="request-status-badge" className={`badge ${cls}`}>
      {status}
    </span>
  );
}

function ErrorBanner({ error }) {
  if (!error) return null;
  return (
    <div className="banner banner-error">
      <strong>Error:</strong> {error}
    </div>
  );
}

function ProjectionCard({ projection }) {
  const proj = projection?.projection ?? {};
  const topics = proj.topics ?? [];
  return (
    <div id="projection-card" className="card">
      <h2>Consented projection</h2>
      {proj.display_name && (
        <p>
          <strong>Display name:</strong> {proj.display_name}
        </p>
      )}
      <p className="muted">
        These topics were returned by the ORF service based on the approved scopes.
      </p>
      {topics.length === 0 ? (
        <p className="muted">No topics in projection.</p>
      ) : (
        <ul>
          {topics.map((t, i) => (
            <li key={i}>
              <code>{t.topic}</code>
              {t.visibility && <span className="muted"> ({t.visibility})</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RequestCard({
  requestData,
  consentUrl,
  onRefresh,
  onExchange,
  loading,
  hasInlineConsent,
  hasSigningKey,
}) {
  if (!requestData) return null;
  const ar = requestData.access_request ?? requestData;
  const status = ar.status;
  return (
    <div id="request-card" className="card">
      <h2>
        Access request <StatusBadge status={status} />
      </h2>
      <p>
        <strong>ID:</strong> <code id="request-id">{ar.request_id}</code>
      </p>
      <p>
        <strong>Purpose:</strong> {ar.purpose}
      </p>
      <p>
        <strong>Required scopes:</strong>{" "}
        {(ar.required_scopes ?? []).map((s) => (
          <span key={s} className="scope-tag">
            {s}
          </span>
        ))}
        {(ar.required_scopes ?? []).length === 0 && <span className="muted">None</span>}
      </p>
      <p>
        <strong>Optional scopes:</strong>{" "}
        {(ar.optional_scopes ?? []).map((s) => (
          <span key={s} className="scope-tag">
            {s}
          </span>
        ))}
        {(ar.optional_scopes ?? []).length === 0 && <span className="muted">None</span>}
      </p>
      {ar.reused_prior_grant_id && (
        <p className="muted">
          This request reused a previously approved grant, so no extra consent step was required.
        </p>
      )}

      {status === "pending" && (
        <div className="banner banner-info" style={{ marginTop: 14 }}>
          <p style={{ margin: "0 0 8px" }}>
            {hasInlineConsent
              ? "Approve or deny below in this tab. The separate localhost review page is optional and not needed for the normal browser flow."
              : "The request is waiting for the user to approve it in the ORF trust app."}
          </p>
          {hasInlineConsent && hasSigningKey && (
            <p style={{ margin: "0 0 8px" }}>
              <strong>Heads up:</strong> you already loaded the local <code>.orf.key</code>, so
              after approval this tab can continue through signing and projection without opening
              the separate localhost review page.
            </p>
          )}
          {consentUrl && (
            <p style={{ margin: "0 0 8px" }}>
              <a id="consent-review-link" href={consentUrl} target="_blank" rel="noreferrer">
                Open separate localhost review page (optional) →
              </a>
            </p>
          )}
          <button
            id="refresh-request-status"
            className="secondary"
            onClick={onRefresh}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh status"}
          </button>
        </div>
      )}

      {status === "approved" && (
        <div style={{ marginTop: 14 }}>
          <p className="muted">
            The request is approved. Start the exchange to get the projection.
          </p>
          <button id="start-exchange" onClick={onExchange} disabled={loading}>
            {loading ? "Starting exchange…" : "Start exchange + get projection"}
          </button>
        </div>
      )}

      {status === "denied" && (
        <p className="muted" style={{ marginTop: 8 }}>
          This request was denied.
        </p>
      )}
    </div>
  );
}

function ConsentCard({
  reviewData,
  selectedScopes,
  onToggleScope,
  denyReason,
  onDenyReasonChange,
  onApprove,
  onDeny,
  loading,
}) {
  if (!reviewData) return null;
  const groups = reviewData.scope_groups ?? {
    required: [],
    optional_already_public: [],
    optional_newly_shared: [],
  };
  const preview = reviewData.projection_preview ?? {};
  const previewTopics = (preview.topics ?? []).filter((topic) => {
    if (topic.visibility === "public") {
      return selectedScopes.includes("topics.public");
    }
    if (topic.visibility === "selective") {
      return selectedScopes.includes(`topics.selective:${topic.topic}`);
    }
    return false;
  });

  const renderGroup = (title, items) => {
    if (!items.length) return null;
    return (
      <div style={{ marginTop: 12 }}>
        <h3 style={{ marginBottom: 8 }}>{title}</h3>
        {items.map((item) => (
          <label key={item.scope} className="scope-row" style={{ display: "grid", gridTemplateColumns: "20px 1fr", gap: 12, marginBottom: 10 }}>
            <input
              type="checkbox"
              checked={selectedScopes.includes(item.scope)}
              onChange={() => onToggleScope(item.scope)}
              disabled={Boolean(item.disabled)}
            />
            <span>
              <strong>{item.label}</strong>
              <br />
              <span className="muted">{item.description}</span>
              {item.required ? (
                <>
                  <br />
                  <span className="muted">Required for this sign-in request.</span>
                </>
              ) : null}
            </span>
          </label>
        ))}
      </div>
    );
  };

  return (
    <div id="inline-consent-card" className="card" style={{ marginTop: 14 }}>
      <h2>Consent review</h2>
      <p className="muted">
        This inline card is the default browser flow. The separate localhost review page is only an
        optional inspection view if you want to see the dedicated trust surface.
      </p>
      <p>
        <strong>Site:</strong> {reviewData.access_request?.site_name}
      </p>
      <p>
        <strong>Purpose:</strong> {reviewData.access_request?.purpose}
      </p>

      {renderGroup("Required to continue", groups.required ?? [])}
      {renderGroup("Optional already-public or identity-level", groups.optional_already_public ?? [])}
      {renderGroup("Optional newly shared with this site only", groups.optional_newly_shared ?? [])}

      <div style={{ marginTop: 16 }}>
        <h3>Preview</h3>
        {preview.display_name && selectedScopes.includes("profile.read") && (
          <p>
            <strong>Display name:</strong> {preview.display_name}
          </p>
        )}
        {previewTopics.length === 0 ? (
          <p className="muted">No topics would be shared with the currently selected scopes.</p>
        ) : (
          <ul>
            {previewTopics.map((topic) => (
              <li key={topic.topic}>
                <code>{topic.topic}</code>
                {topic.visibility ? <span className="muted"> ({topic.visibility})</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <label style={{ display: "block", marginTop: 16 }}>
        Deny reason (optional)
        <input
          id="deny-reason"
          type="text"
          value={denyReason}
          onChange={(e) => onDenyReasonChange(e.target.value)}
          placeholder="Why are you declining this request?"
        />
      </label>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 16 }}>
        <button id="approve-inline-consent" onClick={onApprove} disabled={loading || selectedScopes.length === 0}>
          {loading ? "Approving…" : "Approve required + selected optional scopes"}
        </button>
        <button id="deny-inline-consent" className="secondary" onClick={onDeny} disabled={loading}>
          {loading ? "Denying…" : "Deny request"}
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [serviceUrl, setServiceUrl] = useState(DEFAULT_SERVICE_URL);
  const [profileId, setProfileId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [importStatus, setImportStatus] = useState(null);
  const [signingKey, setSigningKey] = useState(null);
  const [signingKeyStatus, setSigningKeyStatus] = useState(null);

  const [requestData, setRequestData] = useState(null);
  const [consentUrl, setConsentUrl] = useState(null);
  const [projection, setProjection] = useState(null);
  const [ranking, setRanking] = useState(null);
  const [rankingError, setRankingError] = useState(null);
  const [flowStatus, setFlowStatus] = useState("idle");
  const [consentReview, setConsentReview] = useState(null);
  const [selectedScopes, setSelectedScopes] = useState([]);
  const [denyReason, setDenyReason] = useState("");

  const clientRef = useRef(null);

  function getClient() {
    if (!clientRef.current || clientRef.current.baseUrl !== serviceUrl.trim()) {
      clientRef.current = new ORFClient(serviceUrl.trim());
    }
    return clientRef.current;
  }

  function wrapError(err) {
    if (err instanceof ORFClientError) {
      return `${err.message}${err.status ? ` (HTTP ${err.status})` : ""}${err.detail ? ` – ${JSON.stringify(err.detail)}` : ""}`;
    }
    return String(err);
  }

  const loadConsentReview = useCallback(async (requestId) => {
    const review = await getClient().getConsentReview(requestId);
    setConsentReview(review);
    setSelectedScopes(
      [
        ...(review.scope_groups?.required ?? []),
        ...(review.scope_groups?.optional_already_public ?? []),
        ...(review.scope_groups?.optional_newly_shared ?? []),
      ]
        .filter((item) => item.checked)
        .map((item) => item.scope),
    );
    setDenyReason("");
  }, [serviceUrl]);

  const applyRequestState = useCallback(async (result) => {
    const status = result.access_request?.status ?? result.status ?? "pending";
    setRequestData(result);
    setConsentUrl(result.consent_review_url ?? null);
    setFlowStatus(status);
    if (status === "pending") {
      const requestId = result.access_request?.request_id ?? result.request_id;
      try {
        await loadConsentReview(requestId);
      } catch {
        setConsentReview(null);
        setSelectedScopes([]);
      }
    } else {
      setConsentReview(null);
      setSelectedScopes([]);
      setDenyReason("");
    }
  }, [loadConsentReview]);

  const handleProfileUpload = useCallback(async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setError(null);
    setImportStatus(`Importing ${file.name} into the ORF service…`);
    setLoading(true);
    try {
      const text = await file.text();
      const profileDocument = JSON.parse(text);
      const result = await getClient().upsertProfile(profileDocument);
      const importedProfileId = result.profile_id ?? result.public_profile?.profile_id ?? profileDocument.profile_id;
      setProfileId(importedProfileId);
      setImportStatus(`Imported ${file.name} and registered ${importedProfileId} in the ORF service.`);
    } catch (err) {
      setImportStatus(null);
      setError(`Could not import the selected .orf file. ${wrapError(err)}`);
    } finally {
      event.target.value = "";
      setLoading(false);
    }
  }, [serviceUrl]);

  const handleKeyUpload = useCallback(async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    setError(null);
    setLoading(true);
    try {
      const pemText = await file.text();
      const importedKey = await importBrowserSigningKey(pemText);
      setSigningKey(importedKey);
      setSigningKeyStatus(
        `Loaded ${file.name}. The private key stays in this browser tab and only the signature leaves the browser.`,
      );
    } catch (err) {
      setSigningKey(null);
      setSigningKeyStatus(null);
      setError(`Could not load the selected .orf.key file. ${wrapError(err)}`);
    } finally {
      event.target.value = "";
      setLoading(false);
    }
  }, []);

  const handleRequest = useCallback(async () => {
    const cleanedProfileId = profileId.trim();
    setError(null);
    setProjection(null);
    setRanking(null);
    setRankingError(null);
    setRequestData(null);
    setConsentReview(null);
    setSelectedScopes([]);
    setLoading(true);
    setFlowStatus("requesting");
    try {
      await getClient().getPublicProfile(cleanedProfileId);
      const result = await getClient().createAccessRequest({
        profileId: cleanedProfileId,
        siteId: DEFAULT_SITE_ID,
        purpose: "Personalize the Open Recommender React sample feed.",
        requiredScopes: DEFAULT_REQUIRED_SCOPES,
        optionalScopes: DEFAULT_OPTIONAL_SCOPES,
      });
      await applyRequestState(result);
    } catch (err) {
      if (err instanceof ORFClientError && err.status === 404) {
        setError(
          `Profile ${cleanedProfileId} is not registered in the ORF service yet. Upload the .orf file here, run "python -m open_recommender.cli sync-push profile.orf http://127.0.0.1:8000", or open http://127.0.0.1:8000/lens and click "Register or update in local service", then try again.`,
        );
      } else {
        setError(wrapError(err));
      }
      setFlowStatus("idle");
    } finally {
      setLoading(false);
    }
  }, [profileId, serviceUrl, applyRequestState]);

  const handleRefresh = useCallback(async () => {
    if (!requestData) return;
    setError(null);
    setLoading(true);
    try {
      const requestId = requestData.access_request?.request_id ?? requestData.request_id;
      const result = await getClient().getAccessRequest(requestId);
      await applyRequestState(result);
    } catch (err) {
      setError(wrapError(err));
    } finally {
      setLoading(false);
    }
  }, [requestData, serviceUrl, applyRequestState]);

  const handleApproveConsent = useCallback(async () => {
    if (!requestData || !consentReview) return;
    setError(null);
    setLoading(true);
    try {
      const requestId = requestData.access_request?.request_id ?? requestData.request_id;
      const result = await getClient().approveConsentRequest({
        requestId,
        approvedScopes: selectedScopes,
        csrfToken: consentReview.csrf_token,
      });
      await applyRequestState({ ...requestData, ...result, access_request: result.access_request });
    } catch (err) {
      setError(wrapError(err));
    } finally {
      setLoading(false);
    }
  }, [requestData, consentReview, selectedScopes, serviceUrl, applyRequestState]);

  const handleDenyConsent = useCallback(async () => {
    if (!requestData || !consentReview) return;
    setError(null);
    setLoading(true);
    try {
      const requestId = requestData.access_request?.request_id ?? requestData.request_id;
      const result = await getClient().denyConsentRequest({
        requestId,
        reason: denyReason.trim() || undefined,
        csrfToken: consentReview.csrf_token,
      });
      await applyRequestState({ ...requestData, ...result, access_request: result.access_request });
    } catch (err) {
      setError(wrapError(err));
    } finally {
      setLoading(false);
    }
  }, [requestData, consentReview, denyReason, serviceUrl, applyRequestState]);

  const completeBrowserSignIn = useCallback(async (exchangeOverride = null) => {
    const activeExchange = exchangeOverride ?? requestData?._exchange;
    if (!activeExchange) {
      return;
    }
    if (!signingKey) {
      setError("Upload the matching .orf.key file to finish sign-in in the browser.");
      return;
    }

    setError(null);
    setLoading(true);
    try {
      const requestId = requestData?.access_request?.request_id ?? requestData?.request_id;
      const signature = await signChallengePayloadInBrowser(
        activeExchange.challenge_payload,
        signingKey,
      );
      const verified = await getClient().verifySignature({
        requestId,
        challengeId: activeExchange.challenge.challenge_id,
        signature,
      });
      const projectionResult = await getClient().getProjection(verified.session.session_id);
      setProjection(projectionResult);
      setRequestData((prev) => ({ ...prev, _exchange: activeExchange, _verified: verified }));
      setRankingError(null);
      setRanking(null);
      setFlowStatus("done");
      try {
        const rankingResult = await getClient().rankCandidates(verified.session.session_id, {
          topN: DEMO_RANKING_CANDIDATES.length,
          candidates: buildDemoRankingCandidates(),
        });
        setRanking(rankingResult);
      } catch (rankErr) {
        setRankingError(wrapError(rankErr));
      }
    } catch (err) {
      setError(browserSigninErrorMessage(err));
      setFlowStatus("awaiting-signature");
    } finally {
      setLoading(false);
    }
  }, [requestData, serviceUrl, signingKey]);

  const handleRerank = useCallback(async () => {
    const sessionId =
      requestData?._verified?.session?.session_id ?? projection?.session?.session_id ?? null;
    if (!sessionId) {
      return;
    }

    setError(null);
    setRankingError(null);
    setLoading(true);
    try {
      const rankingResult = await getClient().rankCandidates(sessionId, {
        topN: DEMO_RANKING_CANDIDATES.length,
        candidates: buildDemoRankingCandidates(),
      });
      setRanking(rankingResult);
    } catch (err) {
      setRankingError(wrapError(err));
    } finally {
      setLoading(false);
    }
  }, [projection, requestData, serviceUrl]);

  const handleExchange = useCallback(async () => {
    if (!requestData) return;
    setError(null);
    setLoading(true);
    setFlowStatus("exchange");
    try {
      const requestId = requestData.access_request?.request_id ?? requestData.request_id;
      const exchange = await getClient().startExchange(requestId);
      setRequestData((prev) => ({ ...prev, _exchange: exchange }));
      if (signingKey) {
        await completeBrowserSignIn(exchange);
        return;
      }
      setFlowStatus("awaiting-signature");
    } catch (err) {
      setError(wrapError(err));
      setFlowStatus("approved");
    } finally {
      setLoading(false);
    }
  }, [requestData, serviceUrl, signingKey, completeBrowserSignIn]);

  const toggleScope = useCallback((scope) => {
    setSelectedScopes((current) =>
      current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope],
    );
  }, []);

  return (
    <main>
      <h1>Open Recommender – React SDK demo</h1>
      <p className="lead">
        Demonstrates the <code>@open-recommender/orf-web-sdk</code> browser SDK against a locally
        running ORF service. Import a local profile or enter a registered profile ID to start the
        access-request flow.
      </p>

      <ErrorBanner error={error} />

      <div className="grid">
        <div className="card">
          <h2>Service connection</h2>
          <label>
            ORF service URL
            <input
              id="service-url"
              type="text"
              value={serviceUrl}
              onChange={(e) => setServiceUrl(e.target.value)}
              placeholder="http://127.0.0.1:8000"
            />
          </label>
          <label style={{ marginTop: 12 }}>
            Upload local .orf file
            <input
              id="profile-file-upload"
              type="file"
              accept=".orf,application/json"
              onChange={handleProfileUpload}
              disabled={loading}
            />
          </label>
          {importStatus && (
            <p id="profile-import-status" className="muted" style={{ marginTop: 8 }}>
              {importStatus}
            </p>
          )}
          <label style={{ marginTop: 12 }}>
            Upload local .orf.key
            <input
              id="key-file-upload"
              type="file"
              accept=".key,.pem,application/x-pem-file"
              onChange={handleKeyUpload}
              disabled={loading}
            />
          </label>
          {signingKeyStatus && (
            <p id="key-import-status" className="muted" style={{ marginTop: 8 }}>
              {signingKeyStatus}
            </p>
          )}
          <label style={{ marginTop: 12 }}>
            Profile ID
            <input
              id="profile-id"
              type="text"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              placeholder="orf:profile:…"
            />
          </label>
          <p className="muted" style={{ marginTop: 8 }}>
            You can upload a local `.orf` file here or paste a profile ID that is already registered
            in the ORF service.
          </p>
          <p className="muted" style={{ marginTop: 8 }}>
            If you also upload the matching `.orf.key`, the browser can complete the challenge step
            locally without dropping you to CLI.
          </p>
          <button id="request-access" onClick={handleRequest} disabled={loading || !profileId.trim()}>
            {loading && flowStatus === "requesting" ? "Creating request…" : "Request personalized access"}
          </button>
        </div>

        <div className="card">
          <h2>Requested scopes</h2>
          <p className="muted">
            Site ID: <code>{DEFAULT_SITE_ID}</code>
          </p>
          <p className="muted" style={{ marginTop: 12 }}>
            Required for this demo:
          </p>
          <ul>
            {DEFAULT_REQUIRED_SCOPES.map((s) => (
              <li key={s}>
                <span className="scope-tag">{s}</span>
              </li>
            ))}
          </ul>
          <p className="muted" style={{ marginTop: 12 }}>
            Optional extras you can skip:
          </p>
          <ul>
            {DEFAULT_OPTIONAL_SCOPES.map((s) => (
              <li key={s}>
                <span className="scope-tag">{s}</span>
              </li>
            ))}
          </ul>
          <p className="muted" style={{ marginTop: 12 }}>
            Required scopes must all be approved for this request to continue. Optional scopes let
            the user trim the request without denying it entirely.
          </p>
          <p className="muted" style={{ marginTop: 12 }}>
            Approved grants are reused for the same profile, site, purpose, and covered scopes, so
            refreshing the app does not force repeat consent when the backend already has a valid
            grant.
          </p>
        </div>
      </div>

      {requestData && (
        <RequestCard
          requestData={requestData}
          consentUrl={consentUrl}
          onRefresh={handleRefresh}
          onExchange={handleExchange}
          loading={loading}
          hasInlineConsent={Boolean(consentReview)}
          hasSigningKey={Boolean(signingKey)}
        />
      )}

      {consentReview && requestData?.access_request?.status === "pending" && (
        <ConsentCard
          reviewData={consentReview}
          selectedScopes={selectedScopes}
          onToggleScope={toggleScope}
          denyReason={denyReason}
          onDenyReasonChange={setDenyReason}
          onApprove={handleApproveConsent}
          onDeny={handleDenyConsent}
          loading={loading}
        />
      )}

      {requestData?._exchange && flowStatus === "awaiting-signature" && (
        <div id="exchange-card" className="card" style={{ marginTop: 14 }}>
          <h2>Challenge exchange started</h2>
          <p className="muted">
            The exchange returned a <code>challenge_payload</code>. Finish sign-in in this browser
            by uploading the matching <code>.orf.key</code> file and signing locally. Only the
            signature is sent to the ORF service.
          </p>
          <p>
            <strong>Challenge ID:</strong>{" "}
            <code id="challenge-id">{requestData._exchange.challenge?.challenge_id}</code>
          </p>
          {!signingKey && (
            <label style={{ display: "block", marginTop: 16 }}>
              Upload local .orf.key to finish sign-in
              <input
                id="exchange-key-file-upload"
                type="file"
                accept=".key,.pem,application/x-pem-file"
                onChange={handleKeyUpload}
                disabled={loading}
              />
            </label>
          )}
          {signingKeyStatus && (
            <p className="muted" style={{ marginTop: 8 }}>
              {signingKeyStatus}
            </p>
          )}
          <button
            id="complete-browser-signin"
            onClick={() => completeBrowserSignIn()}
            disabled={loading || !signingKey}
          >
            {loading ? "Completing sign-in…" : "Complete sign-in in browser"}
          </button>
        </div>
      )}

      {projection && <ProjectionCard projection={projection} />}
      {projection && (
        <RankingCard
          ranking={ranking}
          rankingError={rankingError}
          onRank={handleRerank}
          loading={loading && Boolean(projection)}
        />
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <h2>Role split in this localhost demo</h2>
        <ul className="muted">
          <li>
            <strong>Browser app</strong> — imports the local profile into the ORF service, creates
            requests, renders consent, and can sign the challenge locally when you upload the
            matching `.orf.key`.
          </li>
          <li>
            <strong>ORF service</strong> — stores registered profiles, persists grants, reuses valid
            grants, enforces consented projections, and reranks site candidates inside the verified
            grant-session boundary.
          </li>
          <li>
            <strong>User-side signer</strong> — in this demo, that signer can live in the same
            browser tab as long as the private key stays local and only the signature leaves the
            browser.
          </li>
        </ul>
      </div>
    </main>
  );
}
