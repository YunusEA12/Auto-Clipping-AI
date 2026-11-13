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
6. **Lokale Account-Zustände zurücksetzen** — seit 13.11.2026 automatisiert:
   `python scripts/switch_tiktok_account.py --dry-run` zeigt erst, was passiert,
   ohne `--dry-run` wird es ausgeführt. Das Script weigert sich zu laufen, solange noch
   Pipeline-Prozesse aktiv sind (ein laufender `auto_pilot.py` würde die archivierten
   Dateien sofort neu schreiben), und rührt keine git-getrackte Datei an. `--clear-output`
   parkt zusätzlich die gerenderten Clips. Was es im Einzelnen tut:
   - löschen: `upload_pacing_state.json`, `.tiktok_browser_profile/` (falls vorhanden)
   - in einen Ordner `backup_alter_account/` verschieben: `viral_memory.json`,
     `ai_guidelines.txt`, `optimization_state.json`. Die Zahlen stammen von einem
     gedrosselten Account und würden der KI falsche Muster beibringen.
   - **behalten:** `upload_ledger.json`. Er verhindert, dass bereits gepostete Clips
     erneut hochgeladen werden. Alte Clips auf dem neuen Account wären Duplicate Content.
   - optional `output/` leeren, damit der neue Account mit frischen Clips startet (`--clear-output`).
   - **nicht** angefasst wird `top_performers.json`: trotz des Namens ist das eine kuratierte,
     git-getrackte Beispielsammlung von Hooks, kein Lernstand des alten Accounts.
7. **Session prüfen:** `uv run --locked python verify_tiktok_selectors.py --headless`.
8. **Ersten Upload beobachten:** einmal mit `--headed` (siehe README_UPLOAD.md) und danach
   in der App prüfen, ob der Post wirklich sichtbar ist.
9. **Fleet wieder starten** und in den ersten zwei Wochen täglich die Aufrufe ansehen.

## Profil des neuen Accounts (entschieden 13.11.2026)

Bewusst eine eigene Kunstfigur statt eines Clip-Channels, der nach dem Streamer benannt ist:
Ein Profil, das den Namen oder das Gesicht eines Streamers traegt, ist Identitaetsanmassung
und faellt sowohl unter TikToks Impersonation-Regeln als auch unter das Problem
"nicht originell" bei der Monetarisierung.

- Kunstfigur, z. B. "Kartoffel schaut Twitch", Bio in der Art
  "Ich bin eine Kartoffel. Ich schaue Twitch. Die besten Momente landen hier."
- Profilbild: eigenes, lustiges Motiv — kein Streamer-Gesicht, kein fremdes Logo.
- **Privates Konto, kein Business-Konto.** Business-Konten haben eingeschraenkte
  Sound-Bibliothek und werden fuer Clip-Content schlechter ausgespielt.
- 2–3 Tage Warm-up vor dem ersten Post: nur am Handy Nischen-Videos ansehen und liken.
  Ein brandneuer Account, der sofort automatisiert postet, ist selbst ein Spam-Signal.

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

**Entscheidung 13.11.2026:** `MIN_CLIP_DURATION` bleibt vorerst bei 15 Sekunden. Kurze Clips
wachsen schneller, und ohne 10.000 Follower ist die Ein-Minuten-Regel ohnehin ohne Wirkung.
Erst ab etwa 5.000–8.000 Followern schrittweise auf laengere Clips umstellen. Ein eigener
Mehrwert pro Clip (Text-Hook, Einordnung) senkt parallel das "nicht originell"-Risiko.
Ein alter Branch `feature/agent-automated-refactoring` haette den Wert sofort auf 61 gesetzt —
bewusst nicht uebernommen.

## Server-Zugang (Stand 13.11.2026)

Die Pipeline laeuft produktiv auf dem Contabo-VPS `vmd204170`, erreichbar nur ueber Tailscale
unter `root@100.64.239.23`. Der urspruengliche Wartungsschluessel `~/.ssh/id_ed25519` ist mit
einer Passphrase verschluesselt, die nicht mehr bekannt ist; eine Passphrase laesst sich nicht
wiederherstellen. Ersatz ist `~/.ssh/id_ed25519_contabo` (ohne Passphrase, damit die Wartung
aus dem Chat heraus ohne Eingabe laeuft — vertretbar, weil der Server nicht aus dem offenen
Internet erreichbar ist). Eingerichtet wird er mit `quality_audit/connect_server.ps1`, nachdem
das root-Passwort unter my.contabo.com (VPS control -> Manage -> Password reset) neu gesetzt
wurde.
