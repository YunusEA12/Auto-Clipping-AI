# System-Architektur

Übersicht über die 24/7 Supervisor-getriebene Pipeline. Siehe [[index]] für den Wiki-Überblick.

## Phasen

- **Phase 1 — Supervision & Monitoring:** `process_supervisor.py` verwaltet `orchestrator.py` und `metrics_tracker.py`. Echtzeit-Stream-Erfassung über `stream_watcher.py` (Twitch).
- **Phase 2 — Analyse & Vision Critic:** `auto_pilot.py` steuert Audio-Extraktion → Whisper-Transkription → LLM-Analyse → `train_loop.py` (Vision Critic / Feedback-Loop).
- **Phase 3 — Render & Output:** `process.py` rendert via FFmpeg, `tiktok_uploader.py` übernimmt die Playwright-Automatisierung für den Upload.
- **Dashboard:** `app.py` (Streamlit), read-only, entkoppelt über `dashboard_api.py`.

## Details

- Video-Layout-Regeln: siehe [[Layout-Logik]]
- Pro-Streamer-Konfiguration: siehe [[Streamer-Settings]]
- Laufender Betriebszustand: siehe [[Status]]
