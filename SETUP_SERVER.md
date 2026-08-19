# 24/7-Betrieb auf einem eigenen Server (statt dem eigenen PC)

Diese Anleitung verlagert `process_supervisor.py` (also `orchestrator.py` +
`metrics_tracker.py` + alle `auto_pilot.py`-Subprozesse) und das Streamlit-Dashboard
(`app.py`) von deinem Windows-PC auf einen dauerhaft laufenden Linux-VPS. Ziel: der PC kann
aus bleiben, der Server läuft weiter.

## Was auf dem Server anders ist als auf deinem PC

- **Kein Intel Quick Sync (QSV).** `process.render_clip()` versucht `h264_qsv` zuerst und
  fällt automatisch auf `libx264` (CPU) zurück, wenn das fehlschlägt — das ist bereits
  eingebaut, nichts zu ändern. Rendering wird auf dem Server also langsamer sein als auf
  deinem PC. Bei 4 vCPUs ist das für ein paar Clips pro Zyklus in der Praxis unproblematisch,
  nur eben nicht mehr in Sekunden, sondern eher in ein bis zwei Minuten pro Clip.
- **Keine TikTok-Cookies aus einem lokal eingeloggten Browser.** `get_cookies.py` liest
  Cookies aus Chrome/Edge/Firefox über deren Verschlüsselung (DPAPI unter Windows) — auf
  einem headless Linux-Server gibt es diesen Browser-Login gar nicht. Siehe unten.
- **Kein automatischer Zugriffsschutz fürs Dashboard.** `app.py` hat keine eigene
  Authentifizierung. Der Server exponiert das Dashboard deshalb NICHT öffentlich, sondern
  nur über Tailscale (dein eigenes privates Netzwerk).

## Server-Empfehlung

Reines CPU-Encoding, keine GPU nötig. Für 1–2 gleichzeitige Streamer reicht ein Server mit
**4 vCPU / 8 GB RAM / NVMe** — z. B. Hetzner CPX31 (Deutschland/Finnland, AMD EPYC, ~9 €/Monat)
oder Netcup als günstigere Alternative (~4,50 €/Monat bei 4 vCPU/6 GB, dann etwas knapper bei
zwei parallelen Streamern gleichzeitig Whisper+Rendering+Playwright fahren). Preise ändern
sich — vor dem Kauf selbst nachschauen, hier nur als Richtwert gedacht.

**Betriebssystem:** Ubuntu 24.04 LTS (Standard-Image bei praktisch jedem Anbieter).

## 1. Grundsystem einrichten

```bash
# Als root/erster Login:
adduser autoclip
usermod -aG sudo autoclip
# Danach als autoclip weiterarbeiten (su - autoclip), nicht dauerhaft als root.

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git ffmpeg build-essential curl
```

## 2. Repo & Python-Umgebung

```bash
sudo mkdir -p /opt/auto-clipping-ai
sudo chown autoclip:autoclip /opt/auto-clipping-ai
git clone https://github.com/YunusEA12/Auto-Clipping-AI.git /opt/auto-clipping-ai
cd /opt/auto-clipping-ai

python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# Playwright braucht zusätzlich die System-Bibliotheken für den Chromium-Browser:
venv/bin/playwright install --with-deps chromium
```

## 3. `.env` (API-Keys)

Lokal auf deinem PC steht bereits eine funktionierende `.env` (Gemini-Key usw.) — die
einfach rüberkopieren, nicht neu anlegen:

```bash
# Von deinem Windows-PC aus (PowerShell), IP durch die echte Server-IP ersetzen:
scp .env autoclip@<server-ip>:/opt/auto-clipping-ai/.env
```

## 4. TikTok-Cookies — die eine Sache, die nicht automatisch "einfach so" geht

`cookies.json` kann nicht auf dem Server selbst erzeugt werden, weil es dort keinen
eingeloggten Browser gibt. Zwei Optionen:

