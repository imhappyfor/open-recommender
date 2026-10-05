// Runs in the extension's isolated world, never imports profile files or keys.
(() => {
  const extensionAPI = globalThis.browser ?? globalThis.chrome;
  let pending = false;
  window.addEventListener("message", async (event) => {
    const request = event.data;
    if (event.source !== window || event.origin !== location.origin || window.top !== window ||
        request?.channel !== "orf-native-v1" || request.direction !== "request") return;
    if (typeof request.request_id !== "string" || request.request_id.length > 36) return;
    const respond = (result) => window.postMessage({
      channel: "orf-native-v1", direction: "response", request_id: request.request_id, ...result,
    }, location.origin);
    if (Object.keys(request).sort().join(",") !== "channel,direction,expires_at,nonce,request_id" ||
        !navigator.userActivation.isActive || pending) {
      respond({status: "error", error: "Click Connect to request one ORF login at a time."});
      return;
    }
    pending = true;
    try {
      let result = await extensionAPI.runtime.sendMessage({operation: "begin", request_id: request.request_id,
        nonce: request.nonce, expires_at: request.expires_at});
      while (result?.status === "pending" && Date.now() < request.expires_at * 1000) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        result = await extensionAPI.runtime.sendMessage({operation: "status", request_id: request.request_id});
      }
      respond(result?.status === "pending" ? {status: "expired"} : result);
    } catch {
      respond({status: "error", error: "ORF Gateway is unavailable. Open the app and enable its browser extension."});
    } finally { pending = false; }
  });
})();
