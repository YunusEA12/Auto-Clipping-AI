# Credential and state handling

- `.env`, `var/`, private streamer configuration and editable local profiles are ignored.
- Cookie/OAuth/client-secret files belong in `var/secrets/`; do not put them in assets,
  examples, issue descriptions or test fixtures.
- Runtime writes use atomic replace. An unreadable upload ledger blocks publishing.
- Application logs redact configured secret values, common API-key shapes and Bearer tokens,
  including exception details. Raw browser HTML/screenshots remain private diagnostic files.
- The repository check refuses credential-shaped paths and runtime data in tracked files.
  The wheel check verifies that these files are absent from the distribution as well.
- These checks do not replace secret scanning of arbitrary source content or key rotation
  after a real disclosure. Do not claim a clean history solely from filename checks.
- Production remains behind the existing Tailscale/firewall and password gate. This update
  does not expose a new public port or grant the dashboard additional sudo permissions.
