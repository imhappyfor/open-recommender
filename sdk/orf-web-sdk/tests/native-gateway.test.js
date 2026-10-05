import assert from "node:assert/strict";
import {test} from "node:test";
import {readFileSync} from "node:fs";
import {createContext, runInContext} from "node:vm";
import {createHash, createPublicKey, verify} from "node:crypto";
import {canonicalJsonBytes, connectNativeORF} from "../src/index.js";

const background = readFileSync(new URL("../../../native/macos/Extension/Resources/background.js", import.meta.url), "utf8");
const content = readFileSync(new URL("../../../native/macos/Extension/Resources/content.js", import.meta.url), "utf8");
const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/native-login-proof.json", import.meta.url)));
const id = "00000000-0000-4000-8000-000000000001";
const makeMessage = () => ({operation: "begin", request_id: id,
  nonce: fixture.payload.nonce, expires_at: Math.floor(Date.now() / 1000) + 120});

function bridge({frameId = 0, nativeResult = {status: "approved", proof: fixture}, navigation = false, chrome = false} = {}) {
  let handler;
  const calls = [];
  let reads = 0;
  const sender = {frameId, url: "https://news.example/article", tab: {id: 1}};
  const browser = {tabs: {get: async () => ({id: 1, url: navigation && ++reads > 1 ? "https://evil.example/" : sender.url})},
    runtime: {onMessage: {addListener: callback => {handler = callback;}},
      sendNativeMessage: async (app, payload) => {calls.push(payload); return nativeResult;}}};
  runInContext(background, createContext({...chrome ? {chrome: browser} : {browser}, URL, Date, setTimeout, console}));
  return {calls, sender, send: message => new Promise(resolve => handler(message, sender, resolve))};
}

test("native Swift proof vector verifies in Node with domain-separated bytes", () => {
  const key = createPublicKey({format: "jwk", key: {kty: "OKP", crv: "Ed25519", x: fixture.payload.public_key}});
  const bytes = Buffer.concat([Buffer.from("ORF native connect v1\n"), canonicalJsonBytes(fixture.payload)]);
  assert.ok(verify(null, bytes, key, Buffer.from(fixture.signature, "base64url")));
});

test("background obtains origin from browser metadata and exports only approved proof", async () => {
  const api = bridge({nativeResult: {status: "approved", proof: fixture, profile_id: "secret", event_log: ["secret"]}});
  const result = await api.send(makeMessage());
  assert.equal(result.status, "approved");
  assert.deepEqual(Object.keys(result).sort(), ["proof", "status"]);
  assert.equal(api.calls[0].origin, "https://news.example");
  assert.equal(api.calls[0].operation, "begin");
  assert.ok(!JSON.stringify(result).includes("secret"));
});

test("Chrome API adapter works and native host allowlist matches the extension's public packaging key", async () => {
  const result = await bridge({chrome: true, nativeResult: {status: "pending"}}).send(makeMessage());
  assert.equal(result.status, "pending");
  const manifest = JSON.parse(readFileSync(new URL("../../../native/chrome/manifest.json", import.meta.url)));
  const digest = createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest("hex").slice(0, 32);
  const extensionID = [...digest].map(value => String.fromCharCode(97 + parseInt(value, 16))).join("");
  assert.equal(extensionID, "lialdifcbcjmjahaicjmkmklmfjoelom");
  assert.deepEqual(manifest.permissions, ["nativeMessaging"]);
  assert.equal(manifest.background.service_worker, "background.js");
  assert.equal(manifest.content_scripts[0].all_frames, false);
});

test("spoofed origins, extra fields, subframes and expired requests never reach native app", async () => {
  for (const message of [{...makeMessage(), origin: "https://shop.example"},
    {...makeMessage(), expires_at: 1}, {...makeMessage(), nonce: "short"},
    {...makeMessage(), request_id: "../file"}, {...makeMessage(), scopes: ["profile.read"]}]) {
    const api = bridge();
    assert.equal((await api.send(message)).status, "error");
    assert.equal(api.calls.length, 0);
  }
  const frame = bridge({frameId: 1});
  assert.equal((await frame.send(makeMessage())).status, "error");
  assert.equal(frame.calls.length, 0);
});

test("navigation and native denial do not release a login proof", async () => {
  assert.equal((await bridge({navigation: true}).send(makeMessage())).status, "error");
  const denied = await bridge({nativeResult: {status: "denied", proof: fixture}}).send(makeMessage());
  assert.equal(denied.status, "denied");
  assert.equal(denied.proof, undefined);
  const unsafe = structuredClone(fixture);
  unsafe.payload.profile_id = "master-secret";
  const rejected = await bridge({nativeResult: {status: "approved", proof: unsafe}}).send(makeMessage());
  assert.equal(rejected.status, "error");
  assert.ok(!JSON.stringify(rejected).includes("master-secret"));
});

test("content script requires current-page origin, top frame and user activation", async () => {
  for (const [activation, sourceMatches, originMatches, expected] of [
    [true, true, true, 1], [false, true, true, 0], [true, false, true, 0], [true, true, false, 0]]) {
    let handler; let calls = 0;
    const page = {addEventListener: (_type, callback) => {handler = callback;}, postMessage: () => {}};
    page.top = page;
    const browser = {runtime: {sendMessage: async () => {calls++; return {status: "denied"};}}};
    runInContext(content, createContext({window: page, location: {origin: "https://news.example"},
      navigator: {userActivation: {isActive: activation}}, browser}));
    await handler({source: sourceMatches ? page : {}, origin: originMatches ? "https://news.example" : "https://evil.example",
      data: {channel: "orf-native-v1", direction: "request", request_id: id,
        nonce: fixture.payload.nonce, expires_at: Math.floor(Date.now() / 1000) + 120}});
    assert.equal(calls, expected);
  }
});

test("SDK resolves correlated response and removes its listener", async () => {
  const previous = globalThis.window;
  let handler; let removed = 0;
  const page = {location: {origin: "https://news.example"},
    addEventListener: (_type, callback) => {handler = callback;}, removeEventListener: () => {removed++;},
    postMessage: request => queueMicrotask(() => handler({source: page, origin: page.location.origin,
      data: {...request, direction: "response", status: "approved", proof: fixture}}))};
  page.top = page; globalThis.window = page;
  try {
    assert.deepEqual(await connectNativeORF({nonce: fixture.payload.nonce, expiresAt: Math.floor(Date.now() / 1000) + 120}), fixture);
    assert.equal(removed, 1);
    await assert.rejects(connectNativeORF({nonce: "bad", expiresAt: 1}));
  } finally { globalThis.window = previous; }
});
