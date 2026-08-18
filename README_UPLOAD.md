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
verlässt. Ohne `--publish` (sicherer Standard) klickt der Bot **nichts** — TikToks aktueller
Upload-Flow hat keinen eigenen "Als Entwurf speichern"-Button mehr, nur "Veröffentlichen"
(live) und "Verwerfen" (löscht den Upload). Stattdessen wird die Caption ausgefüllt, dann
schließt der Browser, ohne zu klicken — TikTok behält einen so unangetasteten Upload
automatisch als privaten Entwurf (verifiziert am 2026-08-18). Erst mit `--publish` klickt der
Bot aktiv auf "Veröffentlichen" und der Clip geht sofort live.

## Wenn der Login fehlschlägt

Meldet das Skript `"Not logged in (redirected to .../login)"`, sind deine Cookies abgelaufen
oder ungültig — wiederhole den Export-Schritt oben mit einer frischen, aktiven TikTok-Sitzung.
