# AutoClip · ClipMaster

CPU-orientierte Pipeline für deutsche Stream-Highlights: aufnehmen, transkribieren,
Szenen auswählen, vertikal rendern, technisch prüfen und kontrolliert auf TikTok veröffentlichen.
Der produktive Dauerbetrieb läuft auf dem Linux-VPS; der Windows-PC ist Entwicklungsumgebung.
YouTube und Instagram sind zusätzliche Integrationen und standardmäßig deaktiviert.

## Aufbau

```text
src/autoclip/       installierbares Python-Paket mit elf Fachbereichen
assets/branding/   freigegebenes ClipMaster-Profilbild und Profiltext
config/            lokale Streamer-Konfiguration und editierbare Profile
deploy/            systemd und reproduzierbarer Server-Update-Ablauf
docs/              Architektur, Betrieb und Qualitätsbewertung
tests/             isolierte Tests einschließlich UI und echtem FFmpeg-Render
var/               private Zustände, Zugangsdaten, Medien und Prüfberichte
```

[Architektur](docs/architecture.md) · [Entwicklung](CONTRIBUTING.md) ·
[Serverbetrieb](docs/operations.md) · [20 Bewertungskriterien](docs/quality/scorecard.md)

## Lokal starten

Python 3.12, `uv`, FFmpeg und FFprobe müssen installiert sein. Keine CUDA-GPU erforderlich.

```powershell
uv sync --locked --group dev
uv run --locked playwright install chromium
Copy-Item .env.example .env                # nur bei einer neuen Installation
Copy-Item config/streamers.example.json config/streamers.json
./start.ps1 check
./start.ps1                               # Dashboard: http://127.0.0.1:8501
```

API-Schlüssel ausschließlich in `.env` eintragen. Die Beispielkonfiguration veröffentlicht
nichts. `./start.ps1 supervisor` startet die Verarbeitung separat vom Dashboard. Eine
vorhandene Fleet-Pause bleibt nach Neustarts erhalten.

Für ein bestehendes Checkout vor dem ersten Start von Version 0.2 alle lokalen Dienste
stoppen und die Laufzeitdaten migrieren:

```powershell
uv run --locked autoclip migrate-runtime           # Vorschau
uv run --locked autoclip migrate-runtime --apply   # Kollisionen werden nicht überschrieben
```

Die Migration erhält Ledger, Tageszähler und Cookies, legt ein Manifest an und übersetzt
gespeicherte Medienpfade. Neue Pfade stehen in [docs/operations.md](docs/operations.md).

## Dienste und Veröffentlichung

```powershell
uv run --locked autoclip --help
uv run --locked autoclip supervisor
uv run --locked autoclip uploads --once            # ohne --publish keine Posts
uv run --locked autoclip qc-summary --limit 50
uv run --locked autoclip audit --watch
```

Der Supervisor überwacht Live-Aufnahmen, Metrikerfassung und einen eigenen Upload-Worker.
Der Worker kann freigegebene Clips auch hochladen, wenn der betreffende Streamer offline ist.
Er respektiert Fleet-Pause, `auto_upload` und `publish` je Streamer, Critic-Bewertung,
technische QC, Duplikat-Ledger und globale Upload-Limits. Standard: maximal vier Versuche
pro Tag, mindestens drei Stunden Abstand. Zurückgestellte Clips bleiben lokal.

TikTok bietet in diesem Ablauf keinen verlässlichen Entwurfsmodus. Ohne explizites
`publish=True` wird kein Upload-Browser geöffnet. Vorhandene Cookies bestätigen keinen
gültigen Login; 100 % Übertragung bestätigen noch keinen veröffentlichten Post.

[Sitzung einrichten und prüfen](README_UPLOAD.md) · [Profilmaterial](assets/branding/README.md)

## Qualität und Nachweise

```powershell
./start.ps1 test
uv run --locked ruff check .
uv run --locked python -m scripts.check_repository
uv build --wheel
uv run --locked python -m scripts.check_wheel
```

CI prüft Windows und Linux, Lockfile/Requirements-Abgleich, Lint, Format, Paketgrenzen,
Wheel-Inhalt und Tests mit Coverage. Tests verwenden ein isoliertes Anwendungsverzeichnis
und blockieren echte Veröffentlichungen sowie bezahlte Modellaufrufe. Die Medienintegration
rendert synthetisches Video mit ASS und Audio und prüft, dass die Quelle erhalten bleibt.

Technische QC und KI-Urteile sind keine gemessene Zuschauerbindung. Die bestehende
Audit-Kampagne dokumentiert zehn Bewertungen; Watchtime, Retention und Wachstum werden
erst bewertet, wenn belastbare Messwerte vorliegen.

## Produktiv aktualisieren

```bash
sudo bash deploy/update.sh
```

Ein Push aktualisiert die laufenden Dienste nicht. Das Wartungsskript stoppt sie, installiert
die gesperrten Abhängigkeiten und den passenden Browser, migriert bei Bedarf, prüft und
startet anschließend neu. Beim ersten Struktur-Upgrade sind zusätzlich die geprüften neuen
systemd-Einstiegspunkte zu installieren: [SETUP_SERVER.md](SETUP_SERVER.md).
