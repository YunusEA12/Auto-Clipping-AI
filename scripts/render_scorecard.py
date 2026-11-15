"""Render the reviewed 20-criterion scorecard; scores are human judgments, not test percentages."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(document: dict) -> str:
    criteria = document["criteria"]
    if len(criteria) != 20 or len({row["id"] for row in criteria}) != 20:
        raise ValueError("Exactly twenty distinct criteria are required")
    if any(type(row[key]) is not int or not 0 <= row[key] <= 100 for row in criteria for key in ("before", "after")):
        raise ValueError("Scores must be integers from 0 to 100")
    before = sum(row["before"] for row in criteria) / 20
    after = sum(row["after"] for row in criteria) / 20
    lines = [
        "# Projektbewertung — 20 Kriterien",
        "",
        f"Stand: {document['date']} · Ausgangsstand: `{document['baseline_commit']}`.",
        "",
        f"Gleich gewichteter Mittelwert: **{before:.1f} → {after:.1f} / 100**.",
        "",
        "Die Punkte sind begründete Review-Urteile. Sie sind weder Code-Coverage noch Erfolgswahrscheinlichkeit. "
        "100 Punkte erfordern vollständige, wiederholbare Nachweise einschließlich Dauerbetrieb und externer Ergebnisse. "
        "Vorbereitete Arbeit wird nicht als bestätigte Live-Änderung gezählt.",
        "",
        "| # | Kriterium | Vorher | Nachher | Nachweis / Verbesserung | Was für 100 noch fehlt |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in criteria:
        lines.append(
            f"| {row['id']} | {row['criterion']} | {row['before']} | {row['after']} | {row['evidence']} | {row['remaining']} |"
        )
    lines += [
        "",
        "## Nachweise und Grenzen",
        "",
        "- [Architektur](../architecture.md), [Betrieb](../operations.md), [Entwicklung](../../CONTRIBUTING.md).",
        "- [Prüfungen und Produktionsnachweise vom 16.11.2026](validation-2026-11-16.md).",
        "- Alle Änderungen werden mit isolierten Tests und echtem CPU-Medienrender geprüft; Betriebsergebnisse stehen im zugehörigen Wiki-Eintrag.",
        "- Die gemessene Coverage umfasst das Paket `autoclip` und bleibt ausdrücklich unter 100 Prozent.",
        "- KI-Clipqualität, Langzeitverfügbarkeit und Produktwirkung werden ohne neue Daten nicht künstlich höher bewertet.",
        "- Profilbild und Bio sind vorbereitet; eine Captcha-Sperre wird nicht als erfolgreicher Profilwechsel ausgegeben.",
        "- Bereits veröffentlichte Videos und bestehende Qualitätsgrenzen werden für eine höhere Punktzahl nicht manipuliert.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    data = json.loads((ROOT / "docs/quality/scorecard.json").read_text(encoding="utf-8"))
    target = ROOT / "docs/quality/scorecard.md"
    target.write_text(render(data), encoding="utf-8")
    print("Rendered twenty criteria:", sum(row["after"] for row in data["criteria"]) / 20)