**Empfohlen — weiterhin lokal erzeugen, dann rüberkopieren:**
```bash
# Lokal auf deinem PC, wie bisher:
python get_cookies.py
# (oder: python verify_tiktok_selectors.py --manual-login, falls get_cookies.py bei dir
#  admin-rechte-bedingt scheitert — siehe README_UPLOAD.md)

# Dann auf den Server kopieren:
scp cookies.json autoclip@<server-ip>:/opt/auto-clipping-ai/cookies.json
```
Das musst du wiederholen, sobald die Session abläuft (TikTok-Sessions halten typischerweise
Wochen bis Monate) — `tiktok_uploader.py` meldet das im Log deutlich
(`"Not logged in (redirected to .../login)"`), du merkst es also, statt dass es still
kaputtgeht.

**Alternative — Login direkt auf dem Server über einen sichtbaren Browser:**
Nur relevant, wenn du wirklich nie mehr am PC etwas kopieren willst. Braucht X11-Forwarding
oder einen VNC-Desktop auf dem Server (`ssh -X`, dann `python verify_tiktok_selectors.py
--manual-login` dort laufen lassen) — mehr Infrastruktur für denselben Effekt, deshalb nicht
die Standardempfehlung hier.

## 5. `streamers.json`

Ebenfalls gitignored, also nicht im Repo. Entweder von deinem PC rüberkopieren
(`scp streamers.json autoclip@<server-ip>:/opt/auto-clipping-ai/streamers.json`) oder leer
starten lassen und Streamer später über den Fleet-Tab im Dashboard hinzufügen.

## 6. Tailscale (sicherer Fernzugriff aufs Dashboard, ohne offenen Port)

```bash
# Auf dem Server:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Folge dem Link, der ausgegeben wird, um den Server mit deinem Tailscale-Konto zu verbinden.
Installiere Tailscale auch auf deinem PC/Handy (tailscale.com) und logge dich mit demselben
Konto ein — danach sind Server und PC im selben privaten Netzwerk, ohne dass irgendein Port
am Server öffentlich erreichbar sein muss.

Die Tailscale-IP des Servers herausfinden:
```bash
tailscale ip -4
```

## 7. Firewall — alles außer SSH und Tailscale blockieren

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp          # SSH
sudo ufw allow in on tailscale0  # Dashboard (Port 8501) nur über Tailscale erreichbar
sudo ufw enable
```

## 8. systemd-Services einrichten

Die beiden vorbereiteten Unit-Dateien liegen in `deploy/`:

```bash
sudo cp deploy/auto-clipping-supervisor.service /etc/systemd/system/
sudo cp deploy/auto-clipping-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now auto-clipping-supervisor
sudo systemctl enable --now auto-clipping-dashboard
```

`enable` sorgt dafür, dass beide Services auch nach einem Server-Neustart automatisch wieder
hochfahren.

## 9. Prüfen, ob alles läuft

```bash
sudo systemctl status auto-clipping-supervisor
sudo systemctl status auto-clipping-dashboard
journalctl -u auto-clipping-supervisor -f   # Live-Logs, Strg+C zum Beenden
```

Dashboard aufrufen: `http://<tailscale-ip-des-servers>:8501` — von jedem Gerät aus, auf dem
Tailscale mit demselben Konto läuft, egal wo es gerade physisch steht.

## Was NICHT automatisch mitkommt

- **Kein automatischer Cookie-Refresh.** Siehe Schritt 4 — das bleibt ein manueller Schritt
  alle paar Wochen.
- **Kein Zertifikat/HTTPS fürs Dashboard.** Unnötig, da es nur über Tailscales eigenes
  verschlüsseltes Netzwerk erreichbar ist, nicht über das offene Internet.
- **Kein automatisches Backup.** `uploaded_clips/`-Metadaten, `viral_memory.json`,
  `ai_guidelines.txt` und `streamers.json` sind der gelernte Zustand des Systems — ein
  einfacher Cron-Job mit `scp`/`rsync` zurück auf deinen PC (oder ein Snapshot beim
  VPS-Anbieter) ist empfehlenswert, ist hier aber bewusst nicht vorgegeben, da das von deinem
  gewünschten Backup-Rhythmus abhängt.
