# AGENT_REPORT – Aufgabe 4: chat-signal

**Aufgabe:** Chat-Spitzen-Erkennung + optionaler Prompt-Abschnitt, noch ohne echte Chat-Anbindung.

**Geänderte Dateien**
- `chat_signal.py` (neu): Log-Parser (3 Formate), Fenster-Zählung, Spitzenerkennung (Mittel+2σ, Mindestzahl 5, Merge, Top 15), `build_chat_prompt_section()`.
- `analyze.py`: `select_clips(chat_spikes=...)` hängt den Abschnitt an den Energie-Abschnitt; `analyze(chat_log_path=None)`. Default-Verhalten unverändert.
- `obsidian_vault/02_wiki/Chat-Signal.md` (neu) + Link in `index.md`.
- `tests/test_chat_signal.py` (neu, 7 Tests; Chat-Log aus Zufallsrauschen + Burst, Prompt-Integration über gemockten Client).

**Tests:** `uv run --locked pytest -q` 790 passed (Baseline 783).

**Prüfhinweise:** Schwellwerte sind unkalibriert (keine echten Chats). Niemand ruft `chat_log_path` bisher auf. Prompt-Text bewusst als schwacher Hinweis formuliert.
