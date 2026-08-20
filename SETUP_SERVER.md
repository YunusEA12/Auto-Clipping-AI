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
- **Kein automatischer Zugriffsschutz fürs Dashboard.** Der Server exponiert das Dashboard
  deshalb NICHT öffentlich, sondern nur über Tailscale (dein eigenes privates Netzwerk) — das
  ist die eigentliche Absicherung. `app.py` hat seit 2026-08-21 zusätzlich ein optionales
  Passwort-Login (siehe Schritt 3) als zweite Schutzschicht, falls ein Gerät in deinem
  Tailscale-Netzwerk je kompromittiert wird — das ersetzt Tailscale nicht, es ergänzt es.

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

# Playwright braucht zusätzlich die System-Bibliotheken für den Chromium-Browser.
# PLAYWRIGHT_BROWSERS_PATH muss hier gesetzt sein, weil deploy/*.service dieselbe Variable
# setzt (ProtectSystem=strict erlaubt sonst nur Lesezugriff auf $HOME, kein Schreiben in den
# Playwright-Standardpfad ~/.cache/ms-playwright) -- Install-Ort und Laufzeit-Ort müssen
# übereinstimmen:
PLAYWRIGHT_BROWSERS_PATH=/opt/auto-clipping-ai/.playwright-browsers venv/bin/playwright install --with-deps chromium
```

## 3. `.env` (API-Keys)

Lokal auf deinem PC steht bereits eine funktionierende `.env` (Gemini-Key usw.) — die
einfach rüberkopieren, nicht neu anlegen:

```bash
# Von deinem Windows-PC aus (PowerShell), IP durch die echte Server-IP ersetzen:
scp .env autoclip@<server-ip>:/opt/auto-clipping-ai/.env
```

Auf dem Server zusätzlich eine Zeile an die `.env` anhängen — das Dashboard-Passwort (zweite
Schutzschicht hinter Tailscale, siehe oben). Ein zufälliges Passwort erzeugen und einsetzen,
nicht den Platzhalter stehen lassen:
```bash
echo 'DASHBOARD_PASSWORD=<dein-passwort-hier>' >> /opt/auto-clipping-ai/.env
```
Lokal auf deinem PC absichtlich NICHT setzen — dort ist das Dashboard eh nur über localhost
erreichbar, ein Login-Screen wäre nur unnötige Reibung. Ohne `DASHBOARD_PASSWORD` in der
`.env` bleibt der Login-Screen einfach deaktiviert (siehe app.py's `_require_dashboard_password`).

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

## 8b. "Git Pull & Restart"-Button im Dashboard freischalten

Das Dashboard hat einen Button, der `git pull` ausführt und den Supervisor-Service neu
startet — ohne dass du dich per SSH einloggen musst. Er läuft als der unprivilegierte
`autoclip`-User, braucht für `systemctl restart` aber `sudo` — dafür eine eng begrenzte
passwortlose sudo-Regel installieren (erlaubt NUR diese zwei exakten Befehle, nichts
Weiteres):

```bash
which systemctl   # prüfen, ob das wirklich /usr/bin/systemctl ausgibt (Ubuntu 24.04: ja)
sudo cp deploy/sudoers-auto-clipping /etc/sudoers.d/auto-clipping
sudo chmod 440 /etc/sudoers.d/auto-clipping
sudo visudo -c    # validiert die Syntax -- IMMER nach einer Änderung unter /etc/sudoers.d/
```

Ohne diesen Schritt funktioniert der Button nicht (der `sudo systemctl restart`-Aufruf
scheitert an der fehlenden Berechtigung) — die Kernfunktionalität (Aufnahme/Analyse/Upload)
ist davon nicht betroffen, nur der Button selbst.

## 8c. journald-Wachstum begrenzen

Beide Services laufen 24/7 und schreiben pro Zyklus mehrere Log-Zeilen — ohne Obergrenze kann
`/var/log/journal` auf einem kleinen VPS irgendwann mit `output/` um denselben Plattenplatz
konkurrieren. Ein globales journald-Limit ist schnell gesetzt:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp deploy/journald-auto-clipping.conf /etc/systemd/journald.conf.d/
sudo systemctl restart systemd-journald
```

Das deckelt den persistenten Journal-Speicher auf 500 MB (siehe Kommentar in der Datei für
Details). Optional, aber empfohlen — ohne das läuft weiterhin alles, nur eben ohne
Obergrenze.

## 9. Prüfen, ob alles läuft

```bash
sudo systemctl status auto-clipping-supervisor
sudo systemctl status auto-clipping-dashboard
journalctl -u auto-clipping-supervisor -f   # Live-Logs, Strg+C zum Beenden

# Kurz gegenprüfen, dass der Fleet nicht (z. B. durch eine mitgeschleppte fleet_control.json)
# still pausiert hochgefahren ist:
journalctl -u auto-clipping-supervisor | grep "Fleet startet pausiert" || echo "OK: kein Pause-Stand gefunden"
```

Dashboard aufrufen: `http://<tailscale-ip-des-servers>:8501` — von jedem Gerät aus, auf dem
Tailscale mit demselben Konto läuft, egal wo es gerade physisch steht.

## Nachträgliches Update für einen bereits laufenden Server (2026-08-21)

