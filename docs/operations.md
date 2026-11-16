# Operations

The Linux VPS is production; the Windows fleet remains paused. Only one production fleet
should use an account's credentials and quota state.

## Runtime data

| Path | Purpose |
|---|---|
| `config/streamers.json` | Private per-streamer flags, stream URLs and camera configuration |
| `config/profiles/` | Editable profiles, seeded from packaged defaults |
| `var/state/` | Agent, fleet, quota, budget, learning and upload-ledger files |
| `var/secrets/` | Cookie, OAuth and client-secret files; POSIX directory mode 700 |
| `var/temp/` | Recordings, audio, transcripts, ASS and the global render lock |
| `var/output/` | Rendered candidates retained until quality and publishing decisions |
| `var/uploaded_clips/` | Confirmed posts and metadata; archive collisions never overwrite media |
| `var/quality_audit/`, `var/selector_audit/` | Private review and browser evidence |
| `var/profile_assets/` | Prepared branding, verification screenshots and result receipt |
| `var/backups/` | Migration manifests, prior state and deployment recovery references |

Back up `config/`, `var/state/`, `var/secrets/` and `.env` privately. Keep the ledger and
quota state on account changes unless the documented account-switch procedure explicitly
archives a field. Never reset state to make an unresolved upload look like a fresh clip.

## First deployment of version 0.2

Stop supervisor, dashboard, audit and any active guardian before pulling the layout change.
Install the package into the service's **existing** `venv` using:

```bash
sudo -u autoclip env UV_PROJECT_ENVIRONMENT=/opt/auto-clipping-ai/venv \
  /opt/auto-clipping-ai/.maintenance-tools/bin/uv sync --locked --group dev
sudo -u autoclip /opt/auto-clipping-ai/venv/bin/autoclip migrate-runtime --apply
```

Install the reviewed systemd definitions with their qualified module entry points, preserve
the existing browser installation path, firewall and authentication, then reload systemd.
Run health checks, the test suite and the read-only session check before restarting services.
The deployment user must own writable runtime paths; do not run the pipeline as root.

## Subsequent updates

```bash
sudo bash deploy/update.sh
```

The script refuses dirty checkouts and concurrent deployments, records the previous commit
and unit files, stops services, fast-forwards code, installs `uv.lock`, installs the matching
browser, migrates state, runs health/lint/tests and then starts services. Code is executed
as `autoclip`. Run the maintenance script from the server; the dashboard no longer offers
a partial `git pull` that omits dependency installation and migration.

It deliberately leaves services stopped on a failed deployment and prints the failed phase
and recovery directory. Automatic rollback across state-schema changes is not claimed.
For recovery, preserve the current `var/` first, inspect the migration manifest and restore
the prior code, matching unit definitions and data layout together. Do not run old code
against a new layout or reset the ledger. Review a first layout migration separately.

## Checks and publication evidence

```bash
venv/bin/autoclip check
venv/bin/autoclip verify-session --headless --skip-content-page
venv/bin/autoclip qc-summary --limit 50
venv/bin/autoclip uploads --once                 # no publish flag: no posting
systemctl is-active auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit
```

A cookie's presence is not proof of a valid session. A transfer reaching 100% is not a
published post. Confirmed posts require the publication signal, ledger record and Studio
entry. Unconfirmed post clicks retain duplicate protection. Defaults remain four upload
attempts per local day and at least three hours between attempts.

## Remaining operational work

Whisper uses one fleet-wide inference slot by default, held from model construction through
lazy segment consumption. `WHISPER_MAX_CONCURRENCY=2` permits two on hosts with at least
four logical CPUs; larger values are capped at two. `WHISPER_CPU_THREADS` still controls
threads per model. Slot waits time out after 30 minutes and OS locks release after a crash.
Transcript cache files are validated and atomically written under a per-file lock.

Gemini application-level retries each reserve their own daily call under a shared lock.
Unreadable budgets block calls instead of resetting spending history. HTTP transport errors
use bounded backoff; authentication and malformed requests still fail immediately. This is
a request-count limit, not a token-price budget or a count of SDK-internal retries.

Newly rendered clips failing only loudness receive a measured two-pass audio repair. Video
packets are copied, and a verified temporary output replaces only the generated render.
The full QC runs again before the clip can be published. No historical backlog is silently
rewritten or marked as passed.

Long-run measurements are required for memory behaviour, publishing success rate and
recovery time. The existing QC backlog must be reviewed against real video/audio before
thresholds are changed. Browser selectors and human captcha challenges remain external
dependencies. No watchtime, retention or audience-growth evidence is invented.
