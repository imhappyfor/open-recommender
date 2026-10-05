# Getting started

Create a portable profile, choose a trusted local service, and share only
specific signals with a site. For the visual demo, follow the
[README](../README.md#see-the-browser-demo).

## 1. Install

Use Python 3.10+ from the repository root:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

On Windows, activate with `.venv\Scripts\activate`. Quoting `'.[dev]'`
prevents shell wildcard expansion. All commands below assume this environment
is active.

The current cryptography dependency follows upstream platform support: macOS
requires Apple Silicon; 32-bit Windows is unsupported. See the
[upstream compatibility changes](https://cryptography.io/en/latest/changelog/#v49-0-0).

## 2. Create and inspect a profile

```sh
python -m open_recommender.cli create profile.orf --display-name "Alice" --device-id laptop
python -m open_recommender.cli topic-set profile.orf orf:technology/python 0.9
python -m open_recommender.cli topic-set profile.orf orf:media/podcasts 0.8 --visibility selective
python -m open_recommender.cli topic-set profile.orf orf:health/sleep 0.4 --visibility private
python -m open_recommender.cli export-public profile.orf
```

Creation writes `profile.orf` (readable JSON) and `profile.orf.key` (the private
signing key). The profile includes a signed clock-zero registration event.
Creation refuses existing profile/key destinations; choose new paths rather than
replacing an identity. CLI saves use protected temporary files, not in-place truncation.
New files use schema 0.2.0 and JCS signatures; legacy 0.1.0 history is preserved.
Open the profile in a text editor to inspect its topics, consent, and signed history.

- **Public:** may appear in public and approved site projections.
- **Selective:** appears only for a site granted the exact topic scope.
- **Private:** never appears in a partner projection; it is still readable in
  the file and by a service receiving the full profile.

To create synthetic activity instead, add `--seed --seed-value 1234` to
`create`. The CLI prints the seed and event summary. Never confuse generated
preferences with a real user's data.

## 3. Change your preferences

```sh
python -m open_recommender.cli topic-remove profile.orf orf:health/sleep
python -m open_recommender.cli opt-out-set profile.orf orf:politics/news true
python -m open_recommender.cli consent-set profile.orf share_public_topics false
```

Each change is signed locally. Removing a topic does not erase its old events.
Public projections exclude private/selective topics and omit opted-out names.
`hosted_sync=false` is stored intent, not a block on an explicit upload.

## 4. Run and sync with a trusted service

```sh
python -m uvicorn open_recommender.service:create_app --factory --host 127.0.0.1
```

Leave it running. In another activated terminal:

```sh
python -m open_recommender.cli sync-push profile.orf http://127.0.0.1:8000
python -m open_recommender.cli sync-pull profile.orf http://127.0.0.1:8000
```

These commands send/read full history, including private topics. The service
verifies events and merges them; an older upload cannot erase newer events.
Pulling requires the matching key: the CLI signs a one-time owner read challenge
and verifies downloaded signatures before saving. Use `--key-path` and
`--key-passphrase` for a non-default or encrypted key.
Each pull fetches all post-registration history, so late changes from another
device are not skipped. It merges without discarding unsent local events;
download size grows with history. A signature cannot detect service omissions.
For a legacy file, the first push adds a signed registration event with its
matching key. Use `--key-path` or `--key-passphrase` when needed, then reopen
the upgraded file before browser import.

A configured `OPEN_RECOMMENDER_SYNC_TOKEN` gates event routes with one shared
Bearer token **in addition to owner proof for reads**. CLI push/pull accept
`--sync-token` or the same environment variable. Prefer the environment to avoid
putting the token directly in command history. The shared token alone cannot
read a user's history. See [security boundaries](transparency-and-security.md).

A 413 response means a [service budget](protocol.md#service-budgets) was exceeded;
your local history stays intact. Ask the operator about capacity rather than
deleting signed events to make the file fit. For 429, wait the response's
`Retry-After` interval before retrying.

## 5. Review sharing

Open [Local Profile Lens](http://127.0.0.1:8000/lens) to inspect a local file
and simulate scopes. Opening it there does not upload it until you choose
**Register or update in local service**.

Open [Consent inbox](http://127.0.0.1:8000/consent) for pending site requests
or [Site grants](http://127.0.0.1:8000/consent/grants) to inspect existing grants.
These surfaces are localhost-only. Choose the matching unencrypted signing key
to approve, deny, or revoke; only the signature is sent. Use the CLI for encrypted keys.

If a site has given you a request ID:

```sh
python -m open_recommender.cli site-access-request-get <request_id> http://127.0.0.1:8000
python -m open_recommender.cli site-access-request-approve <request_id> http://127.0.0.1:8000 \
  --profile-path profile.orf --scope profile.read --scope topics.public
```

Or deny it:

```sh
python -m open_recommender.cli site-access-request-deny <request_id> http://127.0.0.1:8000 \
  --profile-path profile.orf --reason "Not this time."
```

Approval includes every required scope and only the optional scopes you select.
The CLI signs a one-time challenge bound to the exact decision. The site then
uses a separate user-signed exchange to obtain a short-lived session.
See the [integration guide](integration-guide.md).

## 6. Revoke access or delete hosted records

```sh
python -m open_recommender.cli site-grant-revoke <grant_id> http://127.0.0.1:8000 \
  --profile-path profile.orf
python -m open_recommender.cli profile-delete profile.orf http://127.0.0.1:8000 --confirm
```

Revocation blocks new exchanges and further projection, ranking, or feedback
calls through existing sessions. Deletion atomically removes live profile-linked
records, including grants, sessions, feedback, and audit events.

Neither operation erases partner copies, operator backups/logs, or local files.
Your retained signed profile can be registered again.

## 7. Back up and recover

```sh
python -m open_recommender.cli backup-create profile.orf profile-backup.orfb \
  --backup-passphrase "choose-a-strong-passphrase"
python -m open_recommender.cli backup-restore profile-backup.orfb restored-profile.orf \
  --backup-passphrase "choose-a-strong-passphrase"
```

Only the recovery key is encrypted; profile JSON and history remain readable.
Backup creation also refuses an existing destination. Restore checks the key
matches and refuses to overwrite files without
`--overwrite`. Protect the backup accordingly. Passphrases supplied on a command
line may appear in shell history or process listings.

For additional commands, run `python -m open_recommender.cli --help`.
For contributor tests, see [Contributing](../CONTRIBUTING.md).
