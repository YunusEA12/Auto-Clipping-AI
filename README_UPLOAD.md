# TikTok Auto-Upload — cookies.json einrichten

`tiktok_uploader.py` (und darüber `auto_pilot.py --auto-upload`, `stream_watcher.py --auto-upload`,
sowie der "🚀 Automatisch auf TikTok hochladen"-Schalter in `app.py`) laden Videos über einen
echten Browser (Playwright) hoch, statt über die offizielle TikTok-API — das erlaubt ein
echtes "Veröffentlichen" statt nur eines privaten Inbox-Drafts. Damit das
ohne manuellen Login funktioniert, braucht der Bot deine bereits eingeloggte TikTok-Session als
Cookies in einer Datei namens `cookies.json` im Projekt-Hauptverzeichnis.

## ⚠️ Sicherheit zuerst

`cookies.json` enthält deine echten TikTok-Session-Cookies — wer diese Datei besitzt, kann sich
als du bei TikTok anmelden. Das ist gleichbedeutend mit einem Passwort.

- **Niemals committen.** Die Datei ist bereits in `.gitignore` eingetragen — prüfe trotzdem
  vor jedem `git add .`, dass sie nicht versehentlich mit hochgeladen wird.
- **Niemals teilen** (Screenshots, Chat, Support-Tickets, etc.).
- Cookies laufen irgendwann ab oder werden bei einem TikTok-Logout ungültig — exportiere in
  dem Fall einfach eine neue `cookies.json`.
- Das Automatisieren der TikTok-Web-Oberfläche (statt der offiziellen API) liegt außerhalb des
  offiziell unterstützten Wegs und kann gegen die Nutzungsbedingungen von TikTok verstoßen.
  Das ist deine eigene Abwägung für deinen eigenen Account.

## So bekommst du deine Cookies (empfohlen: get_cookies.py)

Voraussetzung: Du bist in einem deiner normalen Browser (Chrome, Edge oder Firefox) bereits
bei tiktok.com eingeloggt — ganz gewöhnlich, wie jeder andere Login auch.

```
python get_cookies.py
```

Das Skript liest deine TikTok-Session-Cookies direkt aus der lokalen Cookie-Datenbank deines
Browsers (via `browser-cookie3`) — es öffnet keinen Browser, automatisiert keinen Login und
rührt das TikTok-Login-Formular überhaupt nicht an. Es probiert der Reihe nach Chrome, Edge
und Firefox durch, bis eines eine gültige `sessionid` liefert, und speichert das Ergebnis
direkt im korrekten Format als `cookies.json`.

**Wichtig:** Schließe den jeweiligen Browser (oder zumindest den TikTok-Tab) vorher, da
Chrome/Edge ihre Cookie-Datenbank sperren, solange sie laufen — das kann die Extraktion
verhindern oder veraltete Daten liefern. Einen bestimmten Browser erzwingst du mit
`python get_cookies.py --browser edge` (oder `chrome`/`firefox`).

Diese Methode wurde bewusst gegenüber einem interaktiven Playwright-Login gewählt: Ein
frischer, unauthentifizierter Playwright-Browser, der direkt das TikTok-Login-Formular
ansteuert, hat in der Praxis TikToks Bot-/Rate-Limit-Schutz ausgelöst. Das Auslesen der
bereits bestehenden, ganz normal erzeugten Session umgeht dieses Problem vollständig.

### Alternative: manueller Login (wenn get_cookies.py scheitert)

`get_cookies.py`s Shadow-Copy-Fallback (für einen bereits geöffneten Browser) braucht unter
Windows Admin-Rechte — schlägt das fehl (oder ist kein unterstützter Browser installiert),
kannst du stattdessen `verify_tiktok_selectors.py --manual-login` nutzen: Es öffnet ein
sichtbares Browser-Fenster, in dem **du selbst** ganz normal auf tiktok.com einloggst
(inklusive Captcha, falls eins erscheint) — das Skript liest oder tippt dabei keine
Zugangsdaten, es wartet nur, bis du im Terminal Enter drückst. Danach fragt es, ob es die
Session-Cookies dieses Logins in `cookies.json` speichern soll — damit hast du in einem
Rutsch sowohl eine gültige `cookies.json` für `tiktok_uploader.py` als auch die
Selektor-Prüfung gegen die echte TikTok-Oberfläche erledigt.

```
python verify_tiktok_selectors.py --manual-login
```

### Alternative: Browser-Erweiterung

