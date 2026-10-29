# Projekt-Bewertung (kritisch, 1–100)

Stand: 29.10.2026 · Grundlage: Code auf `main`-Stand f829a75, 50 Commits, 731 Tests (alle grün, lokal ausgeführt), Wiki, README, Deploy-Dateien.
Nicht geprüft: echte gerenderte Clips, Live-Performance auf TikTok, das Dashboard im Browser. Bereiche, die davon abhängen, sind als *Schätzung* markiert.

Skala: **1–30** kritisch · **31–50** schwach · **51–70** solide mit Lücken · **71–85** gut · **86–100** herausragend.

## Gesamtbild

| # | Bereich | Punkte | Gewicht | Kurzurteil |
|---|---|---:|---:|---|
| 1 | Recht, Rechte & Plattform-Compliance | **20** | 15 % | Größtes Einzelrisiko: fremde Streams ohne dokumentierte Erlaubnis |
| 2 | Geschäftsmodell & Monetarisierung | **25** | 10 % | Kein definierter Weg zu Einnahmen |
| 3 | Produktvision & Zielklarheit | **45** | 5 % | Technisch klar, geschäftlich unklar |
| 4 | Datenbasis & Feedback-Loop | **35** | 10 % | Nur Views/Likes, kaum Volumen |
| 5 | Wachstum, Distribution & Content-Strategie | **40** | 10 % | Ein Kanal, keine Posting-Strategie |
| 6 | KI-Clip-Auswahl (Analyse + Critic) | **60** | 10 % | Durchdacht, aber unbelegt (*Schätzung*) |
| 7 | Rendering, Layout & Untertitel | **70** | 8 % | Stärkster Produktteil (*Schätzung*) |
| 8 | Upload-Automatisierung | **50** | 7 % | Sehr sorgfältig, aber prinzipiell fragil |
| 9 | Architektur & Modularität | **50** | 5 % | Flache Skriptsammlung, Großdateien |
| 10 | Codequalität & Lesbarkeit | **55** | 4 % | Robust, aber Kommentar-Überladung |
| 11 | Tests & CI | **82** | 4 % | Klare Stärke |
| 12 | Betrieb, Deploy & 24/7 | **45** | 4 % | Hängt am Windows-PC, Server halb fertig |
| 13 | Skalierbarkeit | **35** | 3 % | JSON-Dateien, Browser-Slots, ein Account |
| 14 | Kosten & LLM-Abhängigkeit | **55** | 2 % | Günstig, aber Free-Tier-Hopping |
| 15 | Sicherheit & Secrets | **65** | 2 % | Gute Regeln, riskantes `git add -A` |
| 16 | Dokumentation & Wissensmanagement | **72** | 1 % | Ausführlich, teils veraltet |
| 17 | Dashboard & Bedienbarkeit | **60** | — | *Schätzung*, nicht im Browser geprüft |
| 18 | Entwicklungsprozess & Fokus | **55** | — | Viel Feuerwehr, wenig Roadmap |

**Gewichteter Gesamtwert: 44 / 100.**
Ungewichtet (Technik zählt gleich viel wie Geschäft): 51 / 100.

Kernaussage: **Technisch ein ernstzunehmendes 65–70-Punkte-Projekt, geschäftlich ein 25-Punkte-Projekt.** Der Engpass ist nicht der Code, sondern Rechte, Monetarisierung und Messdaten. Weitere Technik-Arbeit erhöht den Erfolg kaum, solange diese drei Punkte offen sind.

---

## 1 · Recht, Rechte & Plattform-Compliance — 20/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Erlaubnis der Streamer | 10 | Profile für eliasn97, Trymacs, montanablack88; nirgends ist eine Erlaubnis, ein Clipping-Programm oder eine Vereinbarung dokumentiert |
| Urheberrecht Musik/Spiele | 30 | Eigene Hintergrundmusik im Repo (gut); Spiel- und Stream-Musik im Originalton wird nicht geprüft |
| Plattform-AGB (Automatisierung) | 20 | TikTok-Upload und Metriken per Playwright-Browsersteuerung und Cookie-Sitzungen statt offizieller API; Kontosperre möglich |
| „Unoriginal Content“-Regeln | 20 | Reine Re-Uploads fremder Streams gelten auf TikTok/YouTube/Instagram oft als unoriginell → Reichweite und Monetarisierung gedrosselt (Regeln vor Umsetzung aktuell prüfen) |
| Kennzeichnung/Credits | 25 | Kein erkennbarer Pflicht-Credit („Clip von @streamer“) in Titel/Overlay |

