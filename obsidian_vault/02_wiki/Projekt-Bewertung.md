# Projekt-Bewertung (kritisch, 1–100)

Stand: 29.10.2026, **Version 3** · Grundlage: Code auf `main`-Stand bc0e65d, 55 Commits, 777 Tests (alle grün, lokal ausgeführt), Wiki, README, Deploy-Dateien, Branch `feature/agent-automated-refactoring`.
Nicht geprüft: echte gerenderte Clips, Live-Performance auf TikTok, das Dashboard im Browser. Bereiche, die davon abhängen, sind als *Schätzung* markiert.

Skala: **1–30** kritisch · **31–50** schwach · **51–70** solide mit Lücken · **71–85** gut · **86–100** herausragend.

## Gesamtbild

| # | Bereich | Punkte | Gewicht | Kurzurteil |
|---|---|---:|---:|---|
| 1 | Geschäftsmodell & Monetarisierung | **30** | 15 % | Creator-Rewards-Idee liegt ungemergt, keine Entscheidung |
| 2 | Produktvision & Zielklarheit | **45** | 5 % | Technisch klar, geschäftlich unklar |
| 3 | Datenbasis & Feedback-Loop | **35** | 12 % | Nur Views/Likes, kaum Volumen |
| 4 | Wachstum, Distribution & Content-Strategie | **40** | 12 % | Ein Kanal, keine Posting-Strategie |
| 5 | KI-Clip-Auswahl (Analyse + Critic) | **63** | 12 % | Schnitte jetzt auf Satzgrenzen; Treffsicherheit unbelegt (*Schätzung*) |
| 6 | Rendering, Layout & Untertitel | **74** | 10 % | Layoutprüfung über den ganzen Clip; Fallback evtl. zu streng (*Schätzung*) |
| 7 | Upload-Automatisierung | **35** | 8 % | **Neue Qualitätssperre blockiert derzeit jeden Clip mit Untertiteln** |
| 8 | Architektur & Modularität | **50** | 5 % | Flache Skriptsammlung, Großdateien |
| 9 | Codequalität & Lesbarkeit | **55** | 4 % | Robust, aber Kommentar-Überladung |
| 10 | Tests & CI | **80** | 4 % | Viele Tests, aber der Sperr-Fehler rutschte durch |
| 11 | Betrieb, Deploy & 24/7 | **68** | 5 % | Läuft jetzt auf dem VPS; Alarm und Deploy noch manuell |
| 12 | Skalierbarkeit | **35** | 3 % | JSON-Dateien, Browser-Slots, ein Account |
| 13 | Kosten & LLM-Abhängigkeit | **55** | 2 % | Günstig, aber Free-Tier-Hopping |
| 14 | Sicherheit & Secrets | **67** | 2 % | Gute Regeln, riskantes `git add -A` |
| 15 | Dokumentation & Wissensmanagement | **75** | 1 % | Ausführlich, teils veraltet |
| 16 | Dashboard & Bedienbarkeit | **63** | — | Refresh hinter Proxy repariert; *Schätzung* |
| 17 | Entwicklungsprozess & Fokus | **58** | — | Viel Feuerwehr, wenig Roadmap |

**Gewichteter Gesamtwert: 49 / 100** (Version 2: 50, Version 1: 48). **Nach Behebung des Sperr-Fehlers: ≈ 51.**
Ungewichtet (Technik zählt gleich viel wie Geschäft): 55 / 100 (Version 2: 55).

> **Kritischer Befund (V3):** `auto_qc.check_subtitles()` schätzt jede Untertitelzeile als 1 em pro Zeichen breit und kennt keinen Zeilenumbruch. Ein normaler 4-Wort-Block bei Schriftgröße 80 (z. B. „DIGGA WAS IST DAS“) gilt damit schon als zu breit. Nachgestellt mit `process.build_ass_for_clip()`: 13 von 13 Untertitel-Events verletzen die Grenze, auch bei kurzem Titel → `auto_qc.passed = false` → **kein Upload mehr**. Der Fehler blieb unentdeckt, weil die Tests nur kurze Kunsttexte prüfen. Vor dem nächsten Server-Update beheben.

