# Transparency & Security

Open Recommender currently uses a **trusted sync service**. The CLI can work entirely
offline, but registering or syncing a profile sends its full preference state and
signed event history to the service. Private visibility restricts partner projections;
it does not hide synced topics from the service operator.

## What the service stores

Registration and sync persist:

- A profile document containing identity metadata, topics of all visibility levels,
  opt-outs, consent settings, and sync state.
- Signed events, including historical private/selective topics and recommendation
  payloads. Removing a topic does not erase its historical events.
- Challenges and their use status; site access requests, approvals, denials, grants,
  and sessions; audit records linking those operations to profiles and sites.
- Optional ranking feedback, including submitted candidate IDs, topics, timestamps,
  and metadata. Feedback is scoped to a grant and does not become portable ORF state.

The service can therefore see private topics, requested sites, denied requests,
and any recommendation data included in synced events. It does not receive the user's
private signing key through the registration, sync, or challenge APIs.

SQLite stores this data as readable JSON. There is no end-to-end encryption.
The `hosted_sync` flag is stored preference metadata; it currently does not prevent
explicit CLI pushes, profile registration, or event uploads. Setting
`OPEN_RECOMMENDER_SYNC_TOKEN` gates event reads/writes with a shared service token;
it does not enable encryption. Raw history reads separately require a one-time
proof signed by that profile's owner, whether or not a shared token is configured.
The proof binds the profile, read purpose, and exact cursor. A shared token alone
cannot read another user's history. The former unauthenticated GET is retired.
See [owner-only sync](protocol.md#sync-boundaries).

## What partner projections expose

Public projections include profile identity, display name, timestamps, consent
summary, and public topics allowed by sharing settings. They omit the entire
`opt_out_topics` field: a private or never-shared topic must not become public
merely because its owner opted out. Signed opt-outs remain in the owner file,
backups, and the full history uploaded to the trusted service.

Earlier preview responses included opt-out names. This change cannot recall
previously downloaded or cached responses; operators and recipients must handle
those copies under their retention/deletion policies.

Consented projections include identity and approved public/selective topics.
Private topics and opted-out topics are excluded from their topic lists.
Scopes control these projection responses, not the full sync event stream.
Raw history is for the owner's sync client, not a site integration. Do not give
a partner an owner sync proof or a full profile as a shortcut around consent.

A site can retain information it already received. Revoking a grant blocks future
session exchanges and subsequent use of existing sessions through the service;
it cannot delete a site's copies.

## Signed registration and event integrity

Initial registration requires one signed `set_profile` event at clock zero.
Its payload binds `display_name`, `created_at`, and `schema_version`; its envelope
binds the profile ID, device ID, and timestamp. The creation timestamp must match
the signed event timestamp. The profile ID is derived from the public key.

Both `POST /profiles` and the local profile importer merge verified events into the
stored event history. Hosted state is rebuilt from that history, so unsigned snapshot
fields cannot replace it and stale profile uploads cannot roll it back.

Event retries are idempotent. Reusing an event ID with different signed content is
rejected. Registration and event appends commit their event rows and profile state
in one SQLite transaction; invalid batches leave no partial writes.
Concurrent writers acquire the database write lock before reading the stored history.

Signatures authenticate event content. They do not encrypt it, prove freshness,
or prevent someone from replaying a valid signed event. Replays of known events
do not reset state because the store retains the full history.
The CLI verifies downloaded signatures before saving a pull and leaves the local
file unchanged if verification fails. A signature does not prove a service has
returned complete or current history; a malicious operator can omit valid events.

New CLI profiles contain the signed registration event. To upgrade an older local
profile, run `sync-push` with its matching key; the CLI adds and saves the event
before uploading. It accepts `--key-path` and `--key-passphrase` for that step.
Older hosted profiles must receive this registration event before further event
appends. Use the upgraded file for browser imports and across devices.

## Active profiles and backups

- **`.orf`:** readable JSON. Signed events provide integrity when verified; the file
  itself is not encrypted and its unsigned snapshot fields are not authenticated.
- **`.orf.key`:** a separate PEM private key. CLI creation can encrypt it with
  `--passphrase`; otherwise it is unencrypted.
- **`.orfb`:** readable JSON containing the full profile and a passphrase-encrypted
  private key. **Only the key is encrypted.** Topic data and event history remain
  readable in the backup.

CLI profile, key, and backup saves stage bytes in a hidden `.orf-save-*.orf` file
in the destination directory, flush it, and publish the completed file without
truncating the previous version. On Unix, the staged and saved files are owner-only
(`0600`), including replacements of older, more permissive files. On Windows,
use a private directory with appropriate access-control rules; Unix mode bits are
not a Windows ACL guarantee. Filesystem permissions are not encryption.

New identity and backup creation refuse existing destinations, including a file
that appears during publication. Symbolic-link destinations are refused. Restore
requires distinct backup/profile/key paths and keeps the input backup intact;
existing output files still require `--overwrite`.

These are **per-file** guarantees on a local filesystem, not a transaction across
profile and key. The key is saved first; a later profile-save failure can leave a
key without a matching output profile. Inspect both paths before retrying, and
keep the original backup when restoring. Hard-link support is required for
no-overwrite publication. File flushing does not guarantee directory durability
after power loss. A forcibly killed process may leave an owner-only staged file;
do not share it, and remove it only after confirming the required recovery files.
Existing files are not automatically hardened until saved again.

The browser demo imports a key into tab memory to sign challenges. Site-controlled
JavaScript with access to that memory must be trusted. This demo is not an isolated
production wallet or signer.

## Deletion and retention

The owner can delete hosted data with the CLI:

```bash
python -m open_recommender.cli profile-delete profile.orf http://127.0.0.1:8000 --confirm
```

The command uses the matching key to sign a one-time deletion challenge, then calls
`POST /profiles/{profile_id}/delete`. The service removes the profile, signed events,
requests, grants, sessions, challenges, audit records, and ranking feedback in one
transaction, including the deletion challenge itself. It returns counts of removed
rows. Other profiles, the site registry, and service configuration remain intact.
Local profiles, keys, and backup files are not deleted.

Deletion affects the live database only. It does not purge operator backups or logs,
erase partner copies, or guarantee forensic erasure of SQLite pages/WAL files.
There is no automatic retention cleanup. Expiry controls access but does not remove rows.
Re-uploading the retained signed profile can register it again; deletion is not a ban
on future uploads.

To stop sending data, stop invoking registration and sync commands. Deleting a local
file or setting `hosted_sync=false` does not delete hosted copies.
Ask the operator about backup/log retention separately. Keep local files and keys
until recovery needs have been considered.

## Owner-authenticated consent

Approval, denial, revocation, hosted deletion, and raw sync reads require an `owner_proof` signed
by the profile's Ed25519 key. The service issues a five-minute challenge bound to the
action, target ID, and exact JSON parameters, including scopes, reason, actor label,
and grant expiry when supplied. A profile-login or grant-exchange challenge cannot
authorize these actions.

Proof consumption and the mutation share one transaction. Replays, changed parameters,
wrong keys, and expired challenges fail. A rejected action does not consume its proof.
Browser decisions also require their existing localhost check and review token.
The local trust pages and React demo load an unencrypted key in browser memory and
send only its signature. Encrypted keys are supported through the CLI.
The Python sample site optionally loads a local demo key instead; every sample
route requires localhost. Backend credentials stay server-side, personal pages
disable caching, and rendered profile/request text is HTML-escaped. Its memory-only
sessions and demo signer are not a production authentication system. Do not expose
it through a proxy that makes remote requests appear local.

## Logs and audit inspection

The service writes audit rows for request decisions, challenge issuance/verification,
grant sessions, projection reads, owner sync reads (cursor/count only), and ranking operations. An admin token enables
inspection through `/admin/audit-events`.

HTTP access logs and error logs depend on Uvicorn and deployment configuration.
The reference service does not manage log rotation or operator retention.
The Python partner SDK raises HTTP/network errors; it does not configure logging
or cache preferences.

## Current threat boundary

- The device and its user-side signing client are trusted. Malware with file access
  can read profiles and may steal keys.
- The sync operator is trusted with full uploaded state. A service-wide token is
  not a boundary between different profile owners; one-time owner proofs enforce
  that boundary on raw history reads, but do not hide history from the operator.
- Private-key compromise permits forged events. Key rotation is not implemented.
- Consent decisions require signed owner proofs. Local pages additionally use a
  loopback check and review tokens. Optional backend token hashes bind partner
  requests and sessions to a registered site; unset configuration does not
  authenticate sites. Tokens do not verify domain ownership or grant owner rights.
  A stolen backend token can use that site's known active sessions, but not another
  site's sessions. Rotate the token and revoke affected grants after compromise.
  Do not put credentials in browser bundles, URLs, or logs. See
  [backend setup](pilot-integration.md#backend-credentials). The local consent flow
  still belongs on a trusted local machine.
- Browser CORS allows localhost origins. The browser demo and local trust surface
  do not yet provide a production remote consent/signing flow.
- Profile IDs are stable across sites and can enable correlation.
- Rate limiting covers selected auth-sensitive and profile-write routes. Its
  thread-safe, expiring in-process state is capped at 10,000 client/bucket pairs;
  it is not shared across workers and does not prevent profile enumeration.
- Request/item limits and cumulative per-profile/per-grant history budgets reject
  excess work atomically; no history is clipped. They do not impose global disk,
  connection, profile-count, challenge, or audit-retention quotas. See
  [service budgets](protocol.md#service-budgets) and operator constraints in
  [Architecture](architecture.md#deployment-boundary).
- Use HTTPS outside localhost; the reference application does not terminate TLS.

Keep the current service on a trusted local machine. For this version, share only
profiles whose full history you are comfortable disclosing to that service, and
treat partner projections as a separate, narrower sharing boundary.

## Verify the behavior

Dependency advisory checks run in the repository's test workflow: `pip-audit`
checks the Python runtime dependency resolution and `npm audit` checks the demo's
locked dependency tree. They report known advisories, not all reachable exploits
or undiscovered issues. Use a fresh environment and keep dependencies updated;
see [contributor checks](../CONTRIBUTING.md#useful-commands).

Event import, sync, and local application reject malformed signed fields before
changing state. Weights/scores must be finite numbers in `[0, 1]`, consent values
must be booleans, and clocks must be safe integers. Ranking/feedback also reject
non-finite nested metadata. Separate service budgets bound individual requests and
retained histories, not overall deployment growth.

Inspect a local profile or backup in a text editor. Compare its event history using
an [owner-signed read](protocol.md#sync-boundaries), keeping in mind that
`after_clock=0` returns mutations after the clock-zero registration event.

Relevant implementation and regression coverage:

- [Profile model and registration event](../src/open_recommender/models.py)
- [Transactional event merge and storage](../src/open_recommender/store.py)
- [API and local trust surfaces](../src/open_recommender/service.py)
- [CLI and backup format](../src/open_recommender/cli.py)
- [Profile integrity regression tests](../tests/test_profile_integrity.py)
- [Owner proof and private-history boundary tests](../tests/test_owner_actions.py)
- [Malformed input and atomic rejection tests](../tests/test_input_validation.py)
- [Streamed bodies, history budgets, and concurrency checks](../tests/test_resource_limits.py)
