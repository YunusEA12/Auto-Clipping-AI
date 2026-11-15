# TikTok-Sitzung und kontrollierte Uploads

`var/secrets/cookies.json` enthält die Sitzung des eigenen TikTok-Accounts. Die Datei und
alle privaten Laufzeitdaten sind von Git und Wheel-Verteilung ausgeschlossen.

## Sitzung erfassen

```powershell
uv run --locked python scripts/capture_tiktok_session.py
```

Im geöffneten Browser selbst anmelden. Das Skript wartet ohne Terminal-Eingabe und speichert
erst, wenn das tatsächliche Upload-Formular erreichbar ist. Ein Captcha wird vom Menschen
gelöst. Neuere Chrome-/Edge-Versionen können das direkte Auslesen ihrer Cookie-Datenbank
verhindern; `autoclip cookies` ist deshalb nur eine alternative Extraktionsmethode.

Bei Serverbetrieb die Sitzung privat auf denselben Pfad des VPS übertragen. Die Datei muss
dem Dienstbenutzer gehören und unter POSIX nur für diesen lesbar sein. Bestehende gültige
Cookies niemals durch unbestätigte oder leere Exporte ersetzen.

## Ohne Veröffentlichung prüfen

```powershell
uv run --locked autoclip verify-session --headless --skip-content-page
```

Die Diagnose veröffentlicht nichts und wählt keine Datei aus. Sie erkennt verzögerte
Login-Weiterleitungen und liefert bei fehlendem Upload-Formular Exitcode 1. Caption-,
Editor- und Sound-Selektoren existieren teilweise erst nach einer Videoauswahl.
Auf dem VPS muss `PLAYWRIGHT_BROWSERS_PATH` dem Wert der systemd-Unit entsprechen.

## Einzelnen geprüften Clip veröffentlichen

```powershell
uv run --locked autoclip upload-tiktok var/output/creator/clip.mp4 --description "Ein Highlight" --publish --headed
```

Auch dieser CLI-Weg verwendet QC, Ledger und globale Limits. `--headed` dient der sichtbaren
Kontrolle; `--no-music` lässt die zusätzliche TikTok-Soundauswahl aus. Ohne `--publish`
geschieht nichts. Das Budget nicht zurücksetzen, um eine abgewiesene oder unklare
Veröffentlichung sofort zu wiederholen.

Für den regulären Betrieb `auto_upload: true` und `publish: true` je Streamer konfigurieren
und den Supervisor nutzen. Der separate Worker prüft den Backlog auch ohne laufenden Stream.
Ein bestätigter Post wird archiviert; unbestätigte Klicks bleiben gegen Duplikate geschützt.

## Profil

Das freigegebene Material steht unter `assets/branding/`. Vorbereitung und Übernahme:

```powershell
uv run --locked python scripts/generate_profile_assets.py --force
uv run --locked python scripts/setup_tiktok_profile.py --headed
```

Der Zielaccount ist im Profilmanifest festgelegt. Der vollständige Biotext und das Avatarbild
werden nach dem Speichern erneut geprüft. Ein vorhandenes CDN-Bild oder ein HTTP-200 allein
zählt nicht als Nachweis. TikTok kann eine menschliche Captcha-Prüfung verlangen.

YouTube und Instagram bleiben im normalen TikTok-Betrieb deaktiviert. Ihre separat
implementierten Integrationen sind keine Freigabe für zusätzliche automatische Posts.
