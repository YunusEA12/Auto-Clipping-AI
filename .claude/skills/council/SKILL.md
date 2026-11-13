---
name: council
description: Kostenloser KI-Council aus Claude, Gemini und GPT, der auch in Cloud-Sitzungen läuft. Nutzen bei "/council", "Council", "Gremium", "zweite Meinung", "Kreuzprüfung", "Cross-Review", "Konsens", "lass GPT/Gemini drüberschauen" und bei wichtigen Entscheidungen, Architektur, sicherheitsrelevantem Code oder Projektbewertungen.
---

# Council (kostenlos, Cloud-tauglich)

Claude ist Orchestrator und verantwortet das Ergebnis. Gemini und GPT sind unabhängige Gutachter.
Ziel: mehr Fehler finden, blinde Flecken aufdecken, Behauptungen belegen.
Gibt es die MCP-Werkzeuge `council`/`council_reveal` (lokaler ki-council-Server), nimm diese und
den Skill `ki-council`. Sonst nutzt du `scripts/council.py`.

## Kosten: nichts

- **Gemini** automatisch über den Gratis-Tarif von `GEMINI_API_KEY` (kostenlos, solange im
  Google-Projekt des Schlüssels keine Abrechnung aktiviert ist; Tageskontingent pro Modell).
- **GPT** über die **ChatGPT-App**: `ask` schreibt eine fertige Prompt-Datei. Inhalt dem Nutzer in
  einem Codeblock zum Kopieren geben, Antwort zurückkopieren lassen.
- Es gibt bewusst keinen Weg über kostenpflichtige APIs. Nie einen einbauen oder vorschlagen,
  ohne dass der Nutzer ausdrücklich Kosten akzeptiert.

## Voraussetzungen

- `GEMINI_API_KEY` als Umgebungsvariable (Cloud: Umgebungseinstellungen; lokal: `.env`).
  Nie nach Schlüsseln im Chat fragen.
- Zuerst `uv run --locked python scripts/council.py status`. Fehlt der Schlüssel oder fällt Gemini
  aus, klar sagen und mit dem ChatGPT-Gutachten allein weitermachen („kein voller Council“).

## Ablauf

1. **Stufe wählen:** Triviales allein; mittlere Tragweite nur Gemini; voller Council (Gemini +
   ChatGPT-App) bei hoher Tragweite oder ausdrücklichem Wunsch.
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
     --file pfad/zur/datei.py --own-file <scratch>/own.md
   ```
   `--mode`: `answer` (eigene Lösung), `review` (strenge Kritik), `vote` (Empfehlung + Konfidenz).
   Danach den ChatGPT-Prompt (Pfad steht in der Ausgabe) an den Nutzer geben und auf die Antwort warten.
5. **Bewerten:** Tabelle für Gemini, ChatGPT und C (je 1–5): Korrektheit · Vollständigkeit ·
   Belege · Umsetzbarkeit · Risiken erkannt. Gleicher Maßstab für C. Stärksten Punkt nennen, an
   dem ein Gutachter besser war als C. (Blind ist das hier nicht – um so strenger mit C sein.)
6. **Festhalten:** Bewertung in Datei schreiben, dann
   `uv run --locked python scripts/council.py rate --job <id> --rating-file <scratch>/rating.md --contribution <n>`
   (`n` = berechtigte Punkte aus den Gutachten, die in C fehlten; 0 ist gültig). Die erste Bewertung ist endgültig.
7. **Belegen statt zählen:** Die 3–7 tragenden Aussagen selbst prüfen (Tests ausführen, Code lesen,
   Primärquellen). Übereinstimmung ist kein Beweis. Höchstens eine gezielte Rückfrage-Runde.

## Ausgabe an den Nutzer

- **Ergebnis:** beste kombinierte Antwort bzw. verbessertes Ergebnis.
- **Geprüfte Kernaussagen:** belegt / widerlegt / offen, und wie geprüft.
- **Wo die Modelle abweichen** und wie entschieden wurde (Minderheitsmeinungen nennen).
- **Offene Unsicherheiten.**
- **Beitrag der Gutachter:** passend zu `--contribution`; ehrlich sagen, wenn nichts Neues kam.
