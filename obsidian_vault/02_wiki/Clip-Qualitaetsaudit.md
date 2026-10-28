# Begrenztes Clip-Qualitätsaudit

## Auftrag und Evidenz

Eine Kampagne erfasst die nächsten zehn neuen Render seit einem expliziten UTC-Start.
`clip_quality_audit.capture_rendered()` wird im Autopilot direkt nach jedem fertigen Render,
vor dem bisherigen Critic, Purge und Upload aufgerufen. Der Hook sichert Video und passenden
Transkriptkontext; ein unabhängiger Worker bewertet die Kopie. Fehlgeschlagene Sicherungen
werden protokolliert und verändern keine Publikationsfreigaben.

Kategorien (0–10 oder ausdrücklich nicht prüfbar): Schnitt, Untertiteltext/Timing,
Textposition/Safe-Zone, Bildlayout, Audio, Sinn/Hook/Payoff und Viralitätspotenzial.
Der Worker übergibt das vollständige Video inklusive Ton an Gemini und ergänzt FFprobe,
vollständiges FFmpeg-Decoding und Lautheitsmessungen. Das sind KI-Urteile und technische
Messungen, keine menschliche Vollprüfung und keine Erfolgsgarantie.

## Konsistenz und Begrenzung

- Globale Dateisperre schützt die exakte Auswahlgrenze über parallele Streamer hinweg.
- SHA256 identifiziert den Inhalt unabhängig von Titel, Dateiname und Verschiebung.
- Eigene Kopien bleiben nach Purge/Archivierung erhalten; Quellvideos werden nicht verändert.
- Worker-Leases erlauben Wiederaufnahme nach Absturz; maximal drei Versuche pro Clip.
- API-Aufrufe verwenden das vorhandene Tagesbudget und den Modell-Fallback. Kein Text-only-
  Ersatz für Video- oder Audiobewertung; fehlende Evidenz bleibt als solche sichtbar.
- `quality_audit/` ist vollständig gitignored. Ergebnisse, Videos und Transkripte gehören
  nicht ins öffentliche Repository.

## Zwölf-Stunden-Check

`--watch` aktualisiert den Bericht und speichert den ersten fälligen Check unveränderlich.
Er nennt auch eine unvollständige Stichprobe. Performance kommt ausschließlich aus eindeutig
zugeordneten, zeitlich passenden echten Metriken. Historische Clips mit gleichem Titel dürfen
keine vermeintliche Performance für einen gerade erzeugten Clip liefern. Watchtime, Completion,
Retention, Shares und Saves sind im bestehenden Scraper nicht erfasst und werden nicht erfunden.
Der bislang bestehende Critic behandelt reale Viral-Signale erst ab 24 Stunden als belastbarer;
ein früher Check ist deshalb als Zwischenstand zu lesen.

## Befunde der zusätzlichen Prüfung

- `build_ass_for_clip` schrieb vormals `temp/clip_<index>.ass` für alle Streamer.
  Jetzt erhält jeder Aufruf eine eigene Datei; sie wird nach dem Rendern entfernt.
  Ein Paralleltest prüft unabhängige Inhalte bei identischem Clipindex.
- Finales Rendering läuft prozessübergreifend unter einer Dateisperre, damit mehrere
  Streamer nicht gleichzeitig die CPU/iGPU durch finale Encodes überlasten.
- Whisper beanspruchte pro Streamer sämtliche CPU-Kerne. Standard sind jetzt zwei Threads
  je Prozess; `WHISPER_CPU_THREADS` erlaubt eine an die Hardware begrenzte Anpassung.
- Die zusätzliche Sichtprüfung der ersten Proben zeigt, dass automatische hohe Noten
  Layoutfehler übersehen können. Die Prüfanweisung nennt Facecam-Fragmente im Gameplay
  ausdrücklich und verbietet pauschales Verschieben der Untertitel nach unten.
  Menschliche Sichtnotizen liegen privat unter `quality_audit/manual_review.md`.

## Betrieb

Lokaler Start: `uv run --locked python clip_quality_audit.py --init --limit 10
--check-after-hours 12`, danach `./start.ps1 audit`. Bericht: `quality_audit/report.md`.
Eine bestehende Kampagne wird nicht still zurückgesetzt. Der zusätzliche Bericht ist
eine Stichprobe und ersetzt den bisherigen Critic nicht.

Für Betrieb bei ausgeschaltetem PC müssen Capture-Hook, Worker und Metrikerfassung auf
dem Server installiert sein. `deploy/auto-clipping-quality-audit.service` und
`SETUP_SERVER.md` bereiten das vor; ein Git-Push allein ist kein Server-Deployment.
Bei einem Wechsel zwischen Hosts keine zwei unabhängigen Upload-Fleets gleichzeitig starten.
