# Inferenz und Audio-Zuverlässigkeit — 16.11.2026

Der Nachtbetrieb lieferte drei weitere TikTok-Veröffentlichungssignale um 02:08, 05:08
und 08:09 Uhr Europe/Berlin. Das ist ein technischer Upload-Nachweis; Reichweite und
Watchtime werden daraus nicht abgeleitet.

Die Prüfung zeigte reale Verbindungsabbrüche beim KI-Dienst sowie Clips außerhalb des
Lautheitsziels. Daraus folgen diese Änderungen:

- Eine OS-gesperrte Kapazität für Whisper begrenzt gleichzeitig geladene Modelle über
  Prozesse hinweg. Ein Prozessabsturz gibt den Platz frei. Der Lock umfasst auch die
  verzögerte Berechnung des Segmentgenerators.
- Transkripte werden atomar gespeichert. Leere, beschädigte und zeitlich ungültige
  Cache-Dateien werden neu erzeugt; gleichzeitige Aufrufe derselben Datei werden gesperrt.
- Reale HTTPX-Verbindungs- und Zeitfehler werden begrenzt wiederholt. Das KI-Aufruflimit
  zählt jeden Anwendungsversuch unter einem gemeinsamen Lock; korrupte Limits sperren.
- Bei ausschließlich fehlgeschlagener Lautheit wird die fertige Tonspur in zwei Durchläufen
  normalisiert und unabhängig nachgemessen. Videopakete werden unverändert kopiert.
  Die vollständige technische Qualitätsprüfung wird danach wiederholt.

Neue Regressionstests prüfen Prozessabbruch, konkurrierende Budgetreservierungen,
ungültige Transkripte, Transportfehler und echte FFmpeg-Audiokorrektur. Die Schutzgrenzen
für Standbilder, Untertitel und schwarze Frames bleiben erhalten.

Die Profilpflege übernimmt der Nutzer selbst. Die vorbereiteten Markenassets bleiben
unter `assets/branding/`; weitere automatische Profiländerungen sind nicht vorgesehen.

Siehe [Betriebsanleitung](../../docs/operations.md) und [Bewertung](../../docs/quality/scorecard.md).