Rechte sind geklärt: Die geclippten Streamer erlauben Clips ausdrücklich. Der Bereich ist deshalb nicht Teil der Bewertung.

Kernaussage: **Technisch ein ernstzunehmendes 65–70-Punkte-Projekt, geschäftlich ein 25-Punkte-Projekt.** Der Engpass ist nicht der Code, sondern Monetarisierung, Messdaten und Reichweite. Weitere Technik-Arbeit erhöht den Erfolg kaum, solange diese drei Punkte offen sind.

---

## 1 · Geschäftsmodell & Monetarisierung — 30/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Definierte Einnahmequelle | 30 | Branch `feature/agent-automated-refactoring` (TICKET-001, 06.09.) hebt die Mindestlänge auf 61 s für TikTok Creator Rewards. Nicht gemergt: `main` erlaubt weiter 15-s-Clips, die dafür nicht zählen. Ziel und Code widersprechen sich |
| Zielgruppe/Kunde | 25 | Unklar, ob eigener Clip-Kanal, Dienstleistung für Streamer oder SaaS |
| Wettbewerbsabgrenzung | 30 | Markt hat etablierte Clipping-Tools (z. B. Opus Clip, Vizard, StreamLadder); Alleinstellung (deutsche Streams, Facecam-Agent, Critic) nicht formuliert |
| Kosten-Nutzen-Messung | 30 | LLM-Kosten werden gezählt, Erträge nicht |

**Sofort entscheiden:** TICKET-001 mergen oder verwerfen. Wenn Creator Rewards das Ziel ist, müssen Selektor-Prompt, Critic und Qualitätsaudit auf Clips über 1 Minute ausgerichtet werden (Teilnahmebedingungen vorher aktuell prüfen).

**Drei realistische Wege (eine Entscheidung nötig):**
- **A · Bezahltes Clipping für Streamer** (Agentur-Modell): Streamer zahlt pro Clip/Monat oder pro Views. Nutzt die bestehende Clip-Erlaubnis direkt. *Empfehlung.*
- **B · Clipping-Kampagnen** (Bezahlung pro 1.000 Views durch Streamer/Marken): schnell startbar.
- **C · SaaS für deutschsprachige Streamer:** größtes Potenzial, braucht aber Multi-Tenant-Architektur (siehe Bereich 12).

## 2 · Produktvision & Zielklarheit — 45/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Technische Vision | 75 | 24/7-Pipeline von Stream bis Post klar beschrieben |
| Messbares Ziel | 20 | Kein Ziel wie „X Follower / Y € bis Datum“ |
| Konsistenz | 40 | README verspricht TikTok, YouTube Shorts, Instagram; Code läuft im TikTok-only-Modus |
| Roadmap | 35 | Wiki dokumentiert Vergangenheit, nicht Zukunft |

**Maßnahmen:** Eine Seite „Ziele & Roadmap“ mit 3 messbaren Quartalszielen; README auf den echten Funktionsumfang bringen.

## 3 · Datenbasis & Feedback-Loop — 35/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Erhobene Metriken | 35 | Nur Views/Likes; keine Watchtime, Retention, Completion (laut `optimization_engine.py` selbst) |
| Datenquelle | 30 | TikTok-Metriken per DOM-Scraping, fragil |
| Datenvolumen | 20 | Code nennt „very low upload volume“; Buckets brauchen Mindestanzahl → Loop lernt praktisch nichts |
| Ehrlichkeit der Auswertung | 85 | Vorbildlich: keine erfundenen Metriken, „insufficient data“ statt Overfitting |
| Experimente (A/B) | 20 | Kein geplantes Experiment-Design (Hook-Varianten, Längen, Uhrzeiten) |

