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