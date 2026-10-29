# AGENT_REPORT – Aufgabe 2: textplatzierung

**Aufgabe:** Titel/Untertitel nie über dem Facecam-Gesicht und nie in TikTok-UI-Zonen; Position je Layout; auto_qc bleibt gültig.

**Geänderte Dateien**
- `text_placement.py` (neu): reine Geometrie. `face_rects()` berechnet aus der (gepolsterten) Facecam-Box, dem Padding-Faktor und dem Cover-Scaling das Gesichtsrechteck auf dem 1080x1920-Canvas (split_screen, full_cam, multi_cam; sonst leer). `compute_text_placement()` sucht die nächste freie Position für Titel und Untertitel (Titel zuerst, Untertitel meidet auch den Titel). `subtitle_column()` hält Untertitel links der TikTok-Button-Spalte (x ≤ 90 % der Breite).
- `process.py`: `build_ass_for_clip(..., facecam_box=None, multi_cam_boxes=None)`; Untertitel-Anker x/MarginR aus der Spalte; Titel bleibt mittig. split_screen: Titel direkt unter der Facecam-Naht statt in der Facecam-Zone. `process_clips_iter` übergibt die Boxen und die Gesichtsrechtecke an auto_qc.
- `auto_qc.py`: `check_subtitles(path, face_rects=None)` / `check_render(..., face_rects=None)` prüfen zusätzlich rechte Button-Spalte und Gesichtsüberdeckung.
- `tests/test_text_placement.py` (neu, 8 Tests, echte `build_ass_for_clip()`-Ausgabe + `check_subtitles`), `tests/test_auto_qc.py` (Position der Testmanipulation an neuen Anker angepasst).

**Tests:** `uv run --locked pytest -q` → 791 passed (Baseline 783).

**Risiken / Prüfhinweise**
- Gesichtsposition ist eine Schätzung (Gesicht mittig in der gepolsterten Box, +25 % Sicherheitszuschlag); nicht an echtem Render geprüft. Bitte mit einem echten Clip visuell kontrollieren.
- Ohne Boxen (z. B. center_crop/blur_background oder alte Aufrufer) gelten unverändert die alten Ratios; Untertitel sind aber generell ~29 px nach links verschoben und schmaler (rechter Rand 10 %).
- Findet sich keine freie Position (z. B. viele multi_cam-Slots), bleibt die bevorzugte Position; QC kann dann den Clip blockieren.
- Bestehende Tests, die `SPLIT_SCREEN_TITLE_Y_RATIO` prüfen, gelten nur im Fall ohne Box.
