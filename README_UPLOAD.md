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

## So exportierst du deine Cookies

1. Logge dich in einem normalen Browser (Chrome, Firefox, Edge) unter **tiktok.com** mit dem
   Account ein, von dem aus hochgeladen werden soll.
2. Installiere eine Cookie-Export-Erweiterung, z. B. **"Cookie-Editor"** (verfügbar für Chrome
   und Firefox) — oder ein vergleichbares Tool, das Cookies als JSON exportieren kann.
3. Öffne auf **tiktok.com** die Erweiterung und exportiere alle Cookies dieser Seite als JSON
   (meist ein Button "Export" → "JSON" oder "Export as JSON").
4. Speichere den kopierten Inhalt als Datei `cookies.json` im Projekt-Hauptverzeichnis (auf
   derselben Ebene wie `app.py`, `auto_pilot.py`, etc.).

## Erwartetes Format

Eine JSON-Liste von Cookie-Objekten, wie sie die meisten Cookie-Export-Erweiterungen direkt
liefern:

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
