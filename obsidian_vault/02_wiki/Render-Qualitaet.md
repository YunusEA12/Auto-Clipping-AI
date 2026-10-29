# Render-Qualitaet

Die Clip-Auswahl wird vor dem Rendern anhand realer Whisper-Wortzeiten auf Satzgrenzen erweitert. Ohne Wortzeiten gelten Segmentgrenzen. Vorlauf 0,3 Sekunden, Nachlauf 0,5 Sekunden; Quelldauer und 15–150 Sekunden werden danach erneut geprüft. Überlange Sätze werden verworfen.

Facecam-Ausschnitte werden an fünf über den Clip verteilten Zeitpunkten geprüft. Mindestens 90 Prozent eines erkannten Gesichts müssen im gewählten Ausschnitt liegen. Fehlende Gesichter, verschobene Kameras oder Lesefehler führen zum Vollbild mit unscharfem Hintergrund. Jeder Multi-Cam-Slot wird geprüft; `layout_validation` dokumentiert die Entscheidung.

Nach dem Rendern enthält der atomar geschriebene Sidecar `auto_qc`. FFmpeg misst Schwarzbild (ab 0,5 Sekunden), Standbild (ab 2 Sekunden) und integrierte Lautheit (Ziel −14 LUFS, Toleranz ±3 LU). Facecam und Untertitel werden separat geprüft. Messfehler sperren den Upload. Der fertige Audiomix und der Quellton ohne Musik werden mit loudnorm I=-14:TP=-1:LRA=11 normalisiert und danach gemessen.

Untertitel werden anhand der generierten ASS-Textgeometrie konservativ auf sichere Grenzen geprüft; dies ist keine OCR des gerenderten Bildes. Gesichtsstichproben erkennen keine beliebig kurzen Layoutwechsel zwischen den Messpunkten. Die technischen Prüfungen garantieren keine inhaltliche Qualität oder Viralität.

Gesperrte Clips bleiben in output erhalten, auch bei niedrigem Kritiker-Score und automatischer Bereinigung. Upload und Wiederholungsversuche respektieren die Sperre. Das Dashboard zeigt die Dateien und Gründe unter Clip-Prüfung. Historische Clips ohne auto_qc behalten den bisherigen Upload-Vertrag.

Im Split-Screen müssen in mindestens vier von fünf Render-Stichproben Gesichter in der oberen Bildhälfte liegen. Andere Layouts versprechen keine einzelne obere Facecam. Titel haben oben fünf Prozent Sicherheitsabstand; der Split-Screen-Titel sitzt bei 1080×1920 nun bei y=180 statt y=100. Untertitel verwenden horizontal 5–90 Prozent und vertikal 10–85 Prozent des Bildes.

## Sperrquote und Korrektur der Textpruefung

Die Breite wird mit 0,63 em je Zeichen geschaetzt. ASS-WrapStyle, Style- und Event-Raender sowie Wortumbrueche bestimmen die Zeilenzahl. Ein zu breites Einzelwort bleibt gesperrt. Dies ist weiterhin eine geometrische Naeherung, keine libass-Pixelmessung. Facecam-Pruefungen tolerieren nun einen verpassten Treffer unter fuenf Stichproben.

`python qc_summary.py --root output -n 20` fasst die neuesten 20 MP4-Dateien rekursiv nach Dateizeit zusammen. blocked_fraction ist der Anteil gesperrter an gueltig geprueften Clips; fehlende QC und ungueltige Berichte werden separat ausgewiesen. Bei null auswertbaren Berichten ist der Anteil null (JSON null), nicht 0 Prozent. Gruende werden pro Clip einmal gezaehlt. Das Skript veraendert keine Dateien.
