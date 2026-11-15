# Professionelles Projektlayout — 16.11.2026

Die flachen Backend-Module sind in das installierbare Paket `src/autoclip/` umgezogen.
Die elf Fachpakete bilden Aufnahme, KI, Rendering, Veröffentlichung, Steuerung,
Konfiguration, Qualität, Überwachung, Dashboard, gemeinsame Infrastruktur und Werkzeuge ab.
`app.py` bleibt lediglich als kleiner Streamlit-Einstieg erhalten.

Verbindliche Beschreibung: [Architektur](../../docs/architecture.md).
Start-/Update-Verfahren: [Betrieb](../../docs/operations.md), [Entwicklung](../../CONTRIBUTING.md).
Die 20 bewerteten Kriterien mit Ausgangs- und aktuellem Stand stehen in
[Qualitätsbewertung](../../docs/quality/scorecard.md).

## Entscheidungen

- Ein `uv.lock`, ein installierbares Paket, ein zentraler CLI-Einstieg `autoclip`.
- Zentral definierte Runtime-Pfade: `config/` und privates `var/` statt verstreuter Zustandsdateien.
- Journalisierte Migration mit Kollisionsprüfung, Rücknahme bei fehlgeschlagenen Moves und
  unveränderten Ledger-/Quota-/Credential-Inhalten.
- Eigenständiger Upload-Worker statt Abhängigkeit vom nächsten Live-Aufnahmezyklus.
- Auch manuelle Uploads verwenden die gemeinsamen Schutzmechanismen.
- Das Dashboard lädt nur den geöffneten Tab, höchstens sechs Archiv-Clips und Download-Daten
  erst beim Klick. Reines Monitoring importiert keine Inferenz-/Rendering-Modelle.
- Keine Veröffentlichung ohne Ledger-Reservierung. Defekte State-Dateien führen zu einer
  sichtbaren Sperre und werden nicht als leere Historie interpretiert.
- Serverupdates gehören in den getesteten Wartungsablauf; ein Git-Pull aus dem laufenden
  Dashboard ersetzt keine Installation oder Migration.

Historische Wiki-Seiten beschreiben ihre damaligen Dateinamen. Für aktuelle Pfade und
Befehle gelten die hier verlinkten Dokumente.

## Geprüfter Betrieb

Am 16.11.2026 wurden die neue Struktur und die Runtime-Migration auf dem VPS angewendet.
36 Moves und 13 Pfadübersetzungen; Ledger, Upload-Limits, Cookies, Streamer-Konfiguration,
Fleet-Freigabe und `.env` blieben bytegleich. Alle drei Dienste laufen mit neuen
Paket-Einstiegen, inklusive eigenständigem Upload-Worker. 54 Kandidaten bestanden dessen
Freigaben. Der nächste zulässige Upload-Versuch liegt bei 02:05:54 Uhr Europe/Berlin.

Die vollständigen Prüfungen und Grenzen stehen im
[Validierungsbericht](../../docs/quality/validation-2026-11-16.md).