Falls du lieber eine Cookie-Export-Erweiterung nutzt (z. B. "Cookie-Editor" für Chrome/Firefox):
logge dich normal auf tiktok.com ein, exportiere die Cookies dieser Seite als JSON, und
speichere sie als `cookies.json` im Projekt-Hauptverzeichnis. Das Format dieser Erweiterungen
weicht leicht von Playwrights nativem Format ab (siehe unten) — `tiktok_uploader.py` gleicht
das automatisch aus.

## Erwartetes Format

`get_cookies.py` erzeugt dieses Format automatisch korrekt. Falls du stattdessen eine
Browser-Erweiterung nutzt, ist eine JSON-Liste von Cookie-Objekten wie folgt erwartet:

```json
[
  {
    "name": "sessionid",
    "value": "dein-echter-session-wert",
    "domain": ".tiktok.com",
    "path": "/",
    "expirationDate": 1799999999.123,
    "httpOnly": true,
    "secure": true,
    "sameSite": "Lax"
  },
  {
    "name": "tt_csrf_token",
    "value": "...",
    "domain": ".tiktok.com",
    "path": "/",
    "expirationDate": 1799999999.123,
    "httpOnly": false,
    "secure": true,
    "sameSite": "Lax"
  }
]
```

Wichtige Felder pro Cookie: `name`, `value`, `domain`, `path`. `expirationDate` wird
automatisch zu Playwrights erwartetem `expires`-Feld umbenannt (`tiktok_uploader._normalize_cookie()`)
— du musst das Format der Export-Erweiterung also nicht manuell anpassen. Enthält die Datei
mehrere Cookies verschiedener Domains, ist das unproblematisch; wichtig ist nur, dass die
`.tiktok.com`-Cookies (insbesondere `sessionid`) enthalten sind.

## Testen

```
python tiktok_uploader.py output/clip_1_beispiel.mp4 --description "Test" --hashtags fyp viral --headed
```

`--headed` zeigt den Browser sichtbar an, damit du den Ablauf einmal live beobachten kannst,
bevor du dich auf den unbeaufsichtigten (`--headed`-losen) Modus in `auto_pilot.py` o.ä.
verlässt.

**Wichtig — Sicherheitsmodell (korrigiert am 2026-08-18):** TikToks aktueller Upload-Flow hat
keinen eigenen "Als Entwurf speichern"-Button mehr, nur "Veröffentlichen" (live) und
"Verwerfen" (löscht den Upload). Eine frühere Version dieses Projekts nahm fälschlicherweise
an, dass ein unangetasteter, nicht veröffentlichter Upload automatisch als privater Entwurf
erhalten bleibt. Das stimmt **nicht** — direkt widerlegt, indem der Account-Besitzer nach
einem echten Test in TikTok Studio und der Handy-App nachgeschaut hat: Der Upload war
nirgendwo zu finden. TikTok verwirft einen nicht veröffentlichten, nicht angeklickten Upload
einfach, statt ihn zu speichern.

Es gibt also keinen sicheren "hochladen, aber nicht veröffentlichen"-Zustand mehr. Ohne
`--publish` rührt der Bot den Browser **überhaupt nicht an** — kein Upload-Versuch, keine
Caption, nichts. Die Datei bleibt exakt dort, wo sie ist. Nur mit `--publish` passiert
tatsächlich etwas: Der Bot öffnet den Browser, lädt hoch, füllt die Caption aus und klickt
aktiv auf "Veröffentlichen" — der Clip geht dann sofort live. Das gilt genauso für
`auto_pilot.py --auto-upload` (braucht zusätzlich `--publish`, sonst wird Phase 5 komplett
übersprungen), `stream_watcher.py --auto-upload` und den "🔴 Sofort live veröffentlichen"-
Schalter in `app.py`.

## Wenn der Login fehlschlägt

Meldet das Skript `"Not logged in (redirected to .../login)"`, sind deine Cookies abgelaufen
oder ungültig — wiederhole den Export-Schritt oben mit einer frischen, aktiven TikTok-Sitzung.

---

# YouTube-Hintergrundmusik — jetzt via ffmpeg beim Rendern (nicht mehr Browser-Automation)

Frühere Version dieses Abschnitts beschrieb `youtube_studio_uploader.py`: eine Playwright-
Automatisierung des YouTube-Studio-Editors per eingeschleusten Google-Session-Cookies
(`youtube_studio_cookies.json`). Verworfen am 2026-08-21 — die Cookies kamen nie über Googles
Login-Seite hinaus (leeres, nicht-wiedererkanntes Login-Formular statt einer akzeptierten
Session, vermutlich IP-Bindung dieser Session-Cookies auf Googles Seite), und jeder weitere
Versuch hätte das Risiko erhöht, den Google-Account wegen automatisierter Session-Nutzung
einzuschränken — was auch `upload.py`s echten, legitimen API-Upload beträfe, da beide am
selben Account hängen.