**Maßnahmen:** YouTube Analytics API (Retention) anbinden; TikTok Business/Creator-Analytics prüfen; pro Woche ein klares Experiment (z. B. 15–30 s vs. 45–60 s); Volumen erhöhen, bevor der Loop Entscheidungen trifft.

## 4 · Wachstum, Distribution & Content-Strategie — 40/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Plattform-Breite | 30 | Nur TikTok aktiv; YouTube/Instagram-Code vorhanden, aber abgeschaltet |
| Posting-Rhythmus | 50 | Pacing-Gate vorhanden, aber keine Strategie nach Uhrzeit/Zielgruppe |
| Hooks/Titel | 55 | `top_performers.json` enthält nur 3 generische Beispiele, keine eigenen Daten |
| Nischenfokus | 45 | Deutsche Gaming-Streamer ist eine gute Nische, aber nicht bewusst positioniert |
| Community/Interaktion | 15 | Keine Kommentar-Antworten, keine Serienformate |

**Maßnahmen:** YouTube Shorts wieder einschalten (offizielle API = weniger Risiko); Serienformate („Elias' Ausraster der Woche“); Hook-Bibliothek aus eigenen Top-Clips statt Beispielen.

## 5 · KI-Clip-Auswahl — 63/100 (*Schätzung*)

| Kriterium | Punkte | Befund |
|---|---:|---|
| Prompt-Design | 70 | Strukturierte JSON-Ausgabe, Kohärenzregel, „Quality gate, not quota“ |
| Signale | 60 | Transkript + Audio-Energie + Triggerwörter; kein Chat-Signal (Twitch-Chat-Spikes sind oft das stärkste Signal) |
| Vision-Critic | 55 | ~52 % Überlebensrate laut Code-Kommentar; Treffsicherheit nicht gegen echte Performance validiert |
| Modellwahl | 45 | Billigstes Lite-Modell als Selektor *und* Critic |
| Halluzinationsschutz | 70 | Zeitstempel-Grenzen und Dauerprüfung vorhanden |
| Schnittgrenzen | 75 | Neu: `align_clip_boundaries()` erweitert Clips auf Whisper-Satzgrenzen (+0,3 s Vorlauf, +0,5 s Nachlauf), prüft danach erneut die Länge. Rest-Risiko: ohne Satzzeichen gilt das ganze Whisper-Segment, `wave.Error` bei defekter Audiodatei wird nicht abgefangen |

**Maßnahmen:** Twitch-Chat-Aktivität als Signal; Critic-Urteil mit gemessener Performance korrelieren (sonst ist er Bauchgefühl); für die finale Auswahl ein stärkeres Modell testen, Lite nur zum Vorfiltern.

## 6 · Rendering, Layout & Untertitel — 74/100 (*Schätzung*)

| Kriterium | Punkte | Befund |
|---|---:|---|
| Layout-Logik | 80 | Split-Screen, Multi-Cam-Grids, Facecam-Agent gegen Spielgesichter; neu: Ausschnitt wird an 5 Zeitpunkten geprüft, sonst Vollbild mit Unschärfe. Risiko: Ein einziger verpasster Gesichtstreffer (Kopf gedreht, Hand vor dem Gesicht) reicht schon für den Fallback — evtl. zu streng, Quote im Betrieb beobachten |
| Untertitel | 72 | Wortgenaue `.ass`, Escaping und Isolation getestet |
| Audio | 60 | Lautheit wird jetzt gemessen (Ziel −14 ±3 LUFS), aber nicht normalisiert. Liegen Streams typischerweise leiser, sperrt die Prüfung viele Clips — vorher an echten Clips messen oder `loudnorm` im Render ergänzen |
| Wartbarkeit | 50 | `process.py` 1.334 Zeilen |
| Nachweis | 60 | Echter 1080×1920-Render von Live-Aufnahme auf dem VPS validiert; 10 Audit-Bewertungen liegen auf dem Server (nicht im Repo, hier nicht eingesehen) |

**Maßnahmen:** Qualitätsaudit der nächsten 10 Clips wirklich auswerten; Lautheit auf ca. −14 LUFS normalisieren; `process.py` in Layout/Untertitel/Audio/Encode aufteilen.

## 7 · Upload-Automatisierung — 35/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Sicherheitsmodell | 85 | Kein Upload ohne `auto_upload` *und* `publish`; unbestätigte Posts zählen nie als Erfolg |
| Duplikatschutz | 85 | Globales Content-Hash-Ledger |
| Robustheit | 35 | Selektor- und Sprach-Abhängigkeit (Fix für englischen Publish-Dialog zeigt die Fragilität) |
| Plattformrisiko | 25 | Browser-Automation + Cookies = Sperr- und Wartungsrisiko |
| Qualitätssperre (neu) | 10 | Idee richtig (technisch fehlerhafte Clips bleiben lokal, Gründe im Dashboard), aber die Untertitel-Schätzung sperrt derzeit alle Clips (siehe Befund oben). Nach Korrektur realistisch 60–70 |

**Maßnahmen:** TikTok Content Posting API prüfen und, wenn erreichbar, den Playwright-Upload ablösen; bis dahin Selektor-Check täglich automatisch laufen lassen.

## 8 · Architektur & Modularität — 50/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Pipeline-Trennung | 65 | Phasen klar (Supervisor → Autopilot → Render → Upload) |
| Paketstruktur | 30 | 32 Python-Dateien flach im Root, kein Paket |
| Dateigrößen | 40 | `process.py` 1.334, `auto_pilot.py` 1.094, `app.py` 1.039 Zeilen |
| Zustandshaltung | 40 | ~25 JSON-State-Dateien + Locks statt einer Datenbank |
| Konfiguration | 50 | Pfade und Modellnamen als Modul-Konstanten |

**Maßnahmen:** Paket `autoclip/` mit `ingest/`, `analysis/`, `render/`, `publish/`, `metrics/`; State nach SQLite (eine Datei, Transaktionen, Abfragen fürs Dashboard).

## 9 · Codequalität & Lesbarkeit — 55/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Robustheit | 70 | Atomare Writes, Retries, Backoff |
| Fehlerbehandlung | 50 | 58× `except Exception` im Produktivcode |
| Kommentar-Dichte | 35 | Kommentare sind Tagebuch: datierte Vorfallsgeschichten (z. B. ~20 Zeilen nur zur Modellwahl in `analyze.py`). Gehört in Commits/Wiki, nicht in den Code |
| Typisierung | 55 | Teilweise Type Hints, kein Typecheck in CI |

**Maßnahmen:** Kommentare auf das „Warum heute“ kürzen; `ruff` + `mypy` (oder `pyright`) in CI.

## 10 · Tests & CI — 80/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Umfang | 85 | 777 Tests, ca. 12 s Laufzeit. Aber: Tests mit Kunstdaten haben den Sperr-Fehler nicht gefunden — es fehlt ein Test mit echter `build_ass_for_clip()`-Ausgabe |
| CI | 85 | Windows + Ubuntu, gepinnte Actions, `uv --locked` |
| Integration | 65 | Echter Audio-Decode-Test; kein End-to-End-Render mit Beispielvideo |
| Lint/Typen/Coverage | 40 | Nicht in CI |

## 11 · Betrieb, Deploy & 24/7 — 68/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Supervisor & Self-Healing | 70 | Prozess-Supervisor, Heartbeats, Backoff |
| Laufumgebung | 80 | Dauerbetrieb auf Linux-VPS, Windows-Fleet pausiert, PC nicht mehr nötig |
| Server-Deploy | 65 | Supervisor, Dashboard, Audit als systemd-Dienste; Ende-zu-Ende-Lauf (Aufnahme → Transkription → Render) nachgewiesen. Updates aber manuell: Push aktualisiert den Server nicht |
| Health-Check | 70 | Prüft jetzt Schreibrechte der Laufzeitordner (Ursache des Root-Ordner-Ausfalls) |
| Monitoring/Alarme | 40 | Weiterhin kein externer Alarm bei Ausfall |

**Maßnahmen:** Externer Heartbeat-Check mit Push-Alarm; ein Deploy-Skript (`git pull` → `uv sync --locked` → Health-Check → Dienste neu starten), damit Updates reproduzierbar sind; README auf Serverbetrieb umstellen (sagt noch „PC muss an sein“).

## 12 · Skalierbarkeit — 35/100

Ein TikTok-Account, Browser-Slots als Engpass, JSON-State, serielles Rendering auf einer CPU. Für 3 Streamer okay, für 30 nicht. Erst relevant, wenn Weg A/C gewählt wird.

## 13 · Kosten & LLM-Abhängigkeit — 55/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Kostenniveau | 80 | Lokales Whisper, billigstes Gemini-Modell, Tagesbudget |
| Stabilität | 30 | Modell-Pool springt über Free-Tier-Kontingente; mehrere Modelle bereits 404 — Ausfälle sind eingeplant statt verhindert |
| Anbieterbindung | 50 | Nur Gemini, kein Abstraktions-Layer |

**Maßnahmen:** Bezahlten Tarif mit festem Modell; Modellname in Konfiguration statt Code.

## 14 · Sicherheit & Secrets — 67/100

Gute `.gitignore`, Redaction in Logs, Secret-Regeln in `claude.md`. Neu: Dashboard hinter Anmeldung und Firewall, Schlüssel bewusst nicht im Server-Archiv. Offen: Der temporäre Root-SSH-Wartungszugang soll laut `Serverbetrieb.md` entfernt werden — erledigen und abhaken. Risiko: `scripts/git_auto_sync.sh` macht `git add -A` und verlässt sich allein auf `.gitignore` — ein neuer, unbekannter Cookie-Dateiname landet automatisch im Repo (der Fall `youtube_studio_cookies.json` ist schon einmal passiert). **Maßnahme:** Allowlist statt `add -A` oder Secret-Scan (z. B. gitleaks) als Pre-Commit/CI.

## 15 · Dokumentation — 74/100

README, Server-Setup, Upload-README, Obsidian-Wiki, neu `Serverbetrieb.md` (gut: dokumentiert Grenzen ehrlich). Schwächen: README widerspricht dem TikTok-only-Modus; `Status.md` ist eine Dateiliste statt Status; Doku-Mix Deutsch/Englisch.

## 16 · Dashboard — 63/100 (*Schätzung*)

Streamlit, sauber über `dashboard_api.py` entkoppelt, Qualitätsbericht sichtbar. Auto-Refresh nutzt jetzt Streamlits eigenen Timer statt des iframe-Pakets, das hinter dem Proxy versagte. Rest: `streamlit-autorefresh` steht noch in `pyproject.toml`, wird aber nicht mehr importiert — entfernen. Fehlt vermutlich: Geschäftskennzahlen (Follower, Views/Clip, Kosten/Clip, Umsatz).

## 17 · Entwicklungsprozess & Fokus — 58/100

Viele `fix:`-Commits zu Vorfällen, schubweise Arbeit (Pausen von Wochen). Positiv: Der Server-Umzug wurde zu Ende gebracht und dokumentiert. Negativ: Ein Branch mit geschäftsrelevanter Änderung (TICKET-001) liegt seit über sieben Wochen ungemergt. Die Energie fließt in Zuverlässigkeit, nicht in Wachstum. Regel „automatisch `git add .` und push nach jeder Aufgabe“ in `claude.md` widerspricht sauberem Review.

---

## Top-10-Maßnahmen nach Hebelwirkung

| Prio | Maßnahme | Bereich | Aufwand | Wirkung |
|---|---|---|---|---|
| 0 | **Untertitel-Prüfung in `auto_qc.py` reparieren, bevor der Server aktualisiert wird** (sonst keine Uploads) | 6, 7 | klein | kritisch |
| 1 | Geschäftsmodell wählen und TICKET-001 (Creator Rewards, ≥ 61 s) entscheiden | 1 | klein | sehr hoch |
| 2 | 3 messbare Ziele für 90 Tage festlegen und im Dashboard anzeigen | 2, 16 | klein | hoch |
| 3 | YouTube Shorts per offizieller API wieder einschalten + Retention-Daten holen | 3, 4 | mittel | hoch |
| 4 | Qualitätsaudit der 10 Clips auswerten, Critic gegen echte Views prüfen; Sperr- und Fallback-Quote von `auto_qc` beobachten | 5, 6 | klein | hoch |
| 5 | Twitch-Chat-Spikes als Auswahlsignal | 5 | mittel | mittel–hoch |
| 6 | TikTok Content Posting API statt Browser-Upload prüfen | 7 | mittel | mittel–hoch |
| 7 | Externer Ausfall-Alarm + Deploy-Skript für den VPS (Umzug selbst erledigt ✓) | 11 | klein | mittel |
| 8 | Bezahlter LLM-Tarif mit festem Modell | 13 | klein | mittel |
| 9 | Secret-Scan statt blindem `git add -A` | 14 | klein | mittel |
| 10 | Paketstruktur + SQLite + Kommentare entschlacken | 8, 9, 12 | groß | mittel (erst bei Skalierung) |

## Zielwerte nach 90 Tagen

| Bereich | Heute | Ziel |
|---|---:|---:|
| Geschäftsmodell | 30 | 60 |
| Datenbasis | 35 | 60 |
| Wachstum | 40 | 60 |
| Betrieb | 68 | 80 |
| **Gesamt (gewichtet)** | **49** | **≈ 64** |

## Änderungen Version 2 → 3

| Bereich | V2 | V3 | Grund |
|---|---:|---:|---|
| Upload-Automatisierung | 50 | 35 | Neue Qualitätssperre blockiert jeden Clip mit Untertiteln |
| KI-Clip-Auswahl | 60 | 63 | Schnitte auf Satzgrenzen |
| Rendering | 72 | 74 | Layoutprüfung über den ganzen Clip, Titel aus der oberen Gefahrenzone |
| Tests & CI | 82 | 80 | +46 Tests, aber kritischer Fehler unentdeckt |
| Dokumentation | 74 | 75 | `Render-Qualitaet.md` dokumentiert Grenzen ehrlich |

## Änderungen Version 1 → 2

| Bereich | V1 | V2 | Grund |
|---|---:|---:|---|
| Betrieb | 45 | 68 | VPS-Dauerbetrieb, Ende-zu-Ende validiert, Rechte-Check im Health-Check |
| Geschäftsmodell | 25 | 30 | Creator-Rewards-Ansatz gefunden (Branch), aber nicht entschieden |
| Rendering | 70 | 72 | Echter Render auf dem Server nachgewiesen |
| Dashboard | 60 | 63 | Refresh hinter Proxy repariert |
| Entwicklungsprozess | 55 | 58 | Server-Umzug abgeschlossen |
| Sicherheit | 65 | 67 | Dashboard-Anmeldung, Schlüssel nicht im Archiv |
| Dokumentation | 72 | 74 | `Serverbetrieb.md` |

## Methodik und Grenzen

- Bewertung durch Claude; Gemini war mangels Schlüssel nicht verfügbar, GPT-Gutachten über die ChatGPT-App angefordert (`/council`).
- Code, Tests und Konfiguration wurden direkt geprüft; Videoqualität, Dashboard-Bedienung und Plattform-Performance nicht.
- Aussagen zu Plattformregeln (Content Posting API) vor Umsetzung an aktuellen offiziellen Richtlinien prüfen.

Verwandt: [[System-Architektur]] · [[Clip-Qualitaetsaudit]] · [[Status]]
