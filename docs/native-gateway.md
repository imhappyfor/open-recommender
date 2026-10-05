# Native ORF gateway: Chrome-first Mac login preview

The native gateway is a new, **login-only developer preview**, separate from the
existing hosted ORF service. A SwiftUI app approves requests; a Chrome Manifest
V3 extension supplies the requesting website's origin and carries the result back
through a Swift native-messaging host. The Safari target remains available, using
the same bridge source and native validation rules.
No readable profile needs to be hosted for this flow.

## Implemented boundary

- A fresh Ed25519 key is created per canonical website origin, only after native
  approval. It remains in the containing app's Keychain, not an extension-shared
  keychain group. Subsequent approvals for that origin reuse the key.
- The website receives its own public key, site subject, and challenge-bound
  signature. There is no master profile ID, master public key, or cross-site
  mapping in this contract.
- The extension has only `begin` and `status` native operations. There is no
  approve, key-export, arbitrary-sign, file-read, or raw-profile operation.
- Approval and denial happen in the native app. The app must be open for review;
  this preview does not automatically launch it.
- The native code does not make network calls. The approved proof travels through
  the extension and the site's own verification endpoint. There is no local HTTP
  listener or central profile service here. The Chrome developer build is **not
  App Sandbox-enforced**; absence of network code is not an OS-enforced network ban.
- The local mailbox contains requests and approved public proofs, **never
  private keys or profile data**. Files use mode 600, the directory mode 700,
  atomic replacement, and a process-shared lock. Completed nonces remain blocked
  until cleanup, five minutes after request expiry.

This does **not** yet import `.orf` files, share preferences, rank candidates,
back up identities, sync devices, or migrate hosted identities. Native identities
are new identities on this Mac, not portable ORF profiles. Do not remove the app
or forget a site's key without understanding that access to its account may be
lost. The old hosted contract and its plaintext-server boundary remain unchanged.

## Build and run: Chrome

Requirements: macOS 13+, Swift 6+ (Xcode or Command Line Tools), Chrome 120+, and
the repo's Python development dependencies for the demo. No Apple signing team,
Safari packaging, or local web server is needed for the browser/native bridge.
The demo website itself runs a loopback server.

From the repository root:

```sh
bash native/macos/build-chrome.sh
```

This builds an ad-hoc-signed developer app at `native/macos/.build/ORF Gateway.app`
and stages the unpacked extension at `native/macos/.build/chrome-extension`.
The script reuses the existing Swift approval app and shared browser scripts.

1. Open `chrome://extensions`, turn on **Developer mode**, choose **Load unpacked**,
   and select `native/macos/.build/chrome-extension`. The extension ID must be
   `lialdifcbcjmjahaicjmkmklmfjoelom`. The manifest's public packaging key makes
   this ID stable; it is not a user's identity key or an attestation of the build.
2. Register the native host explicitly:

   ```sh
   "native/macos/.build/ORF Gateway.app/Contents/MacOS/orf-chrome-host" --register
   ```

   This writes `org.openrecommender.gateway.json` in your Chrome user-level
   `~/Library/Application Support/Google/Chrome/NativeMessagingHosts` directory.
   It authorizes only this extension ID and records the absolute binary path.
   A conflicting existing registration is refused, not overwritten. If you move
   the repo/app, inspect and remove the old registration yourself, then register
   again. Building alone does not install or register anything in your profile.
3. Open the app and keep it running:

   ```sh
   open "native/macos/.build/ORF Gateway.app"
   ```

4. With the Python development environment active, start the demo:

   ```sh
   PYTHONPATH=src python -m uvicorn examples.native_site:create_native_site_app --factory --host 127.0.0.1 --port 8766
   ```

5. Visit `http://127.0.0.1:8766` in Chrome. Click **Connect ORF**, review the exact
   origin in the Mac app, choose **Approve login**, and return to the page.
   Denial should expose no identity proof.

The extension requests only `nativeMessaging` plus site access for HTTPS and
loopback demo pages. It does not use cookies, history, storage, or file-access APIs.
Use Chrome's site-access controls to grant only the sites you want to connect.
The native host accepts only framed, bounded `begin` and `status` messages and
checks Chrome's extension-origin argument. Website origins are derived from
browser metadata, never from a website-supplied origin field.

The Chrome app and host share a private-per-user mailbox at
`~/Library/Application Support/org.openrecommender.gateway.chrome/GatewayRequests`.
Private keys stay in the app's Keychain. This development build needs a trusted
local OS/user account; same-user malware is not isolated by this directory.
Keychain approval and persistence across rebuilt ad-hoc apps must be manually
verified. Do not use preview identities for valuable accounts: backups, a signed
installer, notarization, sandbox/distribution review, and store release are still
pending. To uninstall the bridge, remove the exact registration file above and
disable/remove the unpacked extension; this does not erase Keychain identities.