Hintergrundmusik wird jetzt direkt beim Rendern in `process.py` gemischt (ffmpeg
`amix`/`volume`, siehe `build_audio_filter()`) — kein Browser, keine Google-Session-Cookies,
kein Studio-UI-Risiko. Tracks liegen in `background_music/` (siehe dortige README); die
Pipeline wählt bei jedem Render zufällig einen aus, sofern welche vorhanden sind.

---

# Instagram Reels Auto-Upload — config/instagram_cookies.json einrichten

`upload_instagram_playwright.py` (und darüber `upload_manager.py`, `auto_pilot.py --instagram`)
spiegelt exakt dieselbe Architektur wie `tiktok_uploader.py`: ein echter Browser (Playwright),
authentifiziert per eingeschleuster Session-Cookies, statt der offiziellen Meta Graph API —
deren App-Review-/Business-Verification-Aufwand für dieses Projekt als zu hoch eingestuft wurde
(2026-08-21, explizite Entscheidung des Account-Besitzers).

**⚠️ Wichtiger als bei TikTok:** Meta verfolgt automatisierte Session-Nutzung nachweislich
aggressiver als die meisten Plattformen — dieselbe Abwägung, die bereits die YouTube-Studio-
Automatisierung oben zu Fall gebracht hat (dort kamen die Cookies nicht einmal über Googles
Login-Seite hinaus, vermutlich IP-Bindung). Ob Instagrams Session-Cookies sich genauso
verhalten, ist **ungetestet** — das ist erst nach einem echten `--headed`-Testlauf bekannt, nicht
vorher. `config/instagram_cookies.json` enthält echte Instagram-Session-Cookies — gleichbedeutend
mit einem Passwort für den Account. Niemals committen (bereits in `.gitignore`), niemals teilen.

## So bekommst du deine Cookies

Genau wie `get_cookies.py` für TikTok — kein Playwright-Login-Fenster, keine automatisierte
Anmeldung, nur ein Auslesen deiner bereits bestehenden, ganz normal erzeugten Session:

```
python setup_instagram_cookies.py
```

Voraussetzung: Du bist in Chrome, Edge oder Firefox bereits bei instagram.com eingeloggt.
Schließe den jeweiligen Browser (oder zumindest den Instagram-Tab) vorher. Einen bestimmten
Browser erzwingst du mit `python setup_instagram_cookies.py --browser edge`.

## Testen — zwingend vor dem unbeaufsichtigten Einsatz

Die Selektoren in `upload_instagram_playwright.py` wurden **noch nie gegen eine echte,
laufende Instagram-Session getestet** (siehe UNVERIFIED SELECTORS im Modul-Docstring) — anders
als `tiktok_uploader.py`, dessen Selektoren einmal live verifiziert wurden. Vor jedem
Live-Einsatz zwingend:

```
python upload_instagram_playwright.py <CLIP.mp4> --description "..." --publish --headed
```

Gegen ein echtes (im Idealfall unwichtiges/löschbares) Test-Reel, mit sichtbarem Browserfenster
— beobachte, ob der Upload-Dialog korrekt geöffnet wird, ob die Datei hochlädt, ob die Caption
gefüllt wird und ob "Share" wirklich veröffentlicht. Erwarte, dass Selektoren angepasst werden
müssen, bevor das zuverlässig funktioniert.

## Aktivierung pro Streamer

Instagram ist standardmäßig für **jeden** Streamer deaktiviert, auch wenn `publish: true`
bereits gesetzt ist — ein separates, bewusstes Opt-in in `streamers.json`:

```json
{"name": "beispiel", "url": "...", "auto_upload": true, "publish": true, "instagram": true}
```

Erst nach einem erfolgreichen `--headed`-Testlauf setzen. `orchestrator.py` reicht `--instagram`
nur durch, wenn sowohl `publish` als auch `instagram` gesetzt sind (siehe
`build_auto_pilot_cmd()`).

## Löschschutz

Der Löschschutz in `metrics_tracker.py` (lokale Datei bleibt erhalten, bis alle aktivierten
Plattformen wirklich hochgeladen haben — siehe dessen eigene Kommentare zur ursprünglichen
YouTube-Version dieses Schutzes) gilt jetzt auch für Instagram: eine lokale Datei wird erst
gelöscht, wenn YouTube (falls `publish: true`) UND Instagram (falls `instagram: true`)
tatsächlich hochgeladen haben — nicht nur TikTok.
