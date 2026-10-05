"""Loopback-only native-login demonstration; no ORF service or profile upload.

python -m uvicorn examples.native_site:create_native_site_app --factory --host 127.0.0.1 --port 8766
"""
from __future__ import annotations

import hashlib
import html
import os
from pathlib import Path
import secrets
import threading
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from open_recommender.native_gateway import verify_native_login
from open_recommender.service import require_local_browser


def create_native_site_app() -> FastAPI:
    origin = os.environ.get("ORF_NATIVE_DEMO_ORIGIN", "http://127.0.0.1:8766")
    from urllib.parse import urlsplit
    parts = urlsplit(origin)
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parts.username or parts.password or parts.path or parts.query or parts.fragment
            or not parts.port or origin != f"http://{parts.netloc}"):
        raise ValueError("ORF_NATIVE_DEMO_ORIGIN must be a loopback HTTP origin with a port.")
    app = FastAPI(dependencies=[Depends(require_local_browser)])
    # ponytail: process-local demo sessions (64 max, 10-minute retention); use transactional site auth storage for deployment.
    sessions: dict[str, dict] = {}
    lock = threading.Lock()
    cookie_name = "orf_native_demo_" + hashlib.sha256(origin.encode()).hexdigest()[:12]

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.headers.get("host") != parts.netloc:
            return Response("Wrong demo host", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def session(request: Request) -> dict:
        entry = sessions.get(request.cookies.get(cookie_name, ""))
        if not entry or entry["expires_at"] <= int(time.time()):
            raise HTTPException(401, "Reload the demo page.")
        if request.method == "POST" and (request.headers.get("origin") != origin or
                not secrets.compare_digest(request.headers.get("x-orf-demo-csrf", ""), entry["csrf"])):
            raise HTTPException(403, "Invalid browser session.")
        return entry

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        with lock:
            now = int(time.time())
            for key in list(sessions):
                if sessions[key]["expires_at"] <= now: del sessions[key]
            if len(sessions) >= 64: raise HTTPException(429, "Demo session limit reached. Try later.")
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            sessions[token] = {"csrf": csrf, "expires_at": now + 600, "challenge": None}
        response = HTMLResponse(f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="orf-csrf" content="{html.escape(csrf, quote=True)}">
<title>ORF native login preview</title><link rel="stylesheet" href="/style.css"></head><body>
<main><p class="eyebrow">OPEN RECOMMENDER · NATIVE PREVIEW</p><h1>Your identity stays with you.</h1>
<p>Connect through the ORF browser extension, then approve in the ORF Gateway Mac app.</p>
<p class="boundary">This site receives only its own public identity and a signed login proof.
No profile, preferences, history, or private key is uploaded.</p>
<button id="connect" type="button">Connect ORF</button><p id="status" role="status" aria-live="polite">Not connected.</p>
<pre id="result" hidden></pre><p class="fine">Local developer preview. The ORF extension and ORF Gateway Mac app are required.
The native client currently supports login only, not profile import or sharing.</p></main>
<script type="module" src="/demo.js"></script></body></html>''')
        response.set_cookie(cookie_name, token, httponly=True, samesite="strict", max_age=600)
        return response

    @app.get("/sdk.js")
    def sdk():
        source = Path(__file__).resolve().parents[1] / "sdk/orf-web-sdk/src/index.js"
        return Response(source.read_text(), media_type="text/javascript")

    @app.get("/demo.js")
    def demo_script():
        return Response('''import {connectNativeORF} from "/sdk.js";
const button = document.querySelector("#connect");
const status = document.querySelector("#status");
const result = document.querySelector("#result");
let challenge;
const headers = {"Content-Type":"application/json", "X-ORF-Demo-CSRF":document.querySelector('meta[name="orf-csrf"]').content};
async function prepare() {
  button.disabled = true;
  const response = await fetch("/challenge", {method:"POST", headers, body:"{}"});
  if (!response.ok) throw new Error("Could not create a browser-bound challenge. Reload the page.");
  challenge = await response.json(); button.disabled = false;
}
button.addEventListener("click", async () => {
  button.disabled = true; result.hidden = true;
  status.textContent = "Open ORF Gateway and approve this website's request.";
  try {
    // Challenge is prefetched so the bridge request retains this click's user activation.
    const proof = await connectNativeORF({nonce:challenge.nonce, expiresAt:challenge.expires_at});
    const response = await fetch("/verify", {method:"POST", headers, body:JSON.stringify({proof})});
    const verified = await response.json();
    if (!response.ok) throw new Error(verified.detail || "Verification failed.");
    status.textContent = "Site-specific identity verified. No preferences were shared.";
    result.textContent = JSON.stringify(verified, null, 2); result.hidden = false;
  } catch (error) { status.textContent = error.message; }
  finally { try { await prepare(); } catch (error) { status.textContent = error.message; } }
});
prepare().catch(error => {status.textContent = error.message;});
''', media_type="text/javascript")

    @app.get("/style.css")
    def styles():
        return Response('''*{box-sizing:border-box}body{margin:0;background:#102323;color:#effaf5;font:18px/1.6 system-ui}
main{max-width:760px;margin:8vh auto;padding:28px}h1{font-size:clamp(2rem,7vw,3.7rem);line-height:1.1}
.eyebrow{color:#9bdec5;font-size:.75rem;letter-spacing:.12em}.boundary{padding:20px;border:1px solid #477367;border-radius:16px}
button{min-height:48px;padding:12px 24px;background:#a1ebc5;border:0;border-radius:12px;font:inherit;font-weight:700;cursor:pointer}
button:disabled{opacity:.6}button:focus-visible{outline:3px solid white;outline-offset:4px}.fine{font-size:.85rem;color:#afc9c3}
pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:16px;background:#193b34;border-radius:12px;font-size:.8rem}
''', media_type="text/css")

    @app.post("/challenge")
    def challenge(request: Request):
        with lock:
            entry = session(request)
            entry["challenge"] = {"nonce": secrets.token_urlsafe(32), "expires_at": int(time.time()) + 120}
            return entry["challenge"]

    @app.post("/verify")
    async def verify(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 4096: raise HTTPException(413, "Proof too large.")
            raw.extend(chunk)
        from open_recommender.crypto import load_json
        try:
            body = load_json(raw)
            if not isinstance(body, dict) or set(body) != {"proof"}: raise ValueError("Invalid proof request.")
        except (ValueError, TypeError): raise HTTPException(400, "Invalid proof request.") from None
        with lock:
            entry = session(request)
            pending = entry["challenge"]
            if not pending: raise HTTPException(400, "No outstanding challenge.")
            try:
                subject = verify_native_login(body["proof"], origin=origin, nonce=pending["nonce"],
                    expires_at=pending["expires_at"])
            except ValueError: raise HTTPException(400, "Proof does not match the current challenge.") from None
            entry["challenge"] = None
            return {"verified": True, "site_subject": subject, "origin": origin}

    return app
