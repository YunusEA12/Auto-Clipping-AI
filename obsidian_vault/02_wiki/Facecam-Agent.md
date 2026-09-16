# Facecam-Agent

Architektur-Entscheidung, 2026-08-25. Wie das System die **echte Webcam** eines Streamers von
Gesichtern unterscheidet, die **das Spiel selbst zeichnet**.

## Der Vorfall

Zwei EA-FC-Pack-Opening-Clips gingen live, bei denen die obere Facecam-Zone des Split-Screens
einen herangezoomten Ausschnitt einer **Spielerkarte** zeigte statt des Streamers — also
denselben Inhalt wie die Gameplay-Zone darunter, nur vergrößert. Genau der "hässliche" Look,
der den Anstoß für diese Seite gab.

## Warum keine der bestehenden Prüfungen das gefunden hat

Das Portrait auf einer EA-FC-Karte **ist** ein echtes Foto eines echten Gesichts. MediaPipe
bewertet es entsprechend — und zwar oft mit höherer Konfidenz als eine kleine, körnige
Eck-Webcam. Damit passiert es jede vorhandene Hürde völlig zu Recht:

| Prüfung | Warum sie nicht greift |
|---|---|
| `is_multi_cam_scene` | Nur *ein* Gesicht — kein Grid |
| `looks_like_busy_multi_face_scene` | Braucht 2+ Detektionen |
| `box_is_mostly_black` | Die Karte ist hellgold, nicht schwarz |
| `face_count > MULTI_CAM_MAX_FACES` | Weit darunter |
| `_sample_smoothed_box` | Misst nur **innerhalb eines Clips** — und während einer Walkout-Animation steht die Karte still, der Median ist also stabil |

Alle bisherigen Signale beantworten *"ist da ein Gesicht, und wo?"*. Keines beantwortet
**"ist dieses Gesicht die Kamera des Streamers oder etwas, das das Spiel malt?"**

## Das unterscheidende Signal: Persistenz

Eine Webcam ist **festes Inventar der gesamten Übertragung** — dasselbe Rechteck, Minute um
Minute, über Menüs, Matches und Pack-Openings hinweg. Spielinhalt ist **flüchtig**: eine Karte
füllt für ein paar Sekunden den Bildschirm und ist wieder weg.

Deshalb scannt `vision.locate_persistent_facecam()` **den gesamten Aufnahme-Chunk**, nicht einen
Clip — denn ein einzelner Clip *kann komplett* aus einer Pack-Opening-Sequenz bestehen, und
genau das ist der Fehler, der ein Kartengesicht wie eine stabile Facecam aussehen lässt.

Ablauf:

1. `FACECAM_SCAN_SAMPLE_COUNT` (16) Frames gleichmäßig über den Chunk verteilt abtasten
   (erste/letzte 5 % ausgespart — Übergänge, Schwarzbilder, angeschnittene GOPs).
2. Alle Detektionen nach **Position** clustern (`_boxes_are_same_position`: Mittelpunkt-Abstand
   + Größenähnlichkeit — Mittelpunkte, weil eine Box beim Vor-/Zurücklehnen wächst und
   schrumpft, die Position aber bleibt).
3. Pro Cluster die **Persistenz** = Anteil der Frames, in denen er vorkommt.
4. Gewinner ist der **persistenteste** Cluster — Konfidenz entscheidet nur bei Gleichstand.
   Bewusst nicht konfidenzgetrieben: ein großes scharfes Kartenportrait *überbietet* die kleine
   Eck-Cam in einem einzelnen Frame, genau das ist ja der Bug.
5. Unter `FACECAM_MIN_PERSISTENCE` (0,45) → **diese Aufnahme hat keine Webcam**.

## Zwei Wirkungen

- **Kamera vorhanden** → das Ergebnis ist die maßgebliche Crop-Box (`verified_box_ratio` in
  `vision.get_facecam_coordinates()`) und **schlägt die Einzelbild-Erkennung**. Das behebt auch
  die schwächere Variante des Bugs, bei der die echte Eck-Cam zwar sichtbar ist, in manchen
  Frames aber vom Kartengesicht überboten wird.
- **Keine Kamera** → `process.resolve_layout()` **vetot Split-Screen** und nimmt stattdessen
  `center_crop`. Kein Spielinhalt landet mehr in der Facecam-Zone.

## Bewusste Grenzen

- **Nur Split-Screen wird vetot, nicht Full-Cam.** Ein echtes Talking-Head-Motiv bewegt sich
  deutlich mehr als eine fixe Eck-Cam und ist damit der Fall, der die Persistenzschwelle am
  ehesten *berechtigt* verfehlt. Ein Veto dort würde echte Talking-Head-Clips herabstufen, um
  einen Fehler zu beheben, der dort nie beobachtet wurde.
- **Fehlgeschlagener Scan ≠ keine Kamera.** Eine unlesbare Datei ist kein Beleg. `FacecamScan.
  scan_succeeded` (`samples_read == 0`) trennt die Fälle; bei einem fehlgeschlagenen Scan
  bleibt alles beim Alten (fail-open), wie bei jedem anderen Layout-Signal in diesem Modul.
- **Ein Scan pro Chunk, nicht pro Clip** — das Ergebnis ist eine Eigenschaft der Aufnahme.
  `_scan_frames_for_faces()` teilt sich dafür *eine* `VideoCapture` und *einen* `FaceDetector`
  über alle Samples (Modell-Ladezeit dominiert sonst).

## Verhältnis zum statischen Override

`streamers.json` kennt weiterhin ein handkonfiguriertes `facecam_box` pro Streamer. Der Agent
liefert dieselbe Art Box, nur **automatisch aus dem Material abgeleitet**. Rangfolge in
`get_facecam_coordinates()`:

1. `verified_box_ratio` (Agent, chunkweit belegt)
2. dynamische Einzelclip-Erkennung ab `DYNAMIC_DETECTION_MIN_CONFIDENCE`
3. `override_box_ratio` (statisch aus `streamers.json`)
4. `_fallback_box()` (Eck-Quadrant)

## Offen / zu verifizieren

Gegen **echtes** EA-FC-Material auf dem Server noch nicht gegengeprüft — die Unit-Tests decken
die Logik ab, nicht die real gemessenen Persistenzwerte. `FACECAM_MIN_PERSISTENCE` und
`FACECAM_CLUSTER_CENTER_TOLERANCE` sind begründete Startwerte, keine gemessenen. Nach dem
Deploy die `Facecam scan of ...`-Logzeilen prüfen und die Schwellen ggf. nachziehen.

## Verwandte Module

- [[Layout-Logik]]
- [[System-Architektur]]
- [[Streamer-Settings]]
