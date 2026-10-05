const activeTabs = new Set();
const extensionAPI = globalThis.browser ?? globalThis.chrome;

function validateConnection(message, sender, tab) {
  const keys = message.operation === "begin" ? "expires_at,nonce,operation,request_id" : "operation,request_id";
  if (Object.keys(message).sort().join(",") !== keys ||
      !["begin", "status"].includes(message.operation) || sender.frameId !== 0 || !sender.tab ||
      typeof message.request_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(message.request_id)) {
    throw new Error("Invalid connection request.");
  }
  if (message.operation === "begin") {
    const now = Math.floor(Date.now() / 1000);
    if (typeof message.nonce !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(message.nonce) ||
        !Number.isSafeInteger(message.expires_at) || message.expires_at <= now || message.expires_at > now + 300) {
      throw new Error("Invalid challenge.");
    }
  }
  const source = new URL(sender.url);
  const top = new URL(tab.url);
  if (source.origin !== top.origin || source.username || source.password ||
      (source.protocol !== "https:" && !(source.protocol === "http:" &&
        ["localhost", "127.0.0.1", "[::1]"].includes(source.hostname)))) throw new Error("Invalid website origin.");
  return source.origin;
}

async function handleNativeRequest(message, sender) {
  let acquired = false;
  try {
    const tab = await extensionAPI.tabs.get(sender.tab?.id);
    const origin = validateConnection(message, sender, tab);
    if (activeTabs.has(tab.id) || activeTabs.size >= 16) throw new Error("Another ORF request is pending.");
    activeTabs.add(tab.id); acquired = true;
    const payload = {operation: message.operation, request_id: message.request_id, origin};
    if (message.operation === "begin") Object.assign(payload, {nonce: message.nonce, expires_at: message.expires_at});
    const result = await extensionAPI.runtime.sendNativeMessage("org.openrecommender.gateway", payload);
    const current = await extensionAPI.tabs.get(tab.id);
    if (new URL(current.url).origin !== origin) throw new Error("Website changed during approval.");
    if (!result || !["pending", "approved", "denied", "revoked", "expired", "error"].includes(result.status)) {
      return {status: "error", error: "Invalid ORF response."};
    }
    // Explicit allowlist: a native response cannot accidentally export mailbox fields or future private state.
    if (result.status === "approved") {
      const proof = result.proof;
      if (!proof || Object.keys(proof).sort().join(",") !== "payload,signature" ||
          !proof.payload || Object.keys(proof.payload).sort().join(",") !==
            "audience,expires_at,issued_at,nonce,public_key,subject,version" ||
          JSON.stringify(proof).length > 2048) throw new Error("Invalid public proof fields.");
      return {status: "approved", proof};
    }
    if (result.status === "error") return {status: "error", error: "ORF rejected the request. Open the app to review setup."};
    return {status: result.status};
  } catch {
    return {status: "error", error: "ORF could not connect. Open ORF Gateway and check this site's extension permission."};
  } finally { if (acquired) activeTabs.delete(sender.tab.id); }
}

// Callback + true keeps Chrome's response channel alive, including versions without Promise listeners.
extensionAPI.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!["begin", "status"].includes(message?.operation)) return false;
  handleNativeRequest(message, sender).then(sendResponse);
  return true;
});
