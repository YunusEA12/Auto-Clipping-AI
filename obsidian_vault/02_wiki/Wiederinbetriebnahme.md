# Wiederinbetriebnahme — 28.10.2026

## Befunde

- Lokales `main` war identisch mit `origin/main` (`cf4c504`); Fetch und Push-Dry-Run funktionierten.
- Dashboard und Supervisor waren nicht gestartet. Alte Logs dokumentierten unter anderem
  ein fehlendes `psutil`, Whisper-Alignment-Fehler und abgebrochene Stream-Aufnahmen.
  `psutil` war bei dieser Untersuchung bereits installiert; der Alignment-Fallback existierte bereits.
- Alle 701 bestehenden Tests bestanden auch vor Änderungen; sie ersetzten die Audio-/Browser-Grenzen
  jedoch weitgehend durch Test-Doubles und bestätigten daher keinen echten Pipeline-Durchlauf.
- Die alte `venv` und die von `uv` verwendete `.venv` hatten verschiedene Paketstände und Interpreter.
- Das Update auf PyAV 19 reproduzierte einen sofortigen Decoder-Abbruch in Faster-Whisper 1.2.1
  (`metadata_errors`). Die veröffentlichte Whisper-Version unterstützt den entfernten Parameter noch nicht:
  [Upstream #1589](https://github.com/SYSTRAN/faster-whisper/issues/1589).

## Entscheidungen

- `uv.lock` enthält aktualisierte, zusammen auflösbare Abhängigkeiten; `av>=11,<19` bleibt
  als begründete Kompatibilitätsgrenze in beiden Abhängigkeitsdateien stehen.
- `.python-version` legt Python 3.12 für den lokalen uv-Start fest. `start.ps1` verwendet immer
  die verwaltete `.venv`. Die alte `venv` bleibt für bestehende lokale Prozesse erhalten.
- `health_check.py` prüft lokale Voraussetzungen einschließlich echtem Whisper/PyAV-Decoding,
  ohne Browserstart, API-Anfrage oder Veröffentlichung. Der Integrationstest dekodiert und resampelt
  eine echte WAV-Datei ohne Modell-Download und verhindert die Decoder-Regression.
- Neue Python-Umgebung und Streamlit-Secrets bleiben außerhalb von Git.
- GitHub Actions prüft Pushes auf `main` und Pull Requests mit derselben gesperrten
  Python-3.12-Umgebung unter Windows; benötigt keine API-Schlüssel oder TikTok-Sitzung.

## Live-Validierung

- Gemini: echte Antworten von `gemini-3.5-flash-lite` und `gemini-3.1-flash-lite`;
  zusätzlich reale Szenenanalyse eines kurzen Transkripts (korrekt keine Highlights ausgewählt).
- Lokales Video: FFmpeg-Audioextraktion, deutsche CPU-Transkription, MediaPipe-Persistenzscan,
  Untertitel und 8-Sekunden-Render mit 1080×1920 erfolgreich.
- Twitch: Statusabfrage für alle zwölf konfigurierten Kanäle; sechs lieferten spielbare Streams.
  Zehn Sekunden Live-Aufnahme von eliasn97 mit Video/Audio und FFprobe-Prüfung erfolgreich.
- TikTok: bestehende Sitzung akzeptiert; Upload-Dateiauswahl vorhanden; Inhaltsliste mit fünf
  Einträgen inklusive Aufrufen/Likes lesbar. Editor-/Sound-Selektoren sind ohne Videoauswahl bzw.
  geöffnetes Sound-Panel nicht sichtbar; der Seitencheck allein bestätigt keinen fertigen Upload.
- Der Benutzer hat automatische TikTok-Veröffentlichungen ausdrücklich freigegeben.
  Der Supervisor verwendet weiterhin die gespeicherten Freigaben je Streamer.
- Diagnoseclips liegen ausschließlich unter `temp/`, damit sie nicht in den Upload-Backlog geraten.

## Betrieb

Start und Diagnose siehe [[../../README|README]] im Projektverzeichnis.
Dashboard: `./start.ps1`; Dauerbetrieb: `./start.ps1 supervisor`; Prüfung: `./start.ps1 check`.
Validierung: 702 Tests bestanden, ein bestehender Test übersprungen; Paketprüfung und Lockfile-Prüfung erfolgreich.
Die gestarteten lokalen Prozesse sind kein installierter Windows-Dienst und überleben keinen Neustart.
Pause/Start im Dashboard steuert die Fleet; Strg+C im Supervisor-Terminal beendet den Prozessbaum.
