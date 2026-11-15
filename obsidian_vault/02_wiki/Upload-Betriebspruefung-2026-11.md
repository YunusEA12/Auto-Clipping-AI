# Upload-Betriebsprüfung — 15.11.2026

## Nachgewiesene Ursachen

- Der produktive Supervisor war gestoppt und deaktiviert, die Fleet-Konfiguration stand auf `running`.
- Die Server-Cookies wurden von TikTok nach einer verzögerten Navigation zur Login-Seite umgeleitet.
  Der Uploader prüfte die URL nur direkt nach `DOMContentLoaded` und wartete danach irreführend
  60 Sekunden auf ein nicht vorhandenes Datei-Eingabefeld.
- Eine neuere lokale Sitzung öffnet auch vom VPS aus das Upload-Formular. Das eigene Profil
  wurde als `@clipmaster6152` inklusive Bearbeiten-Schaltfläche bestätigt.
- Der letzte Versuch hatte noch keine Datei an TikTok übergeben, aber bereits den globalen
  Drei-Stunden-Platz reserviert.
- Ein durch das Ledger abgewiesenes Duplikat konnte ebenfalls den globalen Platz verbrauchen.
- `StartLimitIntervalSec` stand in drei Service-Dateien im falschen Abschnitt; systemd ignorierte es.

## Änderungen

`wait_for_upload_form()` wartet auf das tatsächliche Datei-Eingabefeld und erkennt eine
verzögerte Login-Weiterleitung. Uploader, Sitzungsübernahme und Selektorprüfung verwenden
dieselbe Prüfung. Bei Fehlern entsteht ein privater Snapshot; die Selektorprüfung liefert
einen Fehler-Exitcode. Nicht vorhandene Editor-Selektoren vor der Videoauswahl sind weiterhin normal.

Der TikTok-Dispatcher beansprucht zunächst das Content-Hash-Ledger und erst danach den
globalen Upload-Platz. Verweigert das Pacing den Upload, wird nur der eigene Ledger-Anspruch
freigegeben. Tageslimit, Mindestabstand, Qualitätsprüfung und Veröffentlichungsfreigabe bleiben bestehen.

Tests blockieren echte TikTok-Browser zusätzlich zu den vorhandenen YouTube-/Instagram-Sperren.
Die begonnenen Ruff-Korrekturen und der aus `uv.lock` erzeugte Requirements-Export werden übernommen.

Die Post-Bestätigung akzeptiert nur eine Navigation zur Inhaltsliste oder eine passende
Veröffentlichungsmeldung. Eine Login-Weiterleitung, ein geänderter URL-Parameter oder ein
Fehler-Toast bestätigen keinen Post. Regressionstests decken diese falschen Erfolgssignale ab.

Der erste reale Übertragungsversuch erreichte 100 %, scheiterte aber vor dem Post-Klick:
TikTok zeigt beim neuen Account den Modal-Dialog **Turn on automatic content checks?**.
Das Caption-Feld war vorhanden, wurde aber vom Dialog verdeckt. Die Overlay-Behandlung
schließt gezielt diesen Einrichtungsdialog mit **Cancel**, ohne automatische Kontoprüfungen
einzuschalten. Auch unmittelbar vor der Caption-Eingabe werden die Overlays geprüft.
Browser-Fehler sichern jetzt ebenfalls private Diagnose-Snapshots.

## Bestandsaufnahme

Auf dem Server: 1.135 lokale Videos, davon 900 technisch gesperrt, 76 ohne Critic-Bewertung,
86 ohne lesbare Metadaten und 73 bewertete Kandidaten ohne bestätigten TikTok-Ledger-Eintrag.
Die Zahlen beschreiben diesen Prüfzeitpunkt; sie sind keine Veröffentlichungsstatistik.

Die 900 QC-Sperren enthalten 634-mal `frozen_frames`, 313-mal `loudness`, 66-mal
`subtitles_safezone` und 5-mal `black_frames` (Mehrfachgründe möglich).
Der vorhandene Audit enthält zehn bewertete Clips, aber keine eindeutig zugeordneten
Performance-Belege. Seine KI-Urteile belegen keine gemessene Reichweite.

