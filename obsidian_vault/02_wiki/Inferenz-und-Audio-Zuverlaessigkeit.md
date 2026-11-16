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

## Produktionsnachweis der Audiokorrektur

Eine private Kopie des tatsächlich wegen Lautheit gesperrten Clips
`clip_2_Warum_sie_damals_nicht_zur_Polizei_ging.mp4` wurde auf dem VPS geprüft.
Die integrierte Lautheit verbesserte sich von **−17,88 auf −15,37 LUFS**, innerhalb
der bestehenden Toleranz. Der Hash der kopierten Videopakete blieb identisch;
die ursprüngliche Clipdatei blieb vollständig unverändert. Die Testkopie wurde
nach der Messung entfernt und niemals in die Upload-Warteschlange aufgenommen.

Zusätzlich wurden bestehende Whisper-Transkripte geprüft. Die Wortausrichtung kann
Segmentgrenzen überschreiten (beobachtet: Wortstart 137,54 s, Segmentstart 137,82 s).
Die Validierung akzeptiert solche gemessenen Werte und prüft weiterhin auf endliche,
nichtnegative und geordnete Zeitintervalle. Diese reale Besonderheit hat einen eigenen
Regressionstest erhalten.

Der Code-Stand `e97ec2d` wurde mit dem Wartungsskript auf dem VPS ausgerollt:
**986 Tests bestanden**, alle drei Dienste aktiv, Dashboard-Health `ok`, keine
Supervisor-Neustarts unmittelbar nach dem Deployment. Beide vorhandenen Produktions-
Transkripte bestanden den lesenden Validierungslauf. Der Upload-Worker wartet gemäß
bestehender Quote bis 11:07:53 Uhr Europe/Berlin auf den nächsten zulässigen Versuch.

Die [plattformübergreifende CI](https://github.com/YunusEA12/Auto-Clipping-AI/actions/runs/43828900523)
enthält die Linux- und Windows-Prüfungen für diesen Code-Stand. Die Gesamtbewertung liegt
jetzt bei 83,3/100. Produktwirkung bleibt mangels belastbarer Reichweiten-/Watchtime-Daten
bei 30; die ersten gespeicherten Zählwerte sind null und werden nicht als Wachstum ausgelegt.
