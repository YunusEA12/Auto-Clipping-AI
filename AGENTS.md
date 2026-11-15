# Work in this repository

Read and follow [claude.md](claude.md) before changing code. It retains authority
over publishing safeguards, CPU-only processing, atomic state and secret protection.

- Production code belongs in `src/autoclip/<domain>/`; use qualified package imports.
- Resolve mutable files through `autoclip.runtime.path`. Never put credentials or
  generated clips inside the package or assets directory.
- The dashboard calls `autoclip.dashboard.dashboard_api`; business logic stays outside UI code.
- `autoclip` is the installed CLI. Do not recreate flat backend modules or mutate `sys.path` in package code.
- Run Ruff, tests, the repository check, and the wheel smoke check for structural changes.
- Use isolated synthetic media for tests. Tests must not publish, call paid APIs,
  or modify a real runtime. Keep the global test guards intact.
- Update `docs/` and the linked Obsidian architecture decision when changing runtime contracts.
