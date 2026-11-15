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

## Bestandsaufnahme

Auf dem Server: 1.135 lokale Videos, davon 900 technisch gesperrt, 76 ohne Critic-Bewertung,
86 ohne lesbare Metadaten und 73 bewertete Kandidaten ohne bestätigten TikTok-Ledger-Eintrag.
Die Zahlen beschreiben diesen Prüfzeitpunkt; sie sind keine Veröffentlichungsstatistik.

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
