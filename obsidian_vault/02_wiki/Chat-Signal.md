# Chat-Signal (vorbereitet)

Twitch-Chat-Aktivität als zusätzliches Signal für die Clip-Auswahl, analog zu den Audio-Energie-Spitzen.
**Status:** Logik und Tests vorhanden, aber noch **nicht** an einen echten Chat-Mitschnitt angebunden.

## Ablauf
1. `chat_signal.parse_chat_log(pfad)` liest Nachrichten-Zeitstempel (Sekunden ab Aufnahmebeginn) aus JSON-Lines (`{"t": 12.3}`), einer JSON-Liste oder Textzeilen (`[HH:MM:SS] user: text`, `[MM:SS]`, reine Sekunden).
2. `find_chat_spikes()` zählt Nachrichten pro 5-s-Fenster; ein Fenster ist eine Spitze, wenn es über Mittelwert + 2 Standardabweichungen liegt **und** mindestens 5 Nachrichten hat. Benachbarte Spitzenfenster werden zusammengefasst (Peak bleibt), höchstens 15 Spitzen (stärkste).
3. `build_chat_prompt_section()` erzeugt den optionalen Prompt-Abschnitt „TWITCH CHAT ACTIVITY SPIKES“. Er weist das LLM an, kurz **vor** dem Zeitstempel zu suchen (Chat reagiert verzögert) und den Hinweis nie ohne passende Transkriptstelle zu nutzen (Regel: keine erfundenen Zeitstempel).
4. `analyze.analyze(..., chat_log_path=None)` und `select_clips(..., chat_spikes=None)` nehmen das Signal optional entgegen; ohne Log ändert sich nichts.

## Offen
- Chat-Mitschnitt (Twitch IRC/EventSub) mit gleicher Zeitbasis wie die Aufnahme erfassen, dann `chat_log_path` aus `auto_pilot`/`stream_watcher` durchreichen.
- Schwellwerte an echten Chats kalibrieren.

Siehe [[System-Architektur]].
