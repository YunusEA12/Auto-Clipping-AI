# AGENT_REPORT – Aufgabe 1: musik

**Aufgabe:** Musikauswahl passend zum Clip (Stimmungs-Tags, hook_style, Audio-Energie, Titel), epsilon-greedy bleibt.

**Geänderte Dateien**
- `music_matching.py` (neu): lädt `tags.json`, bewertet Track↔Clip (energy_rating, hook_style, Titel-Keywords DE/EN), liefert Gewichte (Minimum 0.1).
- `background_music/tags.json` (neu): Tags für die 3 vorhandenen Tracks.
- `background_music/README.md`: Tag-Format + Liste der noch zu ergänzenden lizenzfreien Track-Typen (nichts heruntergeladen).
- `process.py`: `pick_background_track(clip=None)` – Preferred-Track-Logik unverändert, die zufällige Wahl ist jetzt gewichtet; `process_clips_iter` und `render_clip` übergeben den Clip.
- `tests/test_music_matching.py` (neu, 7 Tests); `tests/test_process_layout_selection.py`: Mock-Signatur angepasst.

**Tests:** `uv run --locked pytest -q` → 790 passed (Baseline 783).

**Risiken / Prüfhinweise**
- Gewichtung ist heuristisch (Keyword-Listen klein); Score-Formel in `score_track` prüfen.
- Mit nur 3 synthetischen Tracks ist der Effekt gering; Wirkung zeigt sich erst mit mehr Tracks.
- Ohne `tags.json` oder ohne Clip: altes Verhalten (gleichverteilt).