Falls dein Server schon nach dieser Anleitung läuft, BEVOR `PLAYWRIGHT_BROWSERS_PATH` in
`deploy/*.service` dazukam: die schon heruntergeladenen Chromium-Binaries liegen noch unter
dem alten Default-Pfad (`~autoclip/.cache/ms-playwright`), aber die neue Unit-Datei sucht sie
unter `/opt/auto-clipping-ai/.playwright-browsers`. Ohne diesen Schritt findet Playwright nach
dem Update keinen Browser mehr → TikTok-Uploads schlagen fehl.

```bash
cd /opt/auto-clipping-ai
git pull
sudo -u autoclip PLAYWRIGHT_BROWSERS_PATH=/opt/auto-clipping-ai/.playwright-browsers \
    venv/bin/playwright install --with-deps chromium
sudo cp deploy/auto-clipping-supervisor.service deploy/auto-clipping-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart auto-clipping-supervisor auto-clipping-dashboard
```

Danach optional den alten, jetzt ungenutzten Cache löschen:
```bash
sudo -u autoclip rm -rf /home/autoclip/.cache/ms-playwright
```

Gleichzeitig auch Schritt 8c (journald-Cap) und die `fleet_control.json`-Prüfung aus Schritt
6b nachziehen, falls noch nicht geschehen — beides ist unabhängig von diesem Playwright-Fix.

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

## Anhang: Von einem Ad-hoc-Root-Setup auf den dokumentierten Weg migrieren

Falls du (wie es leicht passiert, wenn man schnell "als root eingeloggt, Repo geklont,
`screen`, fertig" macht) den Bot bereits woanders laufen hast — z. B. unter `/root/...` statt
`/opt/auto-clipping-ai`, als `root` statt `autoclip` — hier der Weg zurück zum oben
dokumentierten, gehärteten Setup, OHNE `.env`/`streamers.json`/Cookies neu einrichten zu
müssen:

```bash
# 1. Dedizierten User anlegen (falls noch nicht geschehen — siehe Schritt 1 oben)
sudo adduser --disabled-password --gecos "" autoclip

# 2. Laufende Prozesse sauber stoppen, BEVOR irgendwas verschoben wird
#    (Ctrl+C in der screen-Session, siehe process_supervisor.py's eigenes
#    Graceful-Shutdown -- niemals einfach `screen -X quit` oder `kill -9`,
#    sonst bleiben ffmpeg/streamlink-Kindprozesse verwaist zurück)

# 3. Alles außer venv/ an den neuen Ort kopieren (der abschließende "/." bei der Quelle
#    ist wichtig -- kopiert auch versteckte Dateien wie .env mit):
sudo mkdir -p /opt/auto-clipping-ai
sudo cp -a /root/Auto-Clipping-AI/. /opt/auto-clipping-ai/
sudo rm -rf /opt/auto-clipping-ai/venv    # venv-Pfade sind fest verdrahtet -- an Ort und Stelle neu bauen, nicht kopieren

# 4. Besitzer korrigieren
sudo chown -R autoclip:autoclip /opt/auto-clipping-ai

# 5. venv frisch als autoclip aufbauen
sudo -u autoclip python3 -m venv /opt/auto-clipping-ai/venv
sudo -u autoclip /opt/auto-clipping-ai/venv/bin/pip install --upgrade pip
sudo -u autoclip /opt/auto-clipping-ai/venv/bin/pip install -r /opt/auto-clipping-ai/requirements.txt
sudo -u autoclip PLAYWRIGHT_BROWSERS_PATH=/opt/auto-clipping-ai/.playwright-browsers \
    /opt/auto-clipping-ai/venv/bin/playwright install --with-deps chromium

# 6. Stichprobe: sind .env/streamers.json/client_secret.json mitgekommen?
ls -la /opt/auto-clipping-ai/.env /opt/auto-clipping-ai/streamers.json /opt/auto-clipping-ai/client_secret.json

# 6b. WICHTIG: fleet_control.json auf einen von der alten Session mitgeschleppten Pause-Stand
#     prüfen. process_supervisor.py startet unter systemd erfolgreich, startet dann aber still
#     GAR KEINE Kindprozesse, wenn hier "paused" steht -- ohne Fehlermeldung, nur eine
#     unauffällige Log-Zeile. Kommt das cp -a von oben aus einer Session, in der du den Fleet
#     zwischendurch mal pausiert hattest, wandert dieser Zustand sonst unbemerkt mit:
cat /opt/auto-clipping-ai/fleet_control.json
# Erwartet: {"target_state": "running"} (oder Datei fehlt ganz -- dann ist running der
# Default). Steht dort "paused", vor dem ersten Start korrigieren, sonst wirkt der Service
# nach außen "healthy" (aktiv, kein Crash-Loop) obwohl er de facto nichts tut. Zur Kontrolle
# nach dem Start in Schritt 9 zusätzlich prüfen:
#   journalctl -u auto-clipping-supervisor | grep "Fleet startet pausiert"

# Ab hier normal weiter mit Schritt 6 (Tailscale) / 7 (ufw) / 8 (systemd) oben -- die
# deploy/*.service-Dateien zeigen bereits auf genau diesen Pfad und User.

# 7. Erst NACH Bestätigung, dass alles unter dem neuen Pfad läuft, die alte Kopie entfernen:
# sudo rm -rf /root/Auto-Clipping-AI
```
