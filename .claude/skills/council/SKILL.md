---
name: council
description: KI-Council aus Claude, GPT und Gemini, der auch in Cloud-Sitzungen läuft. Nutzen bei "/council", "Council", "Gremium", "zweite Meinung", "Kreuzprüfung", "Cross-Review", "Konsens", "lass GPT/Gemini drüberschauen" und bei wichtigen Entscheidungen, Architektur, sicherheitsrelevantem Code oder Projektbewertungen.
---

# Council (Cloud-tauglich)

Claude ist Orchestrator und verantwortet das Ergebnis. GPT (OpenAI API) und Gemini (Gemini API)
sind unabhängige Gutachter. Ziel: mehr Fehler finden, blinde Flecken aufdecken, Behauptungen belegen.
Gibt es die MCP-Werkzeuge `council`/`council_reveal` (lokaler Council-Server), nimm diese und den
Skill `ki-council`. Sonst nutzt du dieses Skript, das überall läuft, wo die APIs erreichbar sind.

## Voraussetzungen

- `GEMINI_API_KEY` und `OPENAI_API_KEY` als Umgebungsvariablen (Cloud: Umgebungseinstellungen;
  lokal: `.env`). Nie nach Schlüsseln im Chat fragen.
- Netzwerk: `generativelanguage.googleapis.com` und `api.openai.com` erlaubt.
- Optional `COUNCIL_GEMINI_MODEL`, `COUNCIL_OPENAI_MODEL`, `COUNCIL_BLOCKLIST`.
- Zuerst `uv run --locked python scripts/council.py status`. Fehlt etwas, sag dem Nutzer genau
  was, und arbeite mit dem verfügbaren Gutachter weiter („nur ein Gutachter“).

## Ablauf

1. **Stufe wählen:** Triviales allein; mittlere Tragweite ein Gutachter (`--reviewers gpt` für
   Code, `gemini` für Text); voller Council bei hoher Tragweite oder ausdrücklichem Wunsch.
2. **Erst selbst denken:** Eigene vollständige Antwort C (bzw. bei Reviews eigene Fehlerliste)
   in eine Datei im Scratchpad schreiben, *bevor* du fragst. Frage und Kontext neutral
   formulieren, ohne deine Lösung (Ausnahme `review`: das Ergebnis ist Prüfgegenstand).
3. **Nur Nötiges senden:** Auszüge statt ganzer Repos. Keine Secrets, keine personenbezogenen
   Daten, keine Firmen-/Kundeninterna. Blockiert das Skript, Stelle schwärzen (`<SECRET>`) bzw.
   Inhalt weglassen — nie umformulieren, um die Sperre zu umgehen.
4. **Fragen:**
   ```bash
   uv run --locked python scripts/council.py ask --mode review \
     --question-file <scratch>/q.md --context-file <scratch>/ctx.md \
     --file pfad/zur/datei.py --own-file <scratch>/own.md --reviewers both
   ```
   `--mode`: `answer` (eigene Lösung), `review` (strenge Kritik), `vote` (Empfehlung + Konfidenz).
   Läuft bis zu 5 Minuten; Bash-Timeout entsprechend setzen.
5. **Blind bewerten:** Tabelle für A, B und C (je 1–5): Korrektheit · Vollständigkeit · Belege ·
   Umsetzbarkeit · Risiken erkannt. Gleicher Maßstab für C. Stärksten Punkt nennen, an dem A oder
   B besser war als C. Selbstauskünfte zur Herkunft ignorieren.
6. **Aufdecken:** Bewertung in Datei schreiben, dann
   `uv run --locked python scripts/council.py reveal --job <id> --rating-file <scratch>/rating.md --contribution <n>`
   (`n` = berechtigte Punkte aus A/B, die in C fehlten; 0 ist gültig). Die erste Bewertung ist endgültig.
7. **Belegen statt zählen:** Die 3–7 tragenden Aussagen selbst prüfen (Tests ausführen, Code lesen,
   Primärquellen). Übereinstimmung ist kein Beweis. Höchstens eine gezielte Rückfrage-Runde
   (`ask --reviewers <einer>` mit der strittigen Aussage und dem Gegenargument).

## Ausgabe an den Nutzer

- **Ergebnis:** beste kombinierte Antwort bzw. verbessertes Ergebnis.
- **Geprüfte Kernaussagen:** belegt / widerlegt / offen, und wie geprüft.
- **Wo die Modelle abweichen** und wie entschieden wurde (Minderheitsmeinungen nennen).
- **Offene Unsicherheiten.**
- **Beitrag der Gutachter:** passend zur Zahl in `--contribution`; ehrlich sagen, wenn nichts Neues kam.
- Fiel ein Gutachter aus: klar „nur ein Gutachter, kein voller Council“ sagen.
