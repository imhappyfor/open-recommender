import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { generateKeyPairSync } from "node:crypto";
import { writeFile, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

import puppeteer from "puppeteer";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const APP_DIR = resolve(TEST_DIR, "..");
const MOCK_API_PORT = 8787;
const APP_PORT = 4173;
const SERVICE_URL = `http://127.0.0.1:${MOCK_API_PORT}`;
const APP_URL = `http://127.0.0.1:${APP_PORT}`;

function spawnProcess(command, args, options = {}) {
  return spawn(command, args, {
    stdio: "inherit",
    ...options,
  });
}

async function waitForHttp(url, attempts = 60, sleepMs = 500) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(url, { method: "GET" });
      if (response.ok) {
        return;
      }
    } catch {
      // keep waiting
    }
    await delay(sleepMs);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function jsonResponse(res, statusCode, payload, origin) {
  if (origin) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
  }
  res.setHeader("Content-Type", "application/json");
  res.writeHead(statusCode);
  res.end(JSON.stringify(payload));
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(Buffer.from(chunk));
  }
  const text = Buffer.concat(chunks).toString("utf-8") || "{}";
  return JSON.parse(text);
}

function createMockOrfApi() {
  const requests = new Map();
  let nextId = 1;

  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", SERVICE_URL);
    const origin = req.headers.origin || "*";

    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type,X-Open-Recommender-CSRF-Token");
    res.setHeader("Vary", "Origin");

    if (req.method === "OPTIONS") {
      res.writeHead(204);
      res.end();
      return;
    }

    if (req.method === "GET" && url.pathname === "/health") {
      jsonResponse(res, 200, { status: "ok" }, origin);
      return;
    }

    if (req.method === "POST" && url.pathname === "/profiles") {
      const body = await readJson(req);
      const profile = body.profile ?? {};
      jsonResponse(res, 200, {
        profile_id: profile.profile_id,
        public_profile: {
          profile_id: profile.profile_id,
          display_name: profile.display_name,
          topics: [],
        },
      }, origin);
      return;
    }

    if (req.method === "GET" && url.pathname.match(/^\/profiles\/[^/]+\/public$/)) {
      const profileId = decodeURIComponent(url.pathname.split("/")[2]);
      if (profileId !== "orf:profile:demo") {
        jsonResponse(res, 404, { detail: "Profile not found." }, origin);
        return;
      }
      jsonResponse(res, 200, {
        profile_id: profileId,
        display_name: "Alice Example",
        topics: [{ topic: "orf:technology/python", weight: 0.9, visibility: "public" }],
      }, origin);
      return;
    }

    if (req.method === "POST" && url.pathname.match(/^\/profiles\/[^/]+\/site-access-requests$/)) {
      const profileId = decodeURIComponent(url.pathname.split("/")[2]);
      const body = await readJson(req);
      const requestId = `request-${nextId++}`;
      const record = {
        profile_id: profileId,
        access_request: {
          request_id: requestId,
          site_id: body.site_id,
          site_name: "Open News Demo",
          purpose: body.purpose,
          required_scopes: body.required_scopes ?? [],
          optional_scopes: body.optional_scopes ?? [],
          requested_scopes: [...(body.required_scopes ?? []), ...(body.optional_scopes ?? []), ...(body.requested_scopes ?? [])],
          ignored_requested_scopes: [],
          status: "pending",
        },
        consent_review_url: `${SERVICE_URL}/consent/site-access-requests/${requestId}`,
        approved: false,
      };
      requests.set(requestId, record);
      jsonResponse(res, 200, {
        profile_id: profileId,
        access_request: record.access_request,
        consent_review_url: record.consent_review_url,
      }, origin);
      return;
    }

    if (req.method === "GET" && url.pathname.match(/^\/consent\/site-access-requests\/[^/]+\/review-data$/)) {
      const requestId = decodeURIComponent(url.pathname.split("/")[3]);
      const record = requests.get(requestId);
      if (!record) {
        jsonResponse(res, 404, { detail: "Request not found." }, origin);
        return;
      }
      jsonResponse(res, 200, {
        profile_id: record.profile_id,
        access_request: record.access_request,
        csrf_token: "csrf-1",
        scope_groups: {
          required: [
            {
              scope: "profile.read",
              label: "Basic profile identity",
              description: "Lets the site identify this portable profile without creating a site-specific account.",
              checked: true,
              required: true,
              disabled: true,
            },
            {
              scope: "topics.public",
              label: "Public topics already shareable to sites",
              description: "Lets the site read topics you already expose in your public ORF view.",
              checked: true,
              required: true,
              disabled: true,
            },
          ],
          optional_already_public: [],
          optional_newly_shared: [
            {
              scope: "topics.selective:orf:media/podcasts",
              label: "Selected topic: Media / Podcasts",
              description: "Lets the site read one specific selective topic only if you approve it here.",
              checked: true,
              required: false,
              disabled: false,
            },
          ],
        },
        projection_preview: {
          display_name: "Alice Example",
          topics: [
            { topic: "orf:technology/python", visibility: "public" },
            { topic: "orf:media/podcasts", visibility: "selective" },
          ],
        },
      }, origin);
      return;
    }

    if (req.method === "POST" && url.pathname.match(/^\/consent\/site-access-requests\/[^/]+\/approve$/)) {
      const requestId = decodeURIComponent(url.pathname.split("/")[3]);
      const record = requests.get(requestId);
      if (!record) {
        jsonResponse(res, 404, { detail: "Request not found." }, origin);
        return;
      }
      const body = await readJson(req);
      record.approved = true;
      record.access_request.status = "approved";
      record.access_request.approved_scopes = body.approved_scopes ?? [];
      jsonResponse(res, 200, {
        profile_id: record.profile_id,
        access_request: record.access_request,
        grant: {
          grant_id: "grant-1",
          request_id: requestId,
          approved_scopes: record.access_request.approved_scopes,
        },
      }, origin);
      return;
    }

    if (req.method === "POST" && url.pathname.match(/^\/consent\/site-access-requests\/[^/]+\/deny$/)) {
      const requestId = decodeURIComponent(url.pathname.split("/")[3]);
      const record = requests.get(requestId);
      if (!record) {
        jsonResponse(res, 404, { detail: "Request not found." }, origin);
        return;
      }
      const body = await readJson(req);
      record.access_request.status = "denied";
      record.access_request.denial_reason = body.reason ?? null;
      jsonResponse(res, 200, {
        profile_id: record.profile_id,
        access_request: record.access_request,
      }, origin);
      return;
    }

    if (req.method === "GET" && url.pathname.match(/^\/site-access-requests\/[^/]+$/)) {
      const requestId = decodeURIComponent(url.pathname.split("/")[2]);
      const record = requests.get(requestId);
      if (!record) {
        jsonResponse(res, 404, { detail: "Request not found." }, origin);
        return;
      }
      jsonResponse(res, 200, {
        profile_id: record.profile_id,
        access_request: record.access_request,
        consent_review_url: record.consent_review_url,
      }, origin);
      return;
    }

    if (req.method === "POST" && url.pathname.match(/^\/site-access-requests\/[^/]+\/exchange$/)) {
      const requestId = decodeURIComponent(url.pathname.split("/")[2]);
      const record = requests.get(requestId);
      if (!record) {
        jsonResponse(res, 404, { detail: "Request not found." }, origin);
        return;
      }
      if (!record.approved) {
        jsonResponse(res, 400, { detail: "Request is not approved yet." }, origin);
        return;
      }
      jsonResponse(res, 200, {
        access_request: record.access_request,
        grant: {
          grant_id: "grant-1",
          request_id: requestId,
          approved_scopes: record.access_request.requested_scopes,
        },
        challenge: {
          challenge_id: "challenge-1",
          profile_id: record.profile_id,
        },
        challenge_payload: {
          challenge_id: "challenge-1",
          profile_id: record.profile_id,
          nonce: "abc123",
          created_at: "2026-05-23T00:00:00Z",
        },
      }, origin);
      return;
    }

    if (req.method === "POST" && url.pathname.match(/^\/site-access-requests\/[^/]+\/verify$/)) {
      jsonResponse(res, 200, {
        verified: true,
        grant: {
          grant_id: "grant-1",
          request_id: "request-1",
          approved_scopes: ["profile.read", "topics.public"],
        },
        session: {
          session_id: "session-1",
        },
      }, origin);
      return;
    }

    if (req.method === "GET" && url.pathname.match(/^\/grant-sessions\/[^/]+\/projection$/)) {
      jsonResponse(res, 200, {
        projection: {
          site_id: "open-news-demo",
          grant_id: "grant-1",
          display_name: "Alice Example",
          topics: [{ topic: "orf:technology/python", visibility: "public" }],
        },
      }, origin);
      return;
    }

    jsonResponse(res, 404, { detail: `No mock route for ${req.method} ${url.pathname}` }, origin);
  });

  return { server };
}