## Alternative build: Safari

Requirements: macOS 13+, a working Xcode with Swift 6+, Safari, and a signing team
able to sign both native targets and authorize their shared App Group.

1. Open `native/macos/ORFGateway.xcodeproj` in Xcode.
2. Select **ORF Gateway**. Set the same development team for the app and extension.
   Choose bundle identifiers your team can sign if the defaults are unavailable.
3. Both entitlements and Info plists use
   `$(DEVELOPMENT_TEAM).org.openrecommender.gateway` as their App Group. Configure
   that group for both targets; if you change it, update both Info plists and both
   entitlement files together. There is no unprotected fallback directory.
4. Build/run the **Debug** configuration. Keep the app open.
5. In Safari Settings → Extensions, enable ORF Gateway and allow it on the demo
   website. Its broad host patterns allow future adopters; grant only the sites
   you want to connect. This version does not use browsing-history or cookies APIs.
6. From the repository root, with the Python development environment active:

   ```sh
   python -m uvicorn examples.native_site:create_native_site_app --factory --host 127.0.0.1 --port 8766
   ```

7. Visit `http://127.0.0.1:8766` in Safari. Click **Connect ORF**, approve the
   displayed origin in the Mac app, and return to the page. It verifies and
   consumes its own challenge and displays its site-specific subject.

## Shared origin and demo rules

HTTP is accepted only for exact loopback hosts in Debug. Release native requests
require HTTPS. Origins are canonical scheme/host/port values, not registrable
domains: separate subdomains and non-default ports receive different keys.
No organization-wide identity sharing is implicit. Non-ASCII hostnames must be
browser-normalized ASCII/Punycode; this first contract rejects non-loopback IPv6
host literals. The demo itself is always loopback-only and is not a deployment
template behind a proxy that hides remote client addresses.

The demo origin defaults to `http://127.0.0.1:8766`. To use another loopback port,
set `ORF_NATIVE_DEMO_ORIGIN` to its exact origin and start the server on that port.
The sample rejects mismatched Host/Origin headers, uses a browser-session cookie
and CSRF header, limits proof bytes, and consumes challenges under a lock. It
returns a verified identity, **not a production account or session system**.

## Website integration

Prefetch a backend-issued challenge before the click, then call the SDK directly
inside the click handler so user activation is still present:

```js
import { connectNativeORF } from "@open-recommender/orf-web-sdk";

connectButton.addEventListener("click", async () => {
  const proof = await connectNativeORF({
    nonce: currentChallenge.nonce,
    expiresAt: currentChallenge.expires_at,
  });
  // Send to your own backend with the browser session's CSRF protection.
  await verifyOnYourBackend(proof);
});
```

The page's response is **untrusted until backend verification**. A site controls
its own JavaScript and can forge page messages. Never create an authenticated
session just because a frontend promise resolved or a native status says approved.

On a Python backend:

```python
from open_recommender.native_gateway import verify_native_login

subject = verify_native_login(
    proof,
    origin=YOUR_CONFIGURED_ORIGIN,
    nonce=outstanding_browser_session_challenge["nonce"],
    expires_at=outstanding_browser_session_challenge["expires_at"],
)
# Atomically consume that challenge and create your site's session for subject.
```

Keep challenges short-lived, unpredictable, single-use, and bound to the initiating
browser session. The verifier is deliberately stateless; calling it twice does
not reject replay by itself. The **site** must atomically consume its nonce.
Do not accept the expected origin, nonce, or expiry from the submitted proof.
Do not silently attach a new subject to an existing account without that site's
normal account-linking authorization.

## Wire contract: `orf-native-connect-v1`

The frontend posts a same-window, same-origin message containing exactly:
`channel="orf-native-v1"`, `direction="request"`, `request_id` (UUID v4),
`nonce` (32 random bytes, unpadded canonical base64url), and `expires_at` (Unix
seconds, no more than five minutes ahead). Top-level pages and a user gesture are
required. There is no requested origin, callback URL, scope, or data payload.

The content script sends `begin` and polls `status` using short background
messages. The background derives the origin from browser-provided sender/tab
metadata, rejects subframes and origin mismatch, and checks navigation again
before returning the native response. The native handler validates the origin,
fields, nonce, expiry, and mailbox bounds independently. No arbitrary outbound
URL or callback can be supplied through this contract.

Proofs contain exactly `payload` and `signature`. The payload has exactly:

| Field | Meaning |
| --- | --- |
| `version` | `orf-native-connect-v1` |
| `audience` | Canonical requesting website origin |
| `nonce` | Backend-issued challenge nonce |
| `subject` | `orf:site:` + first 32 hex characters of SHA-256 of the site public key |
| `public_key` | Raw 32-byte Ed25519 site public key, unpadded base64url |
| `issued_at` | Native signing time, integer Unix seconds |
| `expires_at` | Exact original challenge expiry, integer Unix seconds |

