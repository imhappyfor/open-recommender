// Real Chrome extension + Swift stdio host; synthetic approval driver, never the user's Keychain.
import assert from "node:assert/strict";
import {spawn, spawnSync} from "node:child_process";
import {mkdtemp, readFile, readdir, rm} from "node:fs/promises";
import {createServer} from "node:net";
import {createRequire} from "node:module";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import {setTimeout as delay} from "node:timers/promises";

const require = createRequire(new URL("../../../sdk/react-sample-app/package.json", import.meta.url));
const puppeteer = require("puppeteer").default;
const root = fileURLToPath(new URL("../../../", import.meta.url));
const host = join(root, "native/macos/.build/ORF Gateway.app/Contents/MacOS/orf-chrome-host");
const extension = join(root, "native/macos/.build/chrome-extension");
const extensionID = "lialdifcbcjmjahaicjmkmklmfjoelom";
const temporary = await mkdtemp(join(tmpdir(), "orf-chrome-test-"));
const profile = join(temporary, "browser");
const mailbox = join(temporary, "mailbox");
const env = {...process.env, ORF_GATEWAY_TEST_DIRECTORY: mailbox};
let browser, server;

function hostCommand(args, input) {
  const result = spawnSync(host, args, {env, input});
  assert.ifError(result.error);
  return result;
}
function nativeMessage(message, caller = `chrome-extension://${extensionID}/`) {
  const body = Buffer.from(JSON.stringify(message));
  const header = Buffer.alloc(4); header.writeUInt32LE(body.length);
  const result = hostCommand([caller], Buffer.concat([header, body]));
  assert.ok(result.stdout.length >= 4);
  assert.equal(result.stdout.readUInt32LE(), result.stdout.length - 4);
  return JSON.parse(result.stdout.subarray(4));
}
async function pendingRequest() {
  for (let attempt = 0; attempt < 100; attempt++) {
    const names = await readdir(mailbox).catch(() => []);
    for (const name of names.filter(name => name.endsWith(".json"))) {
      const request = JSON.parse(await readFile(join(mailbox, name), "utf8"));
      if (request.status === "pending") return request;
    }
    await delay(100);
  }
  throw new Error("Chrome did not queue a native request");
}

try {
  // Registration is confined to the disposable test profile, never ~/Library's Chrome profile.
  assert.equal(hostCommand(["--register", "--directory", join(profile, "NativeMessagingHosts")]).status, 0);
  assert.equal(nativeMessage({operation: "approve"}).status, "error");
  assert.equal(nativeMessage({operation: "export_key"}).status, "error");
  assert.equal(nativeMessage({}, "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/").status, "error");
  const oversized = Buffer.alloc(4); oversized.writeUInt32LE(2049);
  assert.equal(hostCommand([`chrome-extension://${extensionID}/`], oversized).status, 1);

  const probe = createServer();
  await new Promise(resolve => probe.listen(0, "127.0.0.1", resolve));
  const port = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  server = spawn(process.env.ORF_TEST_PYTHON || "python3", ["-m", "uvicorn",
    "examples.native_site:create_native_site_app", "--factory", "--host", "127.0.0.1", "--port", String(port)],
    {cwd: root, env: {...env, PYTHONPATH: `${root}/src`, ORF_NATIVE_DEMO_ORIGIN: origin}, stdio: ["ignore", "ignore", "pipe"]});
  let serverLog = "";
  server.stderr.on("data", chunk => {serverLog += chunk;});
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    if (server.exitCode !== null) throw new Error(serverLog);
    try {if ((await fetch(origin)).ok) {ready = true; break;}} catch {}
    await delay(100);
  }
  assert.ok(ready, "Demo server must start");
  // Full Chrome for Testing 146+ supports extensions and native hosts in its user-data directory.
  browser = await puppeteer.launch({headless: true, enableExtensions: [extension], userDataDir: profile, env});
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(origin);
  await page.waitForFunction(() => !document.querySelector("#connect").disabled);
  await page.click("#connect");
  await browser.waitForTarget(target => target.url().startsWith(`chrome-extension://${extensionID}/`));
  const request = await pendingRequest();
  assert.equal(request.origin, origin);
  assert.equal(request.proof, undefined);
  await delay(1200);
  assert.equal(await page.$eval("#result", node => node.hidden), true, "No proof before native approval");
  assert.equal(nativeMessage({operation: "status", request_id: request.request_id, origin: "https://other.example"}).status, "error");
  assert.equal(hostCommand(["--test-approve", request.request_id]).status, 0);
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("identity verified"));
  const verified = JSON.parse(await page.$eval("#result", node => node.textContent));
  assert.equal(verified.origin, origin);
  assert.ok(verified.site_subject.startsWith("orf:site:"));
  assert.ok(!JSON.stringify(verified).includes("profile_id"));
  for (const width of [1280, 375, 320]) {
    await page.setViewport({width, height: 900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await page.waitForFunction(() => !document.querySelector("#connect").disabled);
  await page.click("#connect");
  const denied = await pendingRequest();
  assert.equal(hostCommand(["--test-deny", denied.request_id]).status, 0);
  await page.waitForFunction(() => document.querySelector("#status").textContent.includes("denied"));
  assert.equal(await page.$eval("#result", node => node.hidden), true);
  assert.deepEqual(errors, []);
  process.stdout.write("Chrome extension + Swift native messaging + backend verification passed (synthetic approval; Keychain/UI not tested).\n");
} finally {
  if (browser) await browser.close();
  if (server) {
    server.kill("SIGTERM");
    if (server.exitCode === null) await new Promise(resolve => server.once("exit", resolve));
  }
  await rm(temporary, {recursive: true, force: true});
}
