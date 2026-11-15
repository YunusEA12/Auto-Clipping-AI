# Architecture and runtime layout

The code follows the [Python Packaging Guide's src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/).
The build metadata and console entry point follow the [pyproject specification](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/).
There is one installed package, one dependency lock and one runtime path registry.

```text
src/autoclip/
  ai/               transcript selection, transcription, vision, critic, LLM budgets
  capture/          stream recording and source ingestion
  rendering/        FFmpeg, subtitle placement, music, pure display settings
  publishing/       platform adapters, content ledger, fleet-wide upload limits
  control/          supervisor, live-stream orchestrator, autopilot, upload worker
  configuration/    typed streamer and profile configuration
  quality/          deterministic QC, sampled content audit, aggregate QC statistics
  observability/    metrics, optimisation reports, health checks, project guardian
  dashboard/        Streamlit script and its stable dashboard_api boundary
  core/             atomic I/O, redacted logging, browser concurrency, notifications
  tools/            cookie/session diagnostics and runtime migration
  resources/        versioned profiles, hook examples and background music
assets/branding/    approved ClipMaster artwork and copy
config/             private streamer configuration and editable profiles
deploy/             systemd definitions and the maintenance update entry point
docs/               architecture, operations, development and quality evidence
tests/              isolated unit, UI, process and real-media integration tests
var/                ignored mutable data, credentials, outputs and diagnostic evidence
```

`app.py` is the sole hosting compatibility script; it contains no application logic.
Every backend service is invoked through `autoclip <command>` or its qualified module.
Package code must not manipulate `sys.path` or import legacy flat module names.

## Boundaries

The dashboard calls `dashboard_api`. Its monitoring paths do not import Whisper or
OpenCV; processing backends are imported only when the corresponding operation is used.
Pure rendering settings are shared without loading the renderer.

The supervisor owns three separately restartable processes: live-stream orchestration,
metrics collection and the upload worker. Fleet pause stops all three. A streamer going
offline stops its recorder, while the upload worker can still publish assessed backlog.

Every publishing path, including manual dashboard uploads, uses the same QC, content
ledger and fleet-wide pacing. Browser publishing still requires explicit `publish=True`.
The worker only selects enabled streamers with a critic score and successful technical QC.
An unreadable ledger or unavailable reservation defers publication; neither bypasses deduplication.

All state filenames are resolved by `autoclip.runtime.path`, anchored at `AUTOCLIP_HOME`
(default: launch directory). Configuration lives in `config/`; mutable data lives in
`var/`. Credentials live in `var/secrets/`; `.env` stays at the application home.
The installed wheel contains code and seed resources, never live runtime state.

## Migration

`autoclip migrate-runtime` previews legacy moves. `--apply` requires stopped services,
checks every destination before changing anything, journals the move, and rolls back
completed moves on failure. It preserves exact ledger, pacing and credential bytes.
Stored media/audit paths are translated; rewritten JSON is backed up. A collision aborts
before any move. Profile defaults are seeded without replacing custom files.

See [operations](operations.md) for the deploy sequence and recovery limitations.