The signed bytes are `ORF native connect v1\n` followed by compact JSON with
lexically sorted field names, ASCII strings, and integer times. This narrowly
defined encoding is not a general event/JCS encoder. Unknown fields, versions,
noncanonical base64url, booleans/floats as times, bad subjects, wrong audiences,
wrong nonces, expiry mismatch, and invalid signatures fail verification. Clock
tolerance is 30 seconds into the future; validity never extends beyond expiry.
The prefix separates these proofs from hosted challenges and signed events.
The shared synthetic fixture is `tests/fixtures/native-login-proof.json`.

Terminal statuses are `approved`, `denied`, `revoked`, `expired`, and `error`.
Only approved status may include a proof. `pending` means open the app to review.
Native exceptions are replaced with a fixed public error, not file paths or
Keychain diagnostics. The extension strips request/mailbox fields from responses.

## Limits and honest guarantees

- Every login requires approval; there are no persistent sharing grants yet.
- One pending request per origin, 64 retained mailbox requests, and 16 simultaneous
  native calls bound work. Busy/error states fail closed rather than sharing data.
- **Forget identity** cancels retained proofs and deletes that site's local key.
  It cannot recall a delivered proof, invalidate a site's established session, or
  make the site erase stored data. Reconnecting creates a different identity.
- Distinct IDs prevent direct matching by master identifier, not matching by IP,
  browser fingerprint, email, or data the user independently shares.
- The trusted computing base includes the app, extension, browser, Keychain/OS,
  distributed builds, and updates. Open source makes inspection possible; it is
  not a guarantee against a compromised device or malicious build.
- Preference sharing and ranking need a separately reviewed, bounded contract;
  this login operation must not grow an arbitrary-sign or raw-profile escape hatch.

## Verification and remaining release gate

Local verification on 2026-10-04: 146 Python tests, 21 SDK tests, six Swift tests,
both browser flows below, Debug and Release Swift builds, and the standalone
Safari-handler type check pass. The Release host rejects the Debug test-approval
command. These results do not cover native UI clicks or real Keychain identities.

From the repo root:

```sh
python -m unittest discover -s tests -p test_native_gateway.py -v
cd sdk/orf-web-sdk && npm test
cd ../../native/macos && swift test
```

The native tests cover approval, audience isolation, expiry, denial, replay,
revocation, permissions, queue bounds, and Swift/Python/Node signing compatibility.
The SDK tests run the actual bridge source against browser/native test doubles,
including Chrome's API adapter and stable extension ID. After building the Chrome
artifacts, installing the React demo's Node dependencies (`npm ci` in
`sdk/react-sample-app`), and installing the Python development dependencies, run:

```sh
ORF_TEST_PYTHON=/absolute/path/to/venv/bin/python node native/macos/Tests/chrome-smoke.mjs
```

This uses full **Chrome for Testing 146+**, a disposable browser profile, the real
loaded extension and real Swift stdio host. It checks pending → approved → backend
verification, denial, wrong-origin status requests, forbidden operations, caller
allowlisting, frame limits, and mobile-width layout. Approval uses a **synthetic
test key**, not the SwiftUI buttons or your Keychain. No host is installed in your
everyday browser profile. Debug-only CLI test approval requires a temporary test
mailbox; it is not an extension operation and is absent from Release builds.

The additional browser test with a mocked native transport is:

```sh
ORF_TEST_PYTHON=/absolute/path/to/venv/bin/python node native/macos/Tests/browser-smoke.mjs
```

It exercises the real demo page, SDK, content script, background code, and backend
in Chromium using **a synthetic native transport**. It does not prove Safari's
extension isolation, signing, app-group access, or Keychain operation.

A manual Chrome session through the native approval UI and Keychain is still
required before calling this an installed-client release. The app is not
automatically launched, and Chrome Web Store/Developer ID distribution is not
implemented. Safari signing, installation, App Group access, and enabled
execution are also unverified. An earlier Safari packaging attempt on 2026-10-02
failed on an installed Xcode framework mismatch; the Chrome SwiftPM build avoids
that packaging path. No system repair or personal-profile installation is part
of automated verification. Remote CI results are not yet verified.

Platform references: [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging),
[Chrome message response lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/messaging),
[Safari native messaging](https://developer.apple.com/documentation/safariservices/messaging-between-the-app-and-javascript-in-a-safari-web-extension),
[Safari background lifecycle](https://developer.apple.com/documentation/safariservices/optimizing-your-web-extension-for-safari),
and [App Groups](https://developer.apple.com/documentation/xcode/configuring-app-groups).
