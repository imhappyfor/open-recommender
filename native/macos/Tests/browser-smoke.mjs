// Exercises the real demo page/SDK/content/background chain with a TEST-ONLY native transport.
// Does not install a Safari extension, access Keychain, or prove Safari's isolation/signing behavior.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createHash, generateKeyPairSync, sign} from "node:crypto";
import {readFile} from "node:fs/promises";
import {createServer} from "node:net";
import {createRequire} from "node:module";
import {fileURLToPath} from "node:url";
import {createContext, runInContext} from "node:vm";
import {setTimeout as delay} from "node:timers/promises";
import {canonicalJsonBytes} from "../../../sdk/orf-web-sdk/src/index.js";

const require = createRequire(new URL("../../../sdk/react-sample-app/package.json", import.meta.url));
const puppeteer = require("puppeteer").default;
const root = fileURLToPath(new URL("../../../", import.meta.url));
const portProbe = createServer();
await new Promise(resolve => portProbe.listen(0, "127.0.0.1", resolve));
const port = portProbe.address().port;
await new Promise(resolve => portProbe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.env.ORF_TEST_PYTHON || "python3", ["-m", "uvicorn",
  "examples.native_site:create_native_site_app", "--factory", "--host", "127.0.0.1", "--port", String(port)],
  {cwd: root, env: {...process.env, PYTHONPATH: `${root}/src`, ORF_NATIVE_DEMO_ORIGIN: origin}, stdio: ["ignore", "ignore", "pipe"]});
let serverLog = "";
server.stderr.on("data", chunk => {serverLog += chunk.toString();});
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    if (server.exitCode !== null) throw new Error(serverLog);
    try {if ((await fetch(origin)).ok) {ready = true; break;}} catch {}
    await delay(100);
  }
  assert.ok(ready, "Native demo server must start");
  // Uses only synthetic data; match the existing repo harness on CI without a Chromium sandbox.
  browser = await puppeteer.launch({headless: "shell", args: ["--no-sandbox"]});
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const keys = generateKeyPairSync("ed25519");
  const publicKey = keys.publicKey.export({format: "jwk"}).x;
  let handler; let approval = "pending";
  const requests = new Map();
  const calls = [];
  const api = {tabs: {get: async () => ({id: 1, url: page.url()})}, runtime: {
    onMessage: {addListener: callback => {handler = callback;}},
    sendNativeMessage: async (_app, message) => {
      calls.push(message);
      if (message.operation === "begin") {requests.set(message.request_id, message); return {status: "pending"};}
      const request = requests.get(message.request_id);
      if (approval !== "approved") return {status: approval};
      const payload = {version: "orf-native-connect-v1", audience: request.origin, nonce: request.nonce,
        subject: "orf:site:" + createHash("sha256").update(Buffer.from(publicKey, "base64url")).digest("hex").slice(0, 32),
        public_key: publicKey, issued_at: Math.floor(Date.now() / 1000), expires_at: request.expires_at};
      return {status: "approved", proof: {payload, signature: sign(null,
        Buffer.concat([Buffer.from("ORF native connect v1\n"), canonicalJsonBytes(payload)]), keys.privateKey).toString("base64url")}};
    }
  }};
  runInContext(await readFile(new URL("../Extension/Resources/background.js", import.meta.url), "utf8"),
    createContext({browser: api, URL, Date, setTimeout}));
  await page.exposeFunction("orfTestBridge", message => new Promise(resolve => handler(message,
    {frameId: 0, tab: {id: 1}, url: page.url()}, resolve)));
  await page.evaluateOnNewDocument(() => {window.browser = {runtime: {sendMessage: message => window.orfTestBridge(message)}};});
  await page.evaluateOnNewDocument(await readFile(new URL("../Extension/Resources/content.js", import.meta.url), "utf8"));
  await page.goto(origin);
  await page.waitForFunction(() => !document.querySelector("#connect").disabled);
  await page.click("#connect");
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("Open ORF Gateway"));
  await delay(1200);
  assert.equal(await page.$eval("#result", node => node.hidden), true, "Pending request cannot expose a proof");
  approval = "approved";
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("identity verified"));
  const verified = JSON.parse(await page.$eval("#result", node => node.textContent));
  assert.equal(verified.origin, origin);
  assert.ok(verified.site_subject.startsWith("orf:site:"));
  assert.ok(!JSON.stringify(verified).includes("profile_id"));
  assert.equal(calls[0].origin, origin);
  for (const width of [1280, 375, 320]) {
    await page.setViewport({width, height: 900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.ok(await page.$eval("#connect", node => node.getBoundingClientRect().height >= 44));
  }
  await page.waitForFunction(() => !document.querySelector("#connect").disabled);
  approval = "denied";
  await page.click("#connect");
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("denied"));
  assert.equal(await page.$eval("#result", node => node.hidden), true);
  assert.deepEqual(errors, []);
  process.stdout.write("Native demo browser flow passed (native transport stubbed; Safari installation not tested).\n");
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
  if (server.exitCode === null) await new Promise(resolve => server.once("exit", resolve));
}
