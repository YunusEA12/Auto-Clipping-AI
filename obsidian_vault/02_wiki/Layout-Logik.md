# Layout-Logik

Regeln zur Erzeugung des finalen Video-Layouts.

## Standard-Layout

- **Format:** 9:16 vertikal (1080×1920).
- **Split-Screen:** Facecam wird dynamisch via MediaPipe erkannt, gepolstert und in der oberen Hälfte platziert; Gameplay/Hauptinhalt wird auf die untere Hälfte zugeschnitten.
- **Untertitel:** `.ass`-Format, wortweise Hervorhebung, hart einkodiert via FFmpeg.
- **Framerate:** folgt der Quelle (üblich 30/60 fps).

## Erweiterte Szenarien

- Adaptive Layouts für Multi-Cam-Collab-Grids, Reaction- und Screen-Share-Szenen sowie "busy scenes" (siehe Commit-Historie).

## Verwandte Module

- [[System-Architektur]]
- [[Streamer-Settings]]
