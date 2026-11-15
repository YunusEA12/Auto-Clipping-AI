# Codequalitaet — Pruefung vom 15.11.2026

Vollstaendige Durchsicht mit Blick darauf, ob das Projekt groesser werden kann, ohne an
sich selbst zu ersticken. Zahlenbasis: 16.864 Zeilen Produktivcode in 46 Dateien,
11.888 Zeilen Tests in 70 Dateien, 899 Tests.

## Was schon gut ist

Das sind keine Floskeln, sondern die Punkte, die man in Projekten dieser Groesse fast nie
findet:

- **Test-Verhaeltnis 0,70** (Tests zu Produktivcode). Fuer ein Pipeline-Projekt mit viel
  Netzwerk- und Browser-Anteil ist das ungewoehnlich hoch.
- **CI laeuft auf Windows und Ubuntu**, und die GitHub-Actions sind per Commit-SHA gepinnt
  statt per Tag — das schliesst eine ganze Klasse von Lieferketten-Angriffen aus.
- **Atomares Schreiben** aller Zustandsdateien ueber `atomic_io.py`, konsequent durchgezogen.
- **Die Kommentare erklaeren das Warum**, oft mit Datum und belegter Beobachtung
  (`llm_utils.py` zum Quota-Verhalten pro Modell, `upload_manager.py` zum Vorfall mit den
  151 Uploads). Das ist der Unterschied zwischen Code, den ein Nachfolger weiterentwickeln
  kann, und Code, den er neu schreiben muss.
- **Genau ein TODO** im ganzen Projekt.

## Befunde, nach Wirkung sortiert

### 1. `requirements.txt` pinnt nichts — und das hat heute real geschadet

**0 von 22 Paketen** haben eine exakte Version. Der Server installiert, was gerade aktuell
ist. Am 15.11. hat genau das Playwright angehoben, womit der installierte Browser nicht mehr
passte: die Dienste meldeten weiter "active", **jeder Upload waere lautlos gescheitert.**

Verschaerfend: Es gibt **zwei Abhaengigkeitsquellen.** Die CI testet gegen `uv.lock`
(exakt gepinnt), der Server installiert aus `requirements.txt` (ungepinnt). **Die CI prueft
also Versionen, die auf dem Server nie laufen.** Ein gruener Haken sagt nichts ueber die
Produktion.

Dazu ist `requirements.txt` bereits veraltet: `streamlit-autorefresh` wurde aus
`pyproject.toml` entfernt, steht dort aber noch — der Server installiert ein Paket, das das
Projekt nicht mehr benutzt. Und `pytest` steht in der Produktionsdatei.

**Empfehlung:** `requirements.txt` abschaffen und auf dem Server ebenfalls
`uv sync --locked` verwenden. Eine Quelle, exakt gepinnt, identisch zur CI. Das ist der
Eingriff mit dem besten Verhaeltnis von Aufwand zu Wirkung.

### 2. 38 Module flach im Wurzelverzeichnis

Alles liegt nebeneinander: `process.py`, `analyze.py`, `app.py`, `vision.py`,
`upload_manager.py`, dazwischen 19 Zustands-JSONs. Es gibt keine Paketstruktur.

Heute funktioniert das. Es ist aber die eigentliche Bremse fuer Wachstum: Wer neu dazukommt,
sieht 38 gleichrangige Dateien und kann die Phasen der Pipeline nicht erkennen, obwohl
`claude.md` sie klar in drei Phasen beschreibt. Diese Struktur existiert nur in der
Dokumentation, nicht im Dateisystem.

**Empfehlung** (schrittweise, nicht in einem Zug):

    src/clipping/
        capture/     stream_watcher, ingest
        analyze/     transcribe, analyze, vision, llm_utils
        render/      process, auto_qc
        publish/     upload_manager, tiktok_uploader, upload_ledger, ...
        control/     orchestrator, process_supervisor, auto_pilot
        dashboard/   app, dashboard_api
    state/           die 19 Zustands-JSONs, heute im Wurzelverzeichnis

Wichtig: **nicht jetzt.** Erst sollte der Account-Wechsel zwei Wochen stabil laufen. Ein
Umbau dieser Groesse waehrend einer laufenden Umstellung macht jede Fehlersuche doppelt
schwer.

### 3. `app.py` — 1047 Zeilen, null Tests

Das Dashboard ist das drittgroesste Modul und das einzige grosse voellig ohne Test. Kein
einziger Test importiert es. `dashboard_api.py` (347 Zeilen) ist dagegen getestet — die
Trennung aus `claude.md` ist also eingehalten, aber nur die eine Haelfte ist abgesichert.

Ebenfalls ungetestet: `notify.py`.

### 4. Zu grosse Funktionen

| Zeilen | Ort | Funktion |
|---|---|---|
| 262 | `process.py:1126` | `process_clips_iter` |
| 216 | `process.py:890` | `resolve_layout` |
| 201 | `upload_instagram_playwright.py:425` | `upload_video` |
| 192 | `auto_pilot.py:745` | `run_cycle` |
| 173 | `tiktok_uploader.py:733` | `upload_video` |

Eine Funktion mit 262 Zeilen laesst sich nicht als Ganzes im Kopf behalten und nur schwer
gezielt testen. Das sind die Stellen, an denen kuenftige Fehler entstehen werden.

(Ehrlichkeitshalber: `scripts/setup_tiktok_profile.py:run` ist mit 178 Zeilen am selben Tag
von mir dazugekommen und gehoert auf dieselbe Liste.)

### 5. Playwright-Logik vierfach dupliziert

`chromium.launch` steht an **sieben** Stellen, und die Logik zum Wegklicken von Overlays
(Cookie-Banner, Onboarding-Tooltips) ist in vier Dateien eigenstaendig implementiert:
`tiktok_uploader.py`, `upload_instagram_playwright.py`, `scripts/login_instagram.py`,
`scripts/setup_tiktok_profile.py`.

`claude.md` verlangt ausdruecklich, dass Overlays "aggressiv" weggeklickt werden. Eine
Regel, die an vier Stellen getrennt gepflegt wird, gilt in der Praxis bald nur noch an
dreien. Das gehoert in ein gemeinsames `browser_session.py` (Browserstart, Cookies,
Overlay-Behandlung, Captcha-Erkennung).

### 6. Keine Lint-Konfiguration

Kein `ruff`, kein `black`, kein `mypy`. Bei einem Projekt dieser Groesse mit einem
Hauptentwickler plus wechselnden KI-Agenten ist ein Formatierer die billigste Art, Stil-
Diskussionen und Fluechtigkeitsfehler aus dem Weg zu raeumen.

**Empfehlung:** `ruff` als Linter und Formatierer in die CI, zunaechst nur mit den
Standardregeln.

## Reihenfolge

1. **Jetzt:** `requirements.txt` durch `uv sync --locked` ersetzen (Befund 1).
2. **Jetzt:** `ruff` in die CI (Befund 6) — billig, sofort wirksam.
3. **Nach zwei stabilen Wochen:** gemeinsames `browser_session.py` (Befund 5), danach die
   grossen Funktionen aufteilen (Befund 4) und `app.py` testen (Befund 3).
4. **Wenn Ruhe herrscht:** die Paketstruktur (Befund 2).