**Maßnahmen (höchste Priorität):**
1. Nur Streamer clippen, die es schriftlich erlauben oder ein offizielles Clipping-Programm betreiben. Pro Profil Feld `permission: {source, date, terms}` und Pipeline-Sperre ohne Eintrag.
2. Pflicht-Credit des Streamers in Caption und Overlay.
3. TikTok Content Posting API prüfen und, wenn erreichbar, Playwright-Upload ablösen.
4. Eigenleistung erhöhen (Kommentar-Hook, Kontext-Text, Schnitt), damit Clips nicht als reiner Re-Upload gelten.

## 2 · Geschäftsmodell & Monetarisierung — 25/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Definierte Einnahmequelle | 15 | Nirgends beschrieben |
| Zielgruppe/Kunde | 25 | Unklar, ob eigener Clip-Kanal, Dienstleistung für Streamer oder SaaS |
| Wettbewerbsabgrenzung | 30 | Markt hat etablierte Clipping-Tools (z. B. Opus Clip, Vizard, StreamLadder); Alleinstellung (deutsche Streams, Facecam-Agent, Critic) nicht formuliert |
| Kosten-Nutzen-Messung | 30 | LLM-Kosten werden gezählt, Erträge nicht |

**Drei realistische Wege (eine Entscheidung nötig):**
- **A · Bezahltes Clipping für Streamer** (Agentur-Modell): Streamer zahlt pro Clip/Monat oder pro Views. Löst gleichzeitig das Rechteproblem. *Empfehlung.*
- **B · Clipping-Kampagnen** (Bezahlung pro 1.000 Views durch Streamer/Marken): schnell startbar, Erlaubnis inklusive.
- **C · SaaS für deutschsprachige Streamer:** größtes Potenzial, braucht aber Multi-Tenant-Architektur (siehe Bereich 13).

## 3 · Produktvision & Zielklarheit — 45/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Technische Vision | 75 | 24/7-Pipeline von Stream bis Post klar beschrieben |
| Messbares Ziel | 20 | Kein Ziel wie „X Follower / Y € bis Datum“ |
| Konsistenz | 40 | README verspricht TikTok, YouTube Shorts, Instagram; Code läuft im TikTok-only-Modus |
| Roadmap | 35 | Wiki dokumentiert Vergangenheit, nicht Zukunft |

**Maßnahmen:** Eine Seite „Ziele & Roadmap“ mit 3 messbaren Quartalszielen; README auf den echten Funktionsumfang bringen.

## 4 · Datenbasis & Feedback-Loop — 35/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Erhobene Metriken | 35 | Nur Views/Likes; keine Watchtime, Retention, Completion (laut `optimization_engine.py` selbst) |
| Datenquelle | 30 | TikTok-Metriken per DOM-Scraping, fragil |
| Datenvolumen | 20 | Code nennt „very low upload volume“; Buckets brauchen Mindestanzahl → Loop lernt praktisch nichts |
| Ehrlichkeit der Auswertung | 85 | Vorbildlich: keine erfundenen Metriken, „insufficient data“ statt Overfitting |
| Experimente (A/B) | 20 | Kein geplantes Experiment-Design (Hook-Varianten, Längen, Uhrzeiten) |

**Maßnahmen:** YouTube Analytics API (Retention) anbinden; TikTok Business/Creator-Analytics prüfen; pro Woche ein klares Experiment (z. B. 15–30 s vs. 45–60 s); Volumen erhöhen, bevor der Loop Entscheidungen trifft.

## 5 · Wachstum, Distribution & Content-Strategie — 40/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Plattform-Breite | 30 | Nur TikTok aktiv; YouTube/Instagram-Code vorhanden, aber abgeschaltet |
| Posting-Rhythmus | 50 | Pacing-Gate vorhanden, aber keine Strategie nach Uhrzeit/Zielgruppe |
| Hooks/Titel | 55 | `top_performers.json` enthält nur 3 generische Beispiele, keine eigenen Daten |
| Nischenfokus | 45 | Deutsche Gaming-Streamer ist eine gute Nische, aber nicht bewusst positioniert |
| Community/Interaktion | 15 | Keine Kommentar-Antworten, keine Serienformate |

