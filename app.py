"""Streamlit "Agent Control Center": monitors the autonomous auto_pilot.py agent in real
time (Live Radar), lets you manage the 24/7 streamer fleet orchestrator.py watches
(Streamer Verwaltung & Fleet), shows what the multimodal critic has learned — text/content,
visual/layout, and real viral-performance rules (KI Gehirn / ai_guidelines.txt) — shows real
TikTok view/like data from metrics_tracker.py (Viral Analytics), lets you browse the
surviving clips (Clip Archiv), and still offers the original one-click manual pipeline
(Manueller Modus) for processing a single video on demand.

app.py itself does not run any of the background loops — those are auto_pilot.py,
orchestrator.py, and metrics_tracker.py, each typically running as its own long-lived
process. This dashboard only reads the files those processes write (agent_state.json,
orchestrator_state.json, ai_guidelines.txt, viral_memory.json, output/)."""

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

import analyze
import auto_pilot
import ingest
import notify
import orchestrator
import process as process_module
import profiles
import streamers as streamers_module
import train_loop
import transcribe
import upload as upload_module
import tiktok_uploader

try:
    from streamlit_autorefresh import st_autorefresh
    HAS_AUTOREFRESH = True
except ImportError:
    HAS_AUTOREFRESH = False

import logging_setup

logging_setup.configure_logging()
logger = logging.getLogger(__name__)

ENV_PATH = Path(".env")
CLIENT_SECRET_PATH = Path("client_secret.json")
TEMP_DIR = Path("temp")
OUTPUT_DIR = Path("output")
AGENT_STATE_PATH = auto_pilot.AGENT_STATE_PATH
AI_GUIDELINES_PATH = analyze.AI_GUIDELINES_PATH
ORCHESTRATOR_STATE_PATH = orchestrator.ORCHESTRATOR_STATE_PATH
VIRAL_MEMORY_PATH = train_loop.VIRAL_MEMORY_PATH

# If the agent's/orchestrator's last telemetry update is older than this, treat it as
# offline/stalled rather than "just between phases" — generous enough to cover a slow
# render+critic pass (agent) or one poll interval plus a slow liveness check (orchestrator).
STALE_THRESHOLD_SECONDS = 300

FORMAT_OPTIONS = {
    "9:16 (TikTok)": "9:16",
    "1:1 (Square)": "1:1",
    "16:9 (Landscape)": "16:9",
}
LAYOUT_OPTIONS = {
    "Auto (KI entscheidet)": process_module.LAYOUT_AUTO,
    "Split-Screen": process_module.LAYOUT_SPLIT_SCREEN,
    "Blur-Background": process_module.LAYOUT_BLUR_BACKGROUND,
}
HIGHLIGHT_OPTIONS = process_module.HIGHLIGHT_COLORS
NO_PROFILE_LABEL = "Kein Profil (Standard)"

st.set_page_config(page_title="Auto-Clipping AI — Agent Control Center", page_icon="🛰️", layout="wide")

