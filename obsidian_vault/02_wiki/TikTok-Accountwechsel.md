# TikTok-Accountwechsel — 31.10.2026

## Anlass

Der bisherige Account bekam kaum noch Reichweite. Belegter Hauptverdacht: Volumen.
Laut `upload_manager.py` gingen an einem Tag 151 TikTok-Uploads raus (Abstand nur 120 s,
kein Tageslimit). So viele Posts von einem Account sind ein typisches Spam-Signal.
Ein neuer Account mit demselben Verhalten würde sehr wahrscheinlich genauso enden.

## Code-Änderung

- `TIKTOK_DAILY_UPLOAD_CAP` (Standard **4**): maximal so viele echte TikTok-Upload-Versuche
  pro Kalendertag, für die ganze Fleet. `0` = kein Limit.
- `TIKTOK_MIN_UPLOAD_INTERVAL_SECONDS` Standard von 120 auf **10800** (3 Stunden).
- Zusätzliche Clips bleiben lokal in `output/` und werden an späteren Tagen hochgeladen.
- Beide Werte lassen sich in `.env` überschreiben. Erst nach einigen stabilen Wochen langsam
  erhöhen (z. B. 6, dann 8 pro Tag), nie wieder auf über 100 pro Tag.

## Umzug Schritt für Schritt (auf dem Rechner/Server, wo die Pipeline läuft)

1. **Fleet pausieren** (Dashboard) bzw. Dienste stoppen:
   `sudo systemctl stop auto-clipping-supervisor auto-clipping-quality-audit`.
2. **Code aktualisieren:** `git pull --ff-only`, danach `uv sync --locked --group dev`.
   Auf dem Server: Ein Push aktualisiert den laufenden Dienst *nicht* automatisch.
3. **Neuen Account anlegen** — normal in der Handy-App, nicht per Bot.
   Ein paar Tage wie ein Mensch nutzen: Profilbild, Name, Bio passend zur Nische
   (z. B. deutsche Twitch-Highlights), Videos aus der Nische ansehen und liken.
4. **Alte Session loswerden:** Im Desktop-Browser vom alten Account abmelden,
   mit dem neuen anmelden, Browser schließen.
5. **Neue Cookies exportieren:** `uv run --locked python get_cookies.py`
   (überschreibt `cookies.json`). Auf den Server kopieren, falls dort die Pipeline läuft.
6. **Lokale Account-Zustände zurücksetzen** (nicht in Git, nur lokal/Server):
   - löschen: `upload_pacing_state.json`, `.tiktok_browser_profile/` (falls vorhanden)
   - in einen Ordner `backup_alter_account/` verschieben: `viral_memory.json`,
     `ai_guidelines.txt`, `optimization_state.json`. Die Zahlen stammen von einem
     gedrosselten Account und würden der KI falsche Muster beibringen.
   - **behalten:** `upload_ledger.json`. Er verhindert, dass bereits gepostete Clips
     erneut hochgeladen werden. Alte Clips auf dem neuen Account wären Duplicate Content.
   - optional `output/` leeren, damit der neue Account mit frischen Clips startet.
7. **Session prüfen:** `uv run --locked python verify_tiktok_selectors.py --headless`.
8. **Ersten Upload beobachten:** einmal mit `--headed` (siehe README_UPLOAD.md) und danach
   in der App prüfen, ob der Post wirklich sichtbar ist.
9. **Fleet wieder starten** und in den ersten zwei Wochen täglich die Aufrufe ansehen.

## Monetarisierung — wichtige Grenzen

Stand meines Wissens (bitte in der TikTok-App unter Creator-Tools prüfen):
Das Creator Rewards Program verlangt mindestens 18 Jahre, 10.000 Follower,
100.000 Aufrufe in den letzten 30 Tagen, Videos **über 1 Minute** und **Originalinhalte**.

- Die Pipeline erzeugt Clips ab 15 Sekunden. Kürzere Clips als 1 Minute zählen nicht für
  Rewards.
- Unbearbeitete Clips fremder Streamer gelten als „nicht originell“. Sie werden schlechter
  ausgespielt und sind nicht vergütungsfähig. Erlaubnis der Streamer und erkennbarer
  eigener Mehrwert (Kommentar, Einordnung, Zusammenschnitt) senken dieses Risiko.
- Diese Punkte sind Produktentscheidungen und wurden noch nicht im Code geändert.