Der Dashboard-Prozess belegte beim Prüfen etwa 5,4 GiB RAM auf einem VPS mit 7,8 GiB RAM.
Das ist ein Betriebsrisiko; ein Neustart kann den Speicher freigeben, belegt aber keine behobene Ursache.

## Bewertung

Technische Projektqualität: **6/10**. Gute Grundlage durch modulare Pipeline, atomare
Zustandsdateien, Duplikatschutz, umfangreiche Tests und Betrieb auf zwei Plattformen.
Die schwersten Lücken liegen zwischen grünen Unit-Tests und echter Produktion: Sitzung,
Browserinstallation, gestoppte Dienste, Speicherbedarf und ein großer gesperrter Rückstau.
Eine seriöse Bewertung der Content-Wirkung oder Wirtschaftlichkeit erfordert echte
Veröffentlichungen und belastbare Performance-Daten; der Code allein belegt sie nicht.

Nächste Prioritäten:

1. Bestätigte Posts und Dienstzustand nach jeder Auslieferung prüfen.
2. Die 900 QC-Sperren nach tatsächlicher Ursache auswerten; defekte Clips reparieren,
   Qualitätsgrenzen nicht pauschal lockern.
3. Dashboard-Speicherwachstum messen und auf die verantwortliche Datenquelle eingrenzen.
4. Uploads von Live-Aufnahmezyklen entkoppeln: der aktuelle Backlog wird nur bei laufendem
   Autopilot verarbeitet und kann während streamfreier Zeit liegen bleiben.
5. Erst danach große Funktionen und Paketstruktur schrittweise aufteilen.

## Grenzen

Ein grüner Health-Check oder ein vorhandener `sessionid`-Cookie beweist keinen Upload.
Als Ergebnis zählen nur ein bestätigter Veröffentlichungsvorgang und die Kontrolle in TikTok Studio.

## Ergebnis des echten Uploads

**Diese Drohne verändert alles!** wurde auf **@clipmaster6152** veröffentlicht:
<https://www.tiktok.com/@clipmaster6152/video/7708523251978439938>.
Der Uploader beobachtete die Navigation zur Inhaltsliste; Ledger und Archiv bestätigen
den Vorgang. Eine zweite Browser-Sitzung zeigte denselben Post in TikTok Studio mit
Sichtbarkeit **Everyone**, eigener Video-ID und zunächst **Processing...**.
Das beweist die Annahme durch TikTok, noch keine abgeschlossene Plattformverarbeitung
oder Reichweite.

Vorherige erneute Medienmessung: 47,08 Sekunden, −16,29 LUFS, null Schwarzbildsekunden,
keine gemessene eingefrorene Sequenz. Bestehender Critic-Score: 8; Auto-QC bestanden.

Nur die Warteintervalle der nachweislich **vor dem Post-Klick** gescheiterten Sitzungs-
und Modal-Versuche wurden für die kontrollierte Reparatur freigegeben. Die Tageszähler
wurden beibehalten; der bestätigte Versuch beanspruchte regulär den dritten Platz.
Es gibt keine automatische Freigabe bei unbestätigten Post-Klicks.

Supervisor, Dashboard und Audit wurden gestartet; der Supervisor ist wieder für
Systemstarts aktiviert. Die Windows-Fleet bleibt pausiert. Nach dem Dashboard-Neustart
sank der gesamte RAM-Verbrauch des VPS zunächst auf etwa 2,3 GiB; nach Start der Fleet
lag er beim ersten Check bei etwa 3,2 GiB. Das Speicherwachstum bleibt offen.

Die Live-Kontrolle zeigte doppelte Hashtags aus Beschreibung plus separater Tag-Liste.
Für folgende Uploads werden identische Tags jetzt ohne Beachtung der Großschreibung
zusammengeführt. Bereits veröffentlichter Text wurde nicht nachträglich geändert.

GitHub Actions fand zusätzlich fehlendes FFmpeg auf dem Ubuntu-Runner. Die CI installiert
FFmpeg jetzt explizit, statt sich auf vorinstallierte Runner-Werkzeuge zu verlassen.
Lokaler Abschlusslauf: **913 Tests bestanden, 1 übersprungen**, Ruff ohne Befund.