# Base look (dark background, accent color, rounded cards) comes from .streamlit/config.toml.
# This CSS only adds what the theme system can't: a gradient CTA button and a bit of card
# elevation/hover polish. Selectors use `.st-key-*` (from each widget's own `key=`) instead of
# Streamlit's internal DOM classes, which are not guaranteed stable across versions.
st.markdown(
    """
    <style>
    .st-key-process_btn button {
        background: linear-gradient(135deg, #8B5CF6 0%, #EC4899 100%);
        border: none;
        font-weight: 600;
        letter-spacing: 0.2px;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
        box-shadow: 0 4px 14px rgba(139, 92, 246, 0.35);
    }
    .st-key-process_btn button:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 20px rgba(139, 92, 246, 0.5);
    }
    .st-key-setup_card, [class*="st-key-clip_card_"] {
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
    }
    h1 {
        font-weight: 700;
        letter-spacing: -0.5px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛰️ Auto-Clipping AI — Agent Control Center")
st.caption("Überwache den autonomen Clipping-Agenten, sein gelerntes Wissen und die fertigen Clips — alles an einem Ort.")


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return {}


def state_age_seconds(state: dict, field: str = "last_updated") -> "float | None":
    raw = state.get(field)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()


agent_state = read_json_file(AGENT_STATE_PATH)
orchestrator_state = read_json_file(ORCHESTRATOR_STATE_PATH)

with st.sidebar:
    st.header("🤖 Agent Status")
    if not agent_state:
        st.caption("Auto-Pilot läuft nicht (agent_state.json nicht gefunden).")
    else:
        age = state_age_seconds(agent_state)
        offline = age is not None and age > STALE_THRESHOLD_SECONDS
        st.caption(f"Ziel: **{agent_state.get('target_streamer', '–')}**")
        if offline:
            st.warning(f"⚠️ Offline (seit {int(age)}s)")
        else:
            st.success(agent_state.get("current_action", "Aktiv"))

    st.header("🛰️ Fleet Status")
    if not orchestrator_state:
        st.caption("Orchestrator läuft nicht (orchestrator_state.json nicht gefunden).")
    else:
        orch_age = state_age_seconds(orchestrator_state, "last_check")
        orch_offline = orch_age is not None and orch_age > STALE_THRESHOLD_SECONDS
        live_count = sum(1 for s in orchestrator_state.get("streamers", {}).values() if s.get("recording"))
        if orch_offline:
            st.warning(f"⚠️ Offline (seit {int(orch_age)}s)")
        else:
            st.success(f"🟢 Aktiv — {live_count} Aufnahme(n) laufen")

    st.divider()

    if HAS_AUTOREFRESH:
        refresh_seconds = st.slider("🔄 Auto-Refresh (Sek.)", 3, 30, 5, key="autorefresh_interval")
        st_autorefresh(interval=refresh_seconds * 1000, key="dashboard_autorefresh")
    else:
        st.caption("Tipp: `pip install streamlit-autorefresh` für automatische Live-Updates.")
        if st.button("🔄 Jetzt aktualisieren", width="stretch"):
            st.rerun()

    st.divider()
    st.header("Systemstatus")
    if ENV_PATH.exists():
        st.success("✅ Bereit — .env gefunden (LLM-Keys)")
    else:
        st.error("❌ Fehlt — .env (LLM-Keys)")

    if CLIENT_SECRET_PATH.exists():
        st.success("✅ Bereit — client_secret.json (YouTube)")
    else:
        st.error("❌ Fehlt — client_secret.json (YouTube)")

    tiktok_ready, tiktok_detail = tiktok_uploader.cookies_status()
    if tiktok_ready:
        st.success("🟢 TikTok verknüpft (Bereit für Upload)")
    else:
        st.error("🔴 Keine Cookies gefunden. Führe `python get_cookies.py` aus.")
    st.caption(tiktok_detail)

    st.divider()
    st.header("🕴️ Streamer-Mitarbeiter")
    profile_names = profiles.list_profiles()
    profile_labels = [NO_PROFILE_LABEL] + profile_names
    selected_profile_label = st.selectbox("Profil", profile_labels)
    selected_profile = None
    if selected_profile_label != NO_PROFILE_LABEL:
        selected_profile = profiles.load_profile(selected_profile_label).model_dump()
        st.caption(f"Trigger-Wörter: {', '.join(selected_profile['trigger_words']) or '–'}")
        if selected_profile["context_prompt"]:
            st.caption(f"Kontext: {selected_profile['context_prompt']}")


tab_radar, tab_fleet, tab_brain, tab_analytics, tab_archive, tab_manual = st.tabs(
    ["📡 Live Radar & Status", "👥 Streamer Verwaltung & Fleet", "🧠 KI Gehirn (Memory)",
     "📈 Viral Analytics", "🎞️ Clip Archiv", "🎛️ Manueller Modus"]
)

# --- Tab 1: Live Radar & Status ------------------------------------------------------------

with tab_radar:
    st.subheader("📡 Live Radar & Status")

    if not agent_state:
        st.info(
            "Der Auto-Pilot läuft aktuell nicht. Starte ihn in einem separaten Terminal, "
            "z. B.:\n\n`python auto_pilot.py --profile eliasn97 --live`"
        )
    else:
        age = state_age_seconds(agent_state)
        target_streamer = agent_state.get("target_streamer", "–")
        current_action = agent_state.get("current_action", "Unbekannt")
        current_cycle = agent_state.get("current_cycle", 0)
        kept_total = agent_state.get("clips_kept_total", 0)
        purged_total = agent_state.get("clips_purged_total", 0)

        if age is not None and age > STALE_THRESHOLD_SECONDS:
            st.warning(f"⚠️ Agent scheint offline zu sein — letztes Update vor {int(age)}s.")
        else:
            st.success(f"🟢 Agent aktiv — Ziel: **{target_streamer}**")

        st.markdown(f"### {current_action}")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Zyklus", current_cycle)
        col2.metric("🏆 Clips behalten", kept_total)
        col3.metric("🗑️ Clips gelöscht", purged_total)
        total_scored = kept_total + purged_total
        survival_rate = f"{(kept_total / total_scored * 100):.0f}%" if total_scored else "–"
        col4.metric("Überlebensrate", survival_rate)

        if agent_state.get("purge_threshold") is not None:
            st.caption(
                f"Purge-Schwellenwert: reward_score < {agent_state['purge_threshold']} · "
                f"Modus: {'🔴 Live' if agent_state.get('live') else '🎞️ VOD'}"
            )

        with st.expander("Rohdaten (agent_state.json)"):
            st.json(agent_state)

    st.divider()
    st.markdown("#### 🔗 TikTok Upload Status")
    tiktok_ready, tiktok_detail = tiktok_uploader.cookies_status()
    if tiktok_ready:
        st.success("🟢 TikTok verknüpft (Bereit für Upload)")
    else:
        st.error("🔴 Keine Cookies gefunden. Führe `python get_cookies.py` aus.")
    st.caption(tiktok_detail)

# --- Tab 2: Streamer Verwaltung & Fleet ------------------------------------------------------

with tab_fleet:
    st.subheader("👥 Streamer Verwaltung & Fleet")
    st.caption(
        "Streamer, die orchestrator.py rund um die Uhr überwacht — sobald einer live geht, "
        "startet der Orchestrator automatisch einen auto_pilot.py-Prozess für ihn."
    )

    if not orchestrator_state:
        st.info(
            "Der Orchestrator läuft aktuell nicht. Starte ihn in einem separaten Terminal:\n\n"
            "`python orchestrator.py`"
        )
    else:
        orch_age = state_age_seconds(orchestrator_state, "last_check")
        if orch_age is not None and orch_age > STALE_THRESHOLD_SECONDS:
            st.warning(f"⚠️ Orchestrator scheint offline zu sein — letzter Check vor {int(orch_age)}s.")
        else:
            st.success(
                f"🟢 Orchestrator aktiv — Poll-Intervall: {orchestrator_state.get('poll_interval', '–')}s"
            )
        with st.expander("Rohdaten (orchestrator_state.json)"):
            st.json(orchestrator_state)

    st.divider()
    st.markdown("#### Konfigurierte Streamer")

    streamer_entries = streamers_module.load_streamers()
    orchestrator_streamers = (orchestrator_state or {}).get("streamers", {})

    if not streamer_entries:
        st.info("Noch keine Streamer konfiguriert. Füge unten einen hinzu.")
    else:
        for entry in streamer_entries:
            live_info = orchestrator_streamers.get(entry["name"], {})
            is_recording = live_info.get("recording", False)
            is_live = live_info.get("live", False)

            with st.container(border=True, key=f"streamer_card_{entry['name']}"):
                col_info, col_status, col_delete = st.columns([3, 2, 1])
                with col_info:
                    st.markdown(f"**{entry['name']}**")
                    st.caption(entry["url"])
                    details = []
                    if entry.get("profile"):
                        details.append(f"Profil: {entry['profile']}")
                    details.append("Auto-Upload: " + ("✅" if entry.get("auto_upload") else "❌"))
                    st.caption(" · ".join(details))
                with col_status:
                    if is_recording:
                        st.success(f"🔴 LIVE — wird aufgenommen (PID {live_info.get('pid', '–')})")
                    elif is_live:
                        st.warning("🟡 Live erkannt, Aufnahme startet...")
                    else:
                        st.caption("⚪ Offline")
                with col_delete:
                    if st.button("🗑️ Entfernen", key=f"del_streamer_{entry['name']}"):
                        streamers_module.remove_streamer(entry["name"])
                        st.rerun()

    st.divider()
    st.markdown("#### ➕ Neuen Streamer hinzufügen")
    with st.form("add_streamer_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            new_streamer_name = st.text_input("Name")
            new_streamer_url = st.text_input("Stream-URL", placeholder="https://twitch.tv/...")
        with col2:
            fleet_profile_options = [""] + profiles.list_profiles()
            new_streamer_profile = st.selectbox("Profil (optional)", fleet_profile_options)
            new_streamer_auto_upload = st.checkbox("🚀 Auto-Upload zu TikTok aktivieren")

        add_streamer_submitted = st.form_submit_button("Hinzufügen", type="primary")
        if add_streamer_submitted:
            if not new_streamer_name or not new_streamer_url:
                st.error("Name und Stream-URL sind erforderlich.")
            else:
                try:
                    streamers_module.add_streamer(
                        new_streamer_name, new_streamer_url, new_streamer_profile, new_streamer_auto_upload
                    )
                    st.success(f"'{new_streamer_name}' hinzugefügt.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

# --- Tab 3: KI Gehirn (Memory) --------------------------------------------------------------

with tab_brain:
    st.subheader("🧠 KI Gehirn (Memory)")
    st.caption(
        "Das Lern-Protokoll des multimodalen Critics (train_loop.py) — getrennt nach "
        "Text/Content, visueller Komposition und echten Viral-Erfolgsmustern."
    )

    if not AI_GUIDELINES_PATH.exists():
        st.info(
            "Noch keine gelernten Regeln vorhanden. ai_guidelines.txt wird automatisch "
            "erzeugt, sobald der Critic (train_loop.py, z. B. via auto_pilot.py) den ersten "
            "Clip-Batch bewertet hat."
        )
    else:
        content = AI_GUIDELINES_PATH.read_text(encoding="utf-8")
        categories = train_loop.parse_guidelines_file(content)

        st.markdown("### 📝 Text/Content Rules")
        col_pos, col_neg = st.columns(2)
        with col_pos:
            st.markdown("#### ✅ Positive Rewards — DO THIS")
            if categories["content_positive"]:
                st.success("\n\n".join(f"✓ {rule}" for rule in categories["content_positive"]))
            else:
                st.info("Noch keine positiven Regeln gelernt.")
        with col_neg:
            st.markdown("#### 🚫 Penalties — NEVER DO THIS")
            if categories["content_negative"]:
                st.error("\n\n".join(f"✗ {rule}" for rule in categories["content_negative"]))
            else:
                st.info("Noch keine Penalty-Regeln gelernt.")

        st.divider()
        st.markdown("### 🎨 Visual/Layout Rules")
        st.caption("Aus dem Vision-Critic — bewertet Screenshots der gerenderten Clips (Facecam-Position, Gameplay-Sichtbarkeit, Glitches).")
        col_vpos, col_vneg = st.columns(2)
        with col_vpos:
            st.markdown("#### ✅ Visuell — DO THIS")
            if categories["visual_positive"]:
                st.success("\n\n".join(f"✓ {rule}" for rule in categories["visual_positive"]))
            else:
                st.info("Noch keine visuellen Regeln gelernt.")
        with col_vneg:
            st.markdown("#### 🚫 Visuell — NEVER DO THIS")
            if categories["visual_negative"]:
                st.error("\n\n".join(f"✗ {rule}" for rule in categories["visual_negative"]))
            else:
                st.info("Noch keine visuellen Penalty-Regeln gelernt.")

        st.divider()
        st.markdown("### 🔥 Viral Success Patterns")
        st.caption("Aus echten TikTok-Performance-Daten (viral_memory.json via metrics_tracker.py).")
        if categories["viral_patterns"]:
            st.success("\n\n".join(f"🔥 {rule}" for rule in categories["viral_patterns"]))
        else:
            st.info("Noch keine Muster aus echten Performance-Daten gelernt.")

        with st.expander("Rohtext (ai_guidelines.txt)"):
            st.code(content, language=None)

# --- Tab 4: Viral Analytics ------------------------------------------------------------------

with tab_analytics:
    st.subheader("📈 Viral Analytics")
    st.caption("Echte View-/Like-Zahlen für hochgeladene Clips, erfasst von metrics_tracker.py.")

    viral_memory = read_json_file(VIRAL_MEMORY_PATH)
    if not viral_memory:
        st.info(
            "Noch keine Performance-Daten vorhanden. Starte `python metrics_tracker.py` in "
            "einem separaten Terminal, um View-/Like-Zahlen für Clips in uploaded_clips/ zu erfassen."
        )
    else:
        rows = []
        for clip_id, entry in viral_memory.items():
            rows.append({
                "Titel": entry.get("title", clip_id),
                "Views": entry.get("views"),
                "Likes": entry.get("likes"),
                "Viral Score (KI)": entry.get("viral_score"),
                "Energie": entry.get("energy_rating"),
                "Critic Score": entry.get("reward_score"),
                "Zuletzt geprüft": entry.get("checked_at", "–"),
            })

        measured = [r for r in rows if r["Views"] is not None]
        unmeasured_count = len(rows) - len(measured)

        if measured:
            total_views = sum(r["Views"] or 0 for r in measured)
            total_likes = sum(r["Likes"] or 0 for r in measured)
            best = max(measured, key=lambda r: r["Views"] or 0)

            col1, col2, col3 = st.columns(3)
            col1.metric("Gesamt-Views", f"{total_views:,}".replace(",", "."))
            col2.metric("Gesamt-Likes", f"{total_likes:,}".replace(",", "."))
            col3.metric("Top-Clip", best["Titel"], f"{best['Views']:,} Views".replace(",", "."))

            st.divider()
            sorted_rows = sorted(measured, key=lambda r: r["Views"] or 0, reverse=True)
            st.dataframe(sorted_rows, width="stretch", hide_index=True)
        else:
            st.info("Clips sind erfasst, aber noch keine Metriken gemessen.")

        if unmeasured_count:
            st.caption(f"{unmeasured_count} Clip(s) noch ohne gemessene Metriken.")

# --- Tab 5: Clip Archiv ---------------------------------------------------------------------

with tab_archive:
    st.subheader("🎞️ Clip Archiv")

    video_files = sorted(OUTPUT_DIR.glob("*.mp4")) if OUTPUT_DIR.exists() else []

    if not video_files:
        st.info("Noch keine Clips vorhanden. Starte den Auto-Piloten oder den manuellen Modus, um Ergebnisse zu sehen.")
    else:
        clips_by_index = {}
        clips_json_path = None
        if "last_clips_path" in st.session_state:
            clips_json_path = Path(st.session_state["last_clips_path"])
        else:
            clips_candidates = sorted(
                TEMP_DIR.glob("*_clips.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            clips_json_path = clips_candidates[0] if clips_candidates else None

        if clips_json_path and clips_json_path.exists():
            with open(clips_json_path, "r", encoding="utf-8") as f:
                clips_data = json.load(f)
            clips_by_index = {i: c for i, c in enumerate(clips_data.get("clips", []), start=1)}

        for position, video_path in enumerate(video_files):
            match = re.match(r"clip_(\d+)_", video_path.name)
            index = int(match.group(1)) if match else None
            clip = clips_by_index.get(index)

            clip_title = clip["title"] if clip else video_path.name

            with st.container(border=True, key=f"clip_card_{position}"):
                col_video, col_info = st.columns([1, 1])
                with col_video:
                    st.video(str(video_path))
                with col_info:
                    if clip:
                        st.markdown(f"### {clip['title']}")
                        col_viral, col_energy = st.columns(2)
                        col_viral.markdown(f"**Viral Score:** {clip['viral_score']}/10")
                        col_energy.markdown(f"**🔥 Energie-Level:** {clip.get('energy_rating', '–')}/10")
                        st.write(clip["hook_explanation"])
                        if clip.get("description"):
                            st.markdown(f"**📝 Caption:** {clip['description']}")
                        if clip.get("hashtags"):
                            st.caption(" ".join(clip["hashtags"]))
                    else:
                        st.markdown(f"### {video_path.name}")
                        st.caption("Keine KI-Metadaten gefunden.")

                    feedback_text = st.text_input(
                        "Was war schlecht an diesem Clip?",
                        key=f"feedback_input_{video_path.name}",
                    )
                    col_save, col_download, col_delete = st.columns(3)
                    with col_save:
                        if st.button("Feedback speichern", key=f"feedback_btn_{video_path.name}"):
                            if feedback_text:
                                analyze.save_feedback(clip_title, feedback_text)
                                st.success("Feedback gespeichert ✅")
                            else:
                                st.warning("Bitte zuerst ein Feedback eingeben.")
                    with col_download:
                        st.download_button(
                            label="📥 Herunterladen",
                            data=video_path.read_bytes(),
                            file_name=os.path.basename(video_path),
                            mime="video/mp4",
                            key=f"download_{video_path.name}",
                        )
                    with col_delete:
                        if st.button("🗑️ Clip löschen", key=f"del_{video_path.name}"):
                            os.remove(video_path)
                            logger.info("Deleted clip %s", video_path)
                            st.rerun()

# --- Tab 6: Manueller Modus (original one-click pipeline) -------------------------------------

with tab_manual:
    st.subheader("🎛️ Manueller Modus")
    st.caption("Ein einzelnes Video von der Quelle bis zu fertigen Shorts verarbeiten — unabhängig vom Auto-Piloten.")

    st.markdown("##### Video-Quelle")
    col_url, col_upload = st.columns(2)
    with col_url:
        url = st.text_input("YouTube/Twitch-URL", placeholder="https://...")
    with col_upload:
        uploaded_file = st.file_uploader("...oder lokales Video hochladen", type=["mp4", "mkv"])

    st.markdown("##### 🎛️ Individuelle Einstellungen")
    col_format, col_layout, col_highlight = st.columns(3)
    with col_format:
        format_label = st.selectbox("Video-Format", list(FORMAT_OPTIONS.keys()))
        selected_format = FORMAT_OPTIONS[format_label]
    with col_layout:
        layout_label = st.selectbox("Layout", list(LAYOUT_OPTIONS.keys()))
        selected_layout = LAYOUT_OPTIONS[layout_label]
    with col_highlight:
        highlight_label = st.selectbox("Untertitel-Highlight", list(HIGHLIGHT_OPTIONS.keys()))
        selected_highlight = HIGHLIGHT_OPTIONS[highlight_label]

    with st.expander("⚙️ Erweiterte Optionen"):
        do_upload = st.checkbox("Automatischer Upload zu YouTube", value=False)
        do_tiktok_upload = st.checkbox("🚀 Automatisch auf TikTok hochladen", value=False)
        do_notify = st.checkbox("🔔 Discord-Benachrichtigungen senden", value=False)

    st.divider()
    process_clicked = st.button("🚀 Video verarbeiten", type="primary", width="stretch", key="process_btn")

    def resolve_source_video() -> Path:
        if uploaded_file is not None:
            dest = Path(uploaded_file.name)
            dest.write_bytes(uploaded_file.getbuffer())
            logger.info("Saved uploaded video to %s", dest)
            return dest

        if url:
            return ingest.download_from_url(url)

        raise ValueError("Bitte eine URL angeben oder ein Video hochladen.")

    if process_clicked:
        if shutil.which("ffmpeg") is None:
            st.error("FFmpeg ist nicht installiert oder nicht im PATH. Bitte installiere es.")
            st.stop()

        if not ENV_PATH.exists():
            st.error("❌ .env fehlt. Bitte lege eine .env-Datei mit deinem LLM-API-Key an, bevor du startest.")
            st.stop()

        if do_upload and not CLIENT_SECRET_PATH.exists():
            st.error("❌ client_secret.json fehlt. Wird für den automatischen YouTube-Upload benötigt.")
            st.stop()

        if do_tiktok_upload and not tiktok_uploader.cookies_status()[0]:
            st.error(
                "❌ Keine gültigen TikTok-Cookies gefunden. Führe `python get_cookies.py` aus, "
                "bevor du den Auto-Upload aktivierst."
            )
            st.stop()

        try:
            # Phase 1: ingest -> transcribe -> analyze (single status block, this part is fast
            # relative to rendering and doesn't need per-item progress).
            with st.status("Transkription & Analyse laufen...", expanded=True) as status:
                status.write("📥 Lese Quellvideo...")
                video_path = resolve_source_video()

                status.write("🎧 Extrahiere Audio...")
                wav_path = ingest.extract_audio(video_path)

                status.write("📝 Transkribiere (faster-whisper)...")
                transcription_path = transcribe.transcribe(wav_path)

                profile_note = f" mit Profil '{selected_profile['name']}'" if selected_profile else ""
                status.write(f"🤖 KI-Analyse der Szenen (inkl. Emotional-Energy-Scoring){profile_note}...")
                clips_path = analyze.analyze(transcription_path, audio_path=wav_path, profile=selected_profile)

                transcript = analyze.load_transcript(transcription_path)
                with open(clips_path, "r", encoding="utf-8") as f:
                    clips_data = json.load(f)
                clips = clips_data.get("clips", [])
                st.session_state["last_clips_path"] = str(clips_path)

                if clips:
                    status.update(
                        label=f"Analyse abgeschlossen ✅ — {len(clips)} Clip(s) gefunden, Rendering startet",
                        state="complete",
                    )
                else:
                    status.update(
                        label="Analyse abgeschlossen — kein Content mit hohem viralem Potenzial gefunden",
                        state="complete",
                    )

            if not clips:
                # The AI deliberately found nothing worth clipping in this video (see analyze.py's
                # quality gate) — a valid, intentional result, not an error. Nothing to render.
                st.info(
                    "ℹ️ Die KI hat in diesem Video keine Clips mit ausreichend hohem viralem "
                    "Potenzial gefunden. Es wurde nichts gerendert."
                )
            else:
                # Phase 2: render each clip individually so the UI can show real per-clip progress,
                # then immediately upload/notify for that clip — a fully automatic loop, no manual
                # confirmation step in between.
                st.subheader("🎬 Rendering")
                progress_bar = st.progress(0.0)
                clip_status = st.empty()

                for i, total, clip, output_path in process_module.process_clips_iter(
                    video_path,
                    layout=selected_layout,
                    video_format=selected_format,
                    highlight_color=selected_highlight,
                    transcript=transcript,
                ):
                    with clip_status, st.spinner(f"Clip {i}/{total}: {clip['title']}"):
                        upload_status = "rendered"
                        hashtags = clip.get("hashtags") or tiktok_uploader.DEFAULT_HASHTAGS

                        if do_tiktok_upload:
                            outcome = tiktok_uploader.try_upload_clip(
                                output_path, clip.get("description", clip["title"]), hashtags
                            )
                            upload_status = "uploaded" if outcome.success else "failed"

                        if do_notify:
                            notify.send_notification(
                                title=clip["title"],
                                energy=clip.get("energy_rating", 0),
                                filepath=output_path,
                                upload_status=upload_status,
                                description=clip.get("description", ""),
                                hashtags=hashtags,
                            )

                    progress_bar.progress(i / total, text=f"{i}/{total} Clips gerendert")

                clip_status.empty()

                if do_upload:
                    with st.spinner("☁️ Lade Clips zu YouTube hoch (privat)..."):
                        upload_module.upload_all()

                st.success(f"✅ Fertig! {len(clips)} Clip(s) verarbeitet. Siehe Tab \"🎞️ Clip Archiv\".")
        except Exception as e:
            logger.exception("Pipeline failed")
            st.error(f"Fehler in der Pipeline: {e}")