function startPreviewServer() {
  return spawnProcess("npm", ["run", "preview", "--", "--host", "127.0.0.1", `--port=${APP_PORT}`], {
    cwd: APP_DIR,
    env: { ...process.env, CI: "1" },
  });
}

async function main() {
  const build = spawnProcess("npm", ["run", "build"], { cwd: APP_DIR, env: { ...process.env, CI: "1" } });
  const buildExit = await onceExit(build);
  if (buildExit !== 0) {
    throw new Error("Vite build failed.");
  }

  const mock = createMockOrfApi();
  const preview = startPreviewServer();
  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
  const tempProfilePath = resolve(tmpdir(), `orf-react-sample-${Date.now()}.orf`);
  const tempKeyPath = resolve(tmpdir(), `orf-react-sample-${Date.now()}.orf.key`);

  try {
    const { privateKey } = generateKeyPairSync("ed25519");
    await writeFile(
      tempProfilePath,
      JSON.stringify({
        profile_id: "orf:profile:demo",
        display_name: "Alice Example",
        topics: [],
        opt_out_topics: [],
        consent: {
          share_public_topics: true,
          ad_personalization: true,
          hosted_sync: false,
        },
      }),
      "utf-8",
    );
    await writeFile(
      tempKeyPath,
      privateKey.export({ type: "pkcs8", format: "pem" }),
      "utf-8",
    );

    await new Promise((resolve, reject) => {
      mock.server.listen(MOCK_API_PORT, "127.0.0.1", resolve);
      mock.server.on("error", reject);
    });
    await waitForHttp(`${SERVICE_URL}/health`);
    await waitForHttp(APP_URL);

    const page = await browser.newPage();
    const consoleErrors = [];
    page.on("pageerror", (error) => consoleErrors.push(error.message));

    await page.goto(APP_URL, { waitUntil: "networkidle2" });
    await page.locator("#service-url").fill(SERVICE_URL);
    const uploadInput = await page.waitForSelector("#profile-file-upload");
    await uploadInput.uploadFile(tempProfilePath);
    await page.waitForFunction(
      () => document.querySelector("#profile-import-status")?.textContent?.includes("Imported"),
    );
    await page.waitForFunction(
      () => document.querySelector("#profile-id")?.value === "orf:profile:demo",
    );
    const keyUploadInput = await page.waitForSelector("#key-file-upload");
    await keyUploadInput.uploadFile(tempKeyPath);
    await page.waitForFunction(
      () => document.querySelector("#key-import-status")?.textContent?.includes("Loaded"),
    );

    await page.locator("#profile-id").fill("orf:profile:missing");
    await page.locator("#request-access").click();
    await page.waitForFunction(
      () => document.querySelector(".banner-error")?.textContent?.includes("not registered in the ORF service yet"),
    );
    assert.match(
      await page.$eval(".banner-error", (node) => node.textContent ?? ""),
      /not registered in the ORF service yet/,
    );

    await page.locator("#profile-id").fill("orf:profile:demo");
    await page.locator("#request-access").click();

    await page.waitForSelector("#request-card");
    await page.waitForFunction(
      () => document.querySelector("#request-status-badge")?.textContent === "pending",
    );
    await page.waitForSelector("#inline-consent-card");
    await page.locator("#approve-inline-consent").click();
    await page.waitForFunction(
      () => document.querySelector("#request-status-badge")?.textContent === "approved",
    );

    await page.locator("#start-exchange").click();
    await page.waitForSelector("#projection-card");
    assert.match(
      await page.$eval("#projection-card", (node) => node.textContent ?? ""),
      /Alice Example/,
    );

    assert.deepEqual(consoleErrors, []);
    await page.close();
  } finally {
    await browser.close();
    mock.server.close();
    preview.kill("SIGTERM");
    await rm(tempProfilePath, { force: true });
    await rm(tempKeyPath, { force: true });
  }
}

function onceExit(child) {
  return new Promise((resolve, reject) => {
    child.once("exit", (code) => resolve(code ?? 1));
    child.once("error", reject);
  });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
