# Render-Qualitaet

Die Clip-Auswahl wird vor dem Rendern anhand realer Whisper-Wortzeiten auf Satzgrenzen erweitert. Ohne Wortzeiten gelten Segmentgrenzen. Vorlauf 0,3 Sekunden, Nachlauf 0,5 Sekunden; Quelldauer und 15–150 Sekunden werden danach erneut geprüft. Überlange Sätze werden verworfen.

Facecam-Ausschnitte werden an fünf über den Clip verteilten Zeitpunkten geprüft. Mindestens 90 Prozent eines erkannten Gesichts müssen im gewählten Ausschnitt liegen. Fehlende Gesichter, verschobene Kameras oder Lesefehler führen zum Vollbild mit unscharfem Hintergrund. Jeder Multi-Cam-Slot wird geprüft; `layout_validation` dokumentiert die Entscheidung.

Nach dem Rendern enthält der atomar geschriebene Sidecar `auto_qc`. FFmpeg misst Schwarzbild (ab 0,5 Sekunden), Standbild (ab 2 Sekunden) und integrierte Lautheit (Ziel −14 LUFS, Toleranz ±3 LU). Facecam und Untertitel werden separat geprüft. Messfehler sperren den Upload. Die Lautheit wird gemessen, nicht automatisch normalisiert.

Untertitel werden anhand der generierten ASS-Textgeometrie konservativ auf sichere Grenzen geprüft; dies ist keine OCR des gerenderten Bildes. Gesichtsstichproben erkennen keine beliebig kurzen Layoutwechsel zwischen den Messpunkten. Die technischen Prüfungen garantieren keine inhaltliche Qualität oder Viralität.

Gesperrte Clips bleiben in output erhalten, auch bei niedrigem Kritiker-Score und automatischer Bereinigung. Upload und Wiederholungsversuche respektieren die Sperre. Das Dashboard zeigt die Dateien und Gründe unter Clip-Prüfung. Historische Clips ohne auto_qc behalten den bisherigen Upload-Vertrag.

Im Split-Screen müssen in mindestens vier von fünf Render-Stichproben Gesichter in der oberen Bildhälfte liegen. Andere Layouts versprechen keine einzelne obere Facecam. Titel haben oben fünf Prozent Sicherheitsabstand; der Split-Screen-Titel sitzt bei 1080×1920 nun bei y=180 statt y=100. Untertitel verwenden horizontal 5–90 Prozent und vertikal 10–85 Prozent des Bildes.
