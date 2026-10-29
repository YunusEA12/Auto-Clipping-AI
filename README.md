# Auto-Clipping-AI 🎬🤖

Auto-Clipping-AI ist eine modulare, hardware-schonende Python-Pipeline, die den gesamten Prozess der Content-Erstellung für Plattformen wie TikTok, YouTube Shorts und Instagram Reels automatisiert. 

Das Tool nimmt lange Video-Dateien (oder Streams) entgegen, transkribiert diese lokal auf der CPU und nutzt Large Language Models (LLMs), um virale oder spannende Segmente semantisch zu erkennen. Anschließend wird das Video automatisch in ein 9:16 Split-Screen-Format (Facecam + Gameplay) gecroppt und mit dynamischen, wortgenauen Untertiteln versehen.

## 🚀 Kernfunktionen
- **Automatisierte Ingestion:** Herunterladen und Extrahieren von Audiospuren via `yt-dlp` und `FFmpeg`.
- **Lokale Transkription:** CPU-optimiertes Speech-to-Text mit wortgenauen Timestamps (via `faster-whisper`).
- **Intelligente Szenenauswahl:** Semantische Analyse des Transkripts durch LLMs, um die besten 30- bis 60-sekündigen Clips basierend auf Humor, Emotionen und Hooks zu finden.
- **Auto-Compositing:** Vollautomatischer Schnitt, Split-Screen-Generierung (9:16) und Hardcoding von `.ass`-Untertiteln.

## 🛠️ Tech-Stack
- **Sprache:** Python 3
- **Media Processing:** FFmpeg, ffmpeg-python
- **AI & NLP:** faster-whisper (lokal), LLM APIs (für strukturierte JSON-Szenenanalyse)
- **Architektur:** Optimiert für Systeme ohne dedizierte Nvidia-GPU (Intel/CPU-Fokus).

## Lokal starten (Windows / PowerShell)

Voraussetzungen: Python >=3.11, [uv](https://docs.astral.sh/uv/getting-started/installation/)
und FFmpeg/FFprobe auf `PATH`. API-Schlüssel stehen ausschließlich in der lokalen `.env`
(Vorlage: `.env.example`). TikTok benötigt zusätzlich deine lokale `cookies.json`;
siehe [README_UPLOAD.md](README_UPLOAD.md).

```powershell
git pull --ff-only
uv sync --locked --group dev
uv run --locked playwright install chromium
./start.ps1 check
./start.ps1                 # Dashboard auf http://127.0.0.1:8501
```

In einem zweiten Terminal startet `./start.ps1 supervisor` die Stream-Überwachung und
die Metrikerfassung. Das Dashboard allein startet keine Hintergrundverarbeitung.
Beenden mit Strg+C; Fleet-Pause/Start erfolgt im Dashboard. Automatische Veröffentlichungen
richten sich nach `auto_upload` und `publish` je Streamer in `streamers.json`.

„Clips behalten“ bedeutet nur, dass der Critic den Clip akzeptiert hat. Der separate
Radar-Zähler „TikTok bestätigt“ zählt bestätigte Veröffentlichungen seit Agent-Start.
Für lokale Verarbeitung erscheint ein eigener Hinweis. In der Streamer-Verwaltung müssen
für echte Posts sowohl Auto-Upload als auch Veröffentlichen eingeschaltet sein; laufende
Agenten laden geänderte Startoptionen erst beim Neustart. Nicht bestätigte Post-Klicks
bleiben lokal und werden niemals als erfolgreiche Veröffentlichung gezählt.

Die Startdatei verwendet immer `uv.lock` und `.venv`, unabhängig von einer eventuell noch
vorhandenen alten `venv`. Keine manuelle Aktivierung erforderlich. Alternativ direkt:
`uv run --locked python process_supervisor.py` bzw.
`uv run --locked streamlit run app.py --server.address 127.0.0.1`.

Prüfen: `./start.ps1 test` (Tests) und `./start.ps1 check` (lokale Voraussetzungen,
einschließlich echtem Audiodecoding; keine API-Aufrufe oder Veröffentlichungen).
Ein grüner lokaler Check bestätigt keine gültige TikTok-Sitzung. Diese lässt sich mit
`uv run --locked python verify_tiktok_selectors.py --headless` separat prüfen.
Editor- und Sound-Selektoren erscheinen erst nach Videoauswahl bzw. Öffnen des Sound-Panels.

Abhängigkeiten gezielt aktualisieren: `uv lock --upgrade`, dann `uv sync --locked --group dev`
und Tests ausführen. `av<19` ist absichtlich begrenzt: Faster-Whisper 1.2.1 verwendet einen
in PyAV 19 entfernten Parameter; siehe [Upstream-Fehler #1589](https://github.com/SYSTRAN/faster-whisper/issues/1589).
Die Decoder-Integration wird deshalb mit einer echten WAV-Datei ohne Modell-Download getestet.

## Die nächsten zehn Clips prüfen

```powershell
uv run --locked python clip_quality_audit.py --init --limit 10 --check-after-hours 12
./start.ps1 audit
```

Der laufende Autopilot sichert die nächsten zehn neu gerenderten Videos vor Löschung oder
Upload in `quality_audit/`. Der separate Prüfer bewertet Video **mit Ton** und protokolliert
je 0–10 für Schnitt, Untertiteltext/Timing, Textposition/Safe-Zone, Bildlayout, Audio,
Sinn/Hook/Payoff und Viralitätspotenzial. Fehlende Evidenz ergibt „nicht prüfbar“.
Die Prüfung läuft zusätzlich zum bisherigen Critic und verändert keine Veröffentlichungsfreigaben.

Ergebnisse: `quality_audit/report.md`, pro Clip `assessment.md/json`. Videos, Transkripte,
Messwerte und Berichte bleiben privat und außerhalb von Git. Der erste Check nach zwölf Stunden
wird unter `quality_audit/checkpoint_12h.md/json` gesichert. Er enthält nur tatsächlich gemessene
Performance; eine Viralitätsprognose ist keine Garantie und ersetzt keine Watchtime-/Retention-Daten.
Weniger als zehn neue Clips werden ausdrücklich als unvollständige Stichprobe ausgewiesen.
Eine vorhandene Kampagne wird durch erneutes `--init` nicht zurückgesetzt.

Die Windows-Prozesse und ein lokaler Codex-Check benötigen einen eingeschalteten PC.
Für PC-unabhängigen Betrieb muss die Pipeline samt Prüfer auf einem erreichbaren Server
laufen. Dafür liegt `deploy/auto-clipping-quality-audit.service` bereit; Einrichtung siehe
[SETUP_SERVER.md](SETUP_SERVER.md). Ein Git-Push allein aktualisiert keine laufenden Serverprozesse.
