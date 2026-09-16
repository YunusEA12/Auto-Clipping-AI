# Layout-Logik

Regeln zur Erzeugung des finalen Video-Layouts.

## Standard-Layout

- **Format:** 9:16 vertikal (1080×1920).
- **Split-Screen:** Facecam wird dynamisch via MediaPipe erkannt, gepolstert und in der oberen Hälfte platziert; Gameplay/Hauptinhalt wird auf die untere Hälfte zugeschnitten.
- **Untertitel:** `.ass`-Format, wortweise Hervorhebung, hart einkodiert via FFmpeg.
- **Framerate:** folgt der Quelle (üblich 30/60 fps).

## Erweiterte Szenarien

- Adaptive Layouts für Multi-Cam-Collab-Grids, Reaction- und Screen-Share-Szenen sowie "busy scenes" (siehe Commit-Historie).
- **Echte Webcam vs. vom Spiel gezeichnete Gesichter:** siehe [[Facecam-Agent]] — ein chunkweiter Persistenz-Scan entscheidet, ob überhaupt eine Kamera im Material ist, und liefert die maßgebliche Crop-Box. Ohne persistente Kamera wird Split-Screen vetot (sonst landet Spielinhalt in der Facecam-Zone).

## Verwandte Module

- [[Facecam-Agent]]
- [[System-Architektur]]
- [[Streamer-Settings]]
