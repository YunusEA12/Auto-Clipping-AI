# Projektwaechter mit Antigravity

Der Wartungsagent verwendet die offizielle `agy` CLI mit gespeicherter Google-Konto-Anmeldung. Kein Gemini-API-Fallback. Die bestehende Clip-Pipeline verwendet weiterhin ihren eigenen Gemini-Zugang; diese Umstellung betrifft nur den neuen Wartungsagenten.

`python project_guardian.py` prueft einmal ohne Modellaufruf. `python project_guardian.py --watch --enable-agent --agy /pfad/zu/agy` prueft alle 1800 Sekunden. JSON-Bericht: `guardian_reports/latest.json`; atomarer Zustand: `guardian_state.json`. Ein FileLock verhindert parallele Laeufe. Pro UTC-Tag sind maximal acht Antigravity-Laeufe vorgesehen, maximal zehn Minuten je Aufruf. Das begrenzt Aufrufe und Laufzeit, garantiert aber keine feste Tokenzahl oder Eurogrenze. Bei unveraendert gesundem Zustand bleibt das Modell still. Neue Clips koennen einmal taeglich eine Verbesserungsanalyse ausloesen.

Geprueft werden Orchestrator-Aktualitaet, absichtlich pausierte Fleet, freier Speicher, QC-Sperrquote und vorhandene Performance-Stichproben. An Google gehen aggregierte Zahlen und bekannte Fehlerkategorien, keine Cookies, Passwoerter oder Rohprotokolle. Ein Bericht ist keine Benachrichtigung per E-Mail. Fehlende Daten und Modellprognosen sind keine gemessene Watchtime.

Optional: `--repair-workspace /separater/checkout` erlaubt Korrekturkandidaten in einem sauberen Git-Checkout auf einem `codex/guardian*`-Branch. Anschliessend prueft der Wrapper unabhaengig mit pytest. Er fuehrt keine Merges oder Produktionsdeployments aus. Ein gruener Testlauf ist keine Garantie fuer einen guten Patch. CLI-Rechte und Betriebssystem-Isolation muessen separat eingerichtet werden; Anweisungen im Prompt sind keine Sandbox. Keine globalen Freigaben mit `--dangerously-skip-permissions` setzen.

Fuer musikalische Verbesserungen existiert bereits optimization_engine.py: Ausreichend gemessene Uploads pro Musik-/Hook-Gruppe sind Voraussetzung. Die Nutzungsrechte eines Musikstuecks werden dadurch nicht nachgewiesen. Ein taeglicher Bericht beziehungsweise Regeln sind keine Aenderung der Modellgewichte und kein eigenes trainiertes Grundmodell.

## Server einrichten

Die Windows-Anmeldung wird nicht automatisch zum Server kopiert. Die offizielle CLI auf dem Server als Dienstbenutzer `autoclip` installieren, beispielsweise nach `/opt/auto-clipping-ai/.tools/bin`. Einmal interaktiv mit dem gewuenschten persoenlichen Google-Konto anmelden. Die CLI darf nicht auf `modelProvider=gemini` oder Google-Cloud-ADC umgeschaltet sein; zusaetzliche Credit-Overages im Antigravity-Konto deaktiviert lassen, wenn nur das Abo verwendet werden soll. Der Wrapper gibt keine API-/Cloud-Schluessel an die CLI weiter.

Danach einen einzelnen Lauf unter demselben Benutzer, HOME und derselben virtuellen Umgebung pruefen. Erst nach erfolgreichem Subscription-Test `deploy/auto-clipping-guardian.service` als Systemdienst installieren und aktivieren. Die Unit erwartet die CLI in `.tools/bin` und Python in `venv/bin`. Dateirechte und Zugriff auf den Google-Keyring muessen fuer genau diesen Dienstbenutzer funktionieren. Kein Passwort oder Token ins Repository schreiben.

Ein unter Windows gestarteter Agent arbeitet nur bei eingeschaltetem PC. Fuer unabhaengigen Dauerbetrieb ist die Server-Unit erforderlich. Diese Datei oder eine vorbereitete Unit bestaetigt keinen aktivierten Dienst.

Offizielle Referenzen: [Headless CLI](https://antigravity.google/docs/cli/headless/), [Google-Anmeldung](https://antigravity.google/docs/install), [Abo und Kontingente](https://antigravity.google/docs/plans).

## Lokaler Betrieb unter Windows

`install_guardian.ps1` installiert die Benutzeraufgabe Auto-Clipping-Antigravity-Guardian alle 30 Minuten und startet einen ersten Lauf. Kein Administrator oder gespeichertes Windows-Passwort notwendig. Sie laeuft nur unter dem angemeldeten Benutzer bei eingeschaltetem PC. `install_guardian.ps1 -Stop` entfernt ausschliesslich diese Aufgabe. Sie startet weder die lokale Fleet noch einen Serverdienst. Diese lokale Einrichtung prueft nur lokale Projektdaten; bei pausierter Fleet ist ein gesunder Bericht keine Aussage ueber den Serverbetrieb. Automatische Reparaturkandidaten und Uebernahmen sind dort nicht aktiviert.
