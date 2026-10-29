# Serverbetrieb nach Wiederinbetriebnahme

Am 29.10.2026 wurde die bestehende Linux-Installation auf den aktuellen Repository-Stand
gebracht. Die lokale Windows-Fleet bleibt pausiert; nur der Server führt den Dauerbetrieb
mit seinen vorhandenen Streamer-/Facecam-Einstellungen aus.

## Laufzeit und Validierung

- Fast-Forward-Update ohne Überschreiben vorhandener Streamer-Konfiguration.
- Installation der gesperrten Abhängigkeiten in der bestehenden Python-3.12-Umgebung.
- Playwright-Browser an dem bereits von systemd verwendeten Installationsort aktualisiert.
- Root-eigene alte Laufzeitverzeichnisse verhinderten Aufnahmen und Diagnosesnapshots.
  `temp/` und `selector_audit/` gehören jetzt dem Dienstbenutzer. Der lokale Health-Check
  prüft zusätzlich Schreibrechte der Laufzeitverzeichnisse.
- Reale Live-Aufnahme, Audioextraktion, deutsche CPU-Transkription, MediaPipe-Scan und
  1080×1920-Render auf dem VPS erfolgreich. Die Testdateien bleiben außerhalb des Upload-Backlogs.
- Auf Linux 731 Tests bestanden. Die GitHub-Prüfung umfasst Windows und Linux;
  Windows-spezifische Konstanten werden in simulierten Windows-Tests ausdrücklich bereitgestellt.
- Supervisor, Dashboard und zusätzlicher Audit-Dienst sind als systemd-Dienste eingerichtet.
  Die bestehende Firewall, Dashboard-Anmeldung und Dienste anderer Anwendungen bleiben erhalten.

## Datenübergabe und Zugriff

Die zehn abgeschlossenen Bewertungen wurden mit ihren Videos/Transkriptkontexten auf den
Server übertragen. Absolute Snapshot-Pfade wurden dabei auf den Linux-Installationspfad
umgeschrieben. Schlüssel und lokale Zugangshilfen waren ausdrücklich nicht Teil des Archivs.
Bestätigte lokale Upload-Belege wurden mit dem vorhandenen Server-Ledger zusammengeführt,
damit bereits veröffentlichte Dateien nicht erneut als neue Uploads gelten.

Im geschützten Dashboard zeigt der Reiter **Clip-Prüfung** den Bericht und einen JSON-Download.
Der Audit-Dienst aktualisiert die gemessene Performance und erzeugt zum festgelegten
Zwölf-Stunden-Termin einen unveränderlichen Checkpoint. Das funktioniert unabhängig vom PC.
Ein lokaler Chat-Follow-up benötigt weiterhin den PC und die Desktop-App.

Der separat freigegebene temporäre SSH-Wartungszugang wird nach Abschluss entfernt.
Die Anwendung benötigt diesen Root-Zugang nicht zum Weiterlaufen. Ein Git-Push aktualisiert
weiterhin nicht automatisch die laufende Serverinstanz; ein Update muss explizit installiert
und validiert werden.
