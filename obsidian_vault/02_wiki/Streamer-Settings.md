# Streamer-Settings

Einstellungen und Konfiguration je überwachtem Streamer/Kanal. Der Agent-Status pro Streamer wird in `agent_state_<Name>.json` im Projekt-Root persistiert (atomic writes, siehe `atomic_io.py`).

## Aktuell bekannte Streamer

- Trymacs
- eliasn97
- montanablack88

Details zu Layout, Cropping und Facecam-Position pro Streamer siehe [[Layout-Logik]].

## Verwandte Module

- [[index]]
- [[System-Architektur]]
- [[Status]]
