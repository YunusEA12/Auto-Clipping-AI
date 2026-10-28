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
