# Validierung vom 16.11.2026

Die Bewertung mit zwanzig Kriterien steht in [scorecard.md](scorecard.md).
Ausgangsstand: `2e8085c`; Struktur und Schutzmechanismen: `a80e81c`, Lint-Korrektur: `93492e2`.
Bewertungen sind begründete Review-Urteile, keine empirischen Erfolgsquoten.

## Tests und Paket

- Windows, Python 3.12.14: **961 bestanden, 1 plattformspezifisch übersprungen**.
- Produktions-VPS, Linux: **962 bestanden**.
- GitHub Actions: [Windows und Linux erfolgreich](https://github.com/YunusEA12/Auto-Clipping-AI/actions/runs/43774669554).
- Ruff-Lint und Formatprüfung, Paketgrenzen und Schutz gegen private Git-Pfade bestanden.
- `uv.lock` und exportierte `requirements.txt` stimmen überein.
- Wheel gebaut; Import, Ressourcen und Versions-CLI in isoliertem Prozess außerhalb des Checkouts geprüft.
- Echter CPU-FFmpeg-Integrationstest: synthetische Quelle in vertikales Video mit ASS-Untertiteln
  und normalisiertem Audio gerendert; ffprobe, Lautheit und unveränderte Quelldatei geprüft.
- Gemessene lokale Coverage: **71,48 % Statements**, **63,81 % Zweige**, **69,71 % kombiniert**.
  Die Coverage umfasst `autoclip`, einschließlich Dashboard und Wartungswerkzeugen. Externe
  Plattformaufrufe sind in Tests gesperrt; Browser-Veröffentlichungen sind dadurch nicht vollständig abgedeckt.

## Produktionsumstellung

Die Dienste wurden vor Codewechsel und Datenmigration gestoppt. Private Zustandskopien,
vorheriger Commit und Unit-Dateien liegen im privaten Runtime-Backup. Die Migration meldete
**36 Moves und 13 angepasste JSON-Dateien**. SHA-256-Vergleiche bestätigten bytegleiche Inhalte
für Ledger, Upload-Pacing, Cookies, Streamer-Konfiguration, Fleet-Zielzustand und `.env`.
Keine Zugangsdaten oder deren Hashwerte werden hier veröffentlicht.

`auto-clipping-supervisor`, `auto-clipping-dashboard` und `auto-clipping-quality-audit`
sind aktiv und für den Systemstart aktiviert. Der Supervisor verwaltet Orchestrator,
Metrics-Tracker und Upload-Worker über qualifizierte Paketmodule. Live-Aufnahmen wurden
wieder aufgenommen. Die ersten Statusprüfungen zeigten keine Neustarts und keine Fehler
im Supervisor-Journal; die Dashboard-Health-URL antwortete mit `ok`.

Ein zusätzlicher Streamlit-AppTest mit echten Produktionsdateien öffnete alle sieben
Bereiche ohne Exception, einschließlich des großen Clip-Archivs. Spitze im separaten
Testprozess: **274,7 MiB RSS**. Automatische State-Bereinigung war für diesen lesenden
Test deaktiviert. Das ist ein Funktionstest, keine Langzeit- oder Mehrbenutzer-Lastmessung.

Die langsame Pfadübersetzung großer Audit-Historien fiel bei der ersten Migration auf.
Pfadpräfixe werden inzwischen einmal pro Migration vorbereitet; die sechs Migrations-
und Integritätstests bestehen weiterhin. Bereits migrierte Installationen bleiben idempotent.

## Veröffentlichungen und Warteschlange

Ein bestätigtes Ergebnis aus der vorausgehenden Wiederherstellung ist öffentlich:
[Diese Drohne verändert alles!](https://www.tiktok.com/@clipmaster6152/video/7708523251978439938).
Es wird nicht als neue Veröffentlichung dieser Strukturumstellung gezählt.

Der neue Worker fand beim lesenden Prüflauf **54 freigegebene Kandidaten** mit Critic-
und erfolgreicher technischer QC-Prüfung, ohne abgeschlossene oder laufende Ledger-Einträge.
Der aktivierte Worker meldete `waiting / upload_limits`, nächster erlaubter Versuch:
**16.11.2026, 02:05:54 Europe/Berlin**. Die vier täglichen Versuche und drei Stunden Abstand
wurden nicht zurückgesetzt oder umgangen. Zeitpunkt und Anzahl sind eine Statusaufnahme,
keine Garantie einer späteren Plattform-Veröffentlichung.

Die angemeldete TikTok-Upload-Seite war mit dem erwarteten Datei-Input erreichbar.
Steuerelemente, die erst nach Dateiauswahl erscheinen, wurden bei diesem lesenden Check
nicht geprüft. Es wurde dabei kein Video hochgeladen oder veröffentlicht.

## Branding und offene Nachweise

Das freigegebene **ClipMaster – modern und klar** wurde als originales CM-Avatar und
75 Zeichen lange Bio in [assets/branding](../../assets/branding/README.md) vorbereitet.
TikTok verlangte beim Speichern ein menschliches Captcha. Die abschließende Profilprüfung
zeigte weiterhin die alte Darstellung und `No bio yet.`; der Live-Profilwechsel ist offen.

Hohe nächste Prioritäten bleiben die inhaltliche Sichtprüfung des alten QC-Backlogs,
mehrtägiger Betrieb mit RAM-/Ausfallmessungen, reale Upload-Erfolgsquote und belastbare
Watchtime-/Retention-Daten. Qualitätsgrenzen wurden nicht abgesenkt, um Warteschlange
oder Bewertung künstlich zu verbessern.
