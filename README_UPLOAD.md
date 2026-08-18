# TikTok Auto-Upload — cookies.json einrichten

`tiktok_uploader.py` (und darüber `auto_pilot.py --auto-upload`, `stream_watcher.py --auto-upload`,
sowie der "🚀 Automatisch auf TikTok hochladen"-Schalter in `app.py`) laden Videos über einen
echten Browser (Playwright) hoch, statt über die offizielle TikTok-API — das erlaubt einen
echten "Posten"/"Als Entwurf speichern"-Klick statt nur eines privaten Inbox-Drafts. Damit das
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

Das Projekt bringt ein eigenes Tool mit, das das manuelle Rumkopieren von Cookies aus
Browser-Erweiterungen überflüssig macht:

```
python get_cookies.py
```

Das Skript öffnet einen echten, sichtbaren Chrome-Browser und navigiert zu tiktok.com/login.
**Du loggst dich dort selbst ein** (inkl. Captcha/2FA, falls nötig) — Playwright automatisiert
nur das Öffnen des Fensters, nicht den Login selbst. Danach drückst du im Terminal Enter, und
das Skript speichert die aktuellen Session-Cookies direkt im korrekten Playwright-Format als
`cookies.json`. Kein Format-Mismatch, kein manuelles Anpassen nötig.

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
verlässt. Ohne `--publish` wird der Clip nur als **Entwurf gespeichert** (sicherer Standard) —
erst mit `--publish` geht er sofort live.

## Wenn der Login fehlschlägt

Meldet das Skript `"Not logged in (redirected to .../login)"`, sind deine Cookies abgelaufen
oder ungültig — wiederhole den Export-Schritt oben mit einer frischen, aktiven TikTok-Sitzung.
