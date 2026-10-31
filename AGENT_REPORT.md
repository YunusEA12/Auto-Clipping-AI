# AGENT_REPORT – Aufgabe 5: tests-echt

**Aufgabe:** Tests für ingest.py, transcribe.py, stream_watcher.py (je < 50 % Abdeckung).

**Geänderte Dateien (nur Tests, kein Produktivcode)**
- `tests/test_ingest.py` (8 Tests): echtes ffmpeg erzeugt einen 2-s-Testclip (Video+Sinuston), `extract_audio` wird auf 16 kHz/mono/16 bit und Dauer geprüft; Überspringen vorhandener WAV, Fehlerfälle (fehlende/kaputte Datei), `download_from_url` mit gefaktem yt-dlp, `main`.
- `tests/test_transcribe.py` (8 Tests): gefaktes `WhisperModel` mit deutschen Wort-Zeitstempeln; JSON-Format, Thread-Begrenzung, Wiederverwendung, faster-whisper-Alignment-Bug-Retry ohne Wortzeiten, leere Segmente, `main`.
- `tests/test_stream_watcher_pipeline.py` (14 Tests): `process_chunk` (Energie-Filter, Logging, Aufräumen, Fehler), `render_hot_clips` (kein Upload ohne `publish`, Upload/Fehlerstatus, Render-Fehler), `_drain_completed`, `watch_stream` (Chunks, Retry nach Aufnahmefehler), `main` (Flag-Validierung, Profil), `cleanup_stale_chunks` mit Geschwisterdateien.

**Tests:** `uv run --locked pytest -q` → 813 passed (Baseline 783). Abdeckung (pytest-cov, nur zur Messung per `--with`, nicht ins Projekt aufgenommen): ingest 27 % → 96 %, transcribe 44 % → 98 %, stream_watcher 47 % → 90 %.

**Hinweise:** Keine Netzwerkzugriffe, keine Modelle, keine Uploads (Upload/Notify/Process sind gefaked). Test für ingest benötigt das `ffmpeg`-Binary (in CI vorhanden, da bestehende Tests es ebenfalls nutzen sollten – bitte prüfen, ob die Windows-CI ffmpeg hat). Nicht abgedeckt: `record_stream_chunk`-Fehlerzweige, `resolve_streamlink_path`.