**Maßnahmen:** YouTube Shorts wieder einschalten (offizielle API = weniger Risiko); Serienformate („Elias' Ausraster der Woche“); Hook-Bibliothek aus eigenen Top-Clips statt Beispielen.

## 6 · KI-Clip-Auswahl — 60/100 (*Schätzung*)

| Kriterium | Punkte | Befund |
|---|---:|---|
| Prompt-Design | 70 | Strukturierte JSON-Ausgabe, Kohärenzregel, „Quality gate, not quota“ |
| Signale | 60 | Transkript + Audio-Energie + Triggerwörter; kein Chat-Signal (Twitch-Chat-Spikes sind oft das stärkste Signal) |
| Vision-Critic | 55 | ~52 % Überlebensrate laut Code-Kommentar; Treffsicherheit nicht gegen echte Performance validiert |
| Modellwahl | 45 | Billigstes Lite-Modell als Selektor *und* Critic |
| Halluzinationsschutz | 70 | Zeitstempel-Grenzen und Dauerprüfung vorhanden |

**Maßnahmen:** Twitch-Chat-Aktivität als Signal; Critic-Urteil mit gemessener Performance korrelieren (sonst ist er Bauchgefühl); für die finale Auswahl ein stärkeres Modell testen, Lite nur zum Vorfiltern.

## 7 · Rendering, Layout & Untertitel — 70/100 (*Schätzung*)

| Kriterium | Punkte | Befund |
|---|---:|---|
| Layout-Logik | 78 | Split-Screen, Multi-Cam-Grids, Facecam-Agent gegen Spielgesichter — stark |
| Untertitel | 72 | Wortgenaue `.ass`, Escaping und Isolation getestet |
| Audio | 65 | Musik-Mixing getestet; Lautheits-Normalisierung (LUFS) prüfen |
| Wartbarkeit | 50 | `process.py` 1.334 Zeilen |
| Nachweis | 50 | Qualitätsaudit eingerichtet, Ergebnisse noch ausstehend |

**Maßnahmen:** Qualitätsaudit der nächsten 10 Clips wirklich auswerten; Lautheit auf ca. −14 LUFS normalisieren; `process.py` in Layout/Untertitel/Audio/Encode aufteilen.

## 8 · Upload-Automatisierung — 50/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Sicherheitsmodell | 85 | Kein Upload ohne `auto_upload` *und* `publish`; unbestätigte Posts zählen nie als Erfolg |
| Duplikatschutz | 85 | Globales Content-Hash-Ledger |
| Robustheit | 35 | Selektor- und Sprach-Abhängigkeit (Fix für englischen Publish-Dialog zeigt die Fragilität) |
| Plattformrisiko | 25 | Browser-Automation + Cookies = Sperr- und Wartungsrisiko |

## 9 · Architektur & Modularität — 50/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Pipeline-Trennung | 65 | Phasen klar (Supervisor → Autopilot → Render → Upload) |
| Paketstruktur | 30 | 28 Python-Dateien flach im Root, kein Paket |
| Dateigrößen | 40 | `process.py` 1.334, `auto_pilot.py` 1.094, `app.py` 1.039 Zeilen |
| Zustandshaltung | 40 | ~25 JSON-State-Dateien + Locks statt einer Datenbank |
| Konfiguration | 50 | Pfade und Modellnamen als Modul-Konstanten |

**Maßnahmen:** Paket `autoclip/` mit `ingest/`, `analysis/`, `render/`, `publish/`, `metrics/`; State nach SQLite (eine Datei, Transaktionen, Abfragen fürs Dashboard).

## 10 · Codequalität & Lesbarkeit — 55/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Robustheit | 70 | Atomare Writes, Retries, Backoff |
| Fehlerbehandlung | 50 | 58× `except Exception` im Produktivcode |
| Kommentar-Dichte | 35 | Kommentare sind Tagebuch: datierte Vorfallsgeschichten (z. B. ~20 Zeilen nur zur Modellwahl in `analyze.py`). Gehört in Commits/Wiki, nicht in den Code |
| Typisierung | 55 | Teilweise Type Hints, kein Typecheck in CI |

**Maßnahmen:** Kommentare auf das „Warum heute“ kürzen; `ruff` + `mypy` (oder `pyright`) in CI.

## 11 · Tests & CI — 82/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Umfang | 88 | 731 Tests, 55 Testdateien, 8 s Laufzeit |
| CI | 85 | Windows + Ubuntu, gepinnte Actions, `uv --locked` |
| Integration | 65 | Echter Audio-Decode-Test; kein End-to-End-Render mit Beispielvideo |
| Lint/Typen/Coverage | 40 | Nicht in CI |

## 12 · Betrieb, Deploy & 24/7 — 45/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Supervisor & Self-Healing | 70 | Prozess-Supervisor, Heartbeats, Backoff |
| Laufumgebung | 30 | Läuft auf privatem Windows-PC; README sagt selbst: PC muss an sein |
| Server-Deploy | 45 | systemd-Units vorhanden, Betrieb nicht nachgewiesen |
| Monitoring/Alarme | 40 | `notify.py` und Health-Check vorhanden, kein externer Alarm bei Ausfall |

**Maßnahmen:** VPS-Umzug abschließen; externer Uptime-/Heartbeat-Check mit Push-Alarm.

## 13 · Skalierbarkeit — 35/100

Ein TikTok-Account, Browser-Slots als Engpass, JSON-State, serielles Rendering auf einer CPU. Für 3 Streamer okay, für 30 nicht. Erst relevant, wenn Weg A/C gewählt wird.

## 14 · Kosten & LLM-Abhängigkeit — 55/100

| Kriterium | Punkte | Befund |
|---|---:|---|
| Kostenniveau | 80 | Lokales Whisper, billigstes Gemini-Modell, Tagesbudget |
| Stabilität | 30 | Modell-Pool springt über Free-Tier-Kontingente; mehrere Modelle bereits 404 — Ausfälle sind eingeplant statt verhindert |
| Anbieterbindung | 50 | Nur Gemini, kein Abstraktions-Layer |

**Maßnahmen:** Bezahlten Tarif mit festem Modell; Modellname in Konfiguration statt Code.

## 15 · Sicherheit & Secrets — 65/100

Gute `.gitignore`, Redaction in Logs, Secret-Regeln in `claude.md`. Risiko: `scripts/git_auto_sync.sh` macht `git add -A` und verlässt sich allein auf `.gitignore` — ein neuer, unbekannter Cookie-Dateiname landet automatisch im Repo (der Fall `youtube_studio_cookies.json` ist schon einmal passiert). **Maßnahme:** Allowlist statt `add -A` oder Secret-Scan (z. B. gitleaks) als Pre-Commit/CI.

## 16 · Dokumentation — 72/100

README, Server-Setup, Upload-README, Obsidian-Wiki. Schwächen: README widerspricht dem TikTok-only-Modus; `Status.md` ist eine Dateiliste statt Status; Doku-Mix Deutsch/Englisch.

## 17 · Dashboard — 60/100 (*Schätzung*)

Streamlit, sauber über `dashboard_api.py` entkoppelt, Qualitätsbericht sichtbar. Fehlt vermutlich: Geschäftskennzahlen (Follower, Views/Clip, Kosten/Clip, Umsatz).

## 18 · Entwicklungsprozess & Fokus — 55/100

Viele `fix:`-Commits zu Vorfällen, schubweise Arbeit (Pausen von Wochen). Die Energie fließt in Zuverlässigkeit, nicht in Wachstum. Regel „automatisch `git add .` und push nach jeder Aufgabe“ in `claude.md` widerspricht sauberem Review.

---

## Top-10-Maßnahmen nach Hebelwirkung

| Prio | Maßnahme | Bereich | Aufwand | Wirkung |
|---|---|---|---|---|
| 1 | Geschäftsmodell wählen (Empfehlung: bezahltes Clipping für Streamer mit Erlaubnis) | 1, 2 | klein | sehr hoch |
| 2 | Erlaubnis-Feld pro Streamer + Pipeline-Sperre + Pflicht-Credit | 1 | klein | sehr hoch |
| 3 | 3 messbare Ziele für 90 Tage festlegen und im Dashboard anzeigen | 3, 17 | klein | hoch |
| 4 | YouTube Shorts per offizieller API wieder einschalten + Retention-Daten holen | 4, 5 | mittel | hoch |
| 5 | Qualitätsaudit der 10 Clips auswerten, Critic gegen echte Views prüfen | 6, 7 | klein | hoch |
| 6 | Twitch-Chat-Spikes als Auswahlsignal | 6 | mittel | mittel–hoch |
| 7 | Auf VPS umziehen + externer Ausfall-Alarm | 12 | mittel | mittel |
| 8 | Bezahlter LLM-Tarif mit festem Modell | 14 | klein | mittel |
| 9 | Secret-Scan statt blindem `git add -A` | 15 | klein | mittel |
| 10 | Paketstruktur + SQLite + Kommentare entschlacken | 9, 10, 13 | groß | mittel (erst bei Skalierung) |

## Zielwerte nach 90 Tagen

| Bereich | Heute | Ziel |
|---|---:|---:|
| Recht & Compliance | 20 | 70 |
| Geschäftsmodell | 25 | 60 |
| Datenbasis | 35 | 60 |
| Wachstum | 40 | 60 |
| Betrieb | 45 | 70 |
| **Gesamt (gewichtet)** | **44** | **≈ 65** |

## Methodik und Grenzen

- Einzelbewertung durch Claude; das Multi-Modell-Gremium (GPT/Gemini) war in dieser Sitzung nicht verbunden. Eine Zweitmeinung kann manuell eingeholt werden.
- Code, Tests und Konfiguration wurden direkt geprüft; Videoqualität, Dashboard-Bedienung und Plattform-Performance nicht.
- Aussagen zu Plattformregeln (Unoriginal Content, Content Posting API) vor Umsetzung an aktuellen offiziellen Richtlinien prüfen.

Verwandt: [[System-Architektur]] · [[Clip-Qualitaetsaudit]] · [[Status]]
