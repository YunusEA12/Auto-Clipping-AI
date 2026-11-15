# Linux-Server · Version 0.2

Voraussetzungen: Python 3.12, `uv`, FFmpeg/FFprobe, Linux-Abhängigkeiten für Playwright,
ein eigener unprivilegierter Dienstbenutzer `autoclip` und ein Checkout unter
`/opt/auto-clipping-ai`, das diesem Benutzer gehört. Die bestehenden Firewall-, Tailscale-
und Dashboard-Anmeldeeinstellungen beibehalten. Port 8501 ist kein öffentlicher Dienst.

## Bestehende Installation aktualisieren

Beim ersten Layout-Wechsel **vor dem Pull** die Anwendung stoppen:

```bash
sudo systemctl stop auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit
# Falls aktiv: auch auto-clipping-guardian stoppen.
cd /opt/auto-clipping-ai
sudo -u autoclip git pull --ff-only
sudo -u autoclip env UV_PROJECT_ENVIRONMENT=/opt/auto-clipping-ai/venv \
  .maintenance-tools/bin/uv sync --locked --group dev
sudo -u autoclip env PLAYWRIGHT_BROWSERS_PATH=/opt/auto-clipping-ai/.playwright-browsers \
  venv/bin/python -m playwright install chromium
sudo -u autoclip venv/bin/autoclip migrate-runtime --apply
```

`UV_BIN`/der gezeigte uv-Pfad muss für `autoclip` ausführbar sein. Ein Symlink nach
`/root/.local/bin/uv` reicht nicht. API-Schlüssel bleiben in `.env`; Credentials liegen
nach der Migration in `var/secrets/`, Streamer-Konfiguration in `config/streamers.json`.

Die geprüften neuen systemd-Units installieren und prüfen:

```bash
sudo install -m 644 deploy/auto-clipping-supervisor.service /etc/systemd/system/
sudo install -m 644 deploy/auto-clipping-dashboard.service /etc/systemd/system/
sudo install -m 644 deploy/auto-clipping-quality-audit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/auto-clipping-supervisor.service \
  /etc/systemd/system/auto-clipping-dashboard.service /etc/systemd/system/auto-clipping-quality-audit.service
sudo -u autoclip env PLAYWRIGHT_BROWSERS_PATH=/opt/auto-clipping-ai/.playwright-browsers venv/bin/autoclip check
sudo -u autoclip venv/bin/python -m pytest -q
sudo systemctl enable --now auto-clipping-supervisor auto-clipping-dashboard auto-clipping-quality-audit
```

Eine vorhandene Guardian-Unit ebenfalls auf den neuen Moduleinstieg aktualisieren, aber nur
starten, wenn sie zuvor bewusst aktiviert war. Zusätzliche Dienste anderer Anwendungen
werden nicht verändert.

## Neue Installation

Den gleichen Paket-/Browser-Installationsweg verwenden, `.env` aus der Vorlage einrichten,
`config/streamers.example.json` privat nach `config/streamers.json` kopieren und eine
gültige TikTok-Sitzung einrichten. Beim ersten CLI-Start entstehen die Runtime-Verzeichnisse
und Standardprofile. Die Beispielkonfiguration veröffentlicht nichts.

## Weitere Updates und Wiederherstellung

Nach dem ersten Strukturwechsel ist `sudo bash deploy/update.sh` der Wartungseinstieg.
Er verlangt einen sauberen Checkout, verhindert parallele Deployments, installiert die
gesperrten Abhängigkeiten, prüft und startet kontrolliert. Das Dashboard führt keinen
unvollständigen Git-Pull mehr aus.

[Details zu Backups, Fehlerfällen und Nachweisen](docs/operations.md) ·
[Architektur](docs/architecture.md) · [Sitzung/Uploads](README_UPLOAD.md)
