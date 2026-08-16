"""Streamlit web UI: a "zero-touch" pipeline — one click takes a video from source all the
way through analysis, per-clip rendering, and optional upload/notification."""

import json
import logging
import os
import re
import shutil
from pathlib import Path

import streamlit as st

import ingest
import notify
import process as process_module
import profiles
import transcribe
import analyze
import upload as upload_module
import upload_tiktok

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ENV_PATH = Path(".env")
CLIENT_SECRET_PATH = Path("client_secret.json")
TIKTOK_CLIENT_CONFIG_PATH = Path("tiktok_client_secret.json")
TEMP_DIR = Path("temp")
OUTPUT_DIR = Path("output")

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

st.set_page_config(page_title="Auto-Clipping AI", page_icon="🎬", layout="wide")

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

st.title("🎬 Auto-Clipping AI")
st.caption("Von der langen VOD zu fertig geschnittenen, untertitelten Shorts — vollautomatisch.")

with st.sidebar:
    st.header("Systemstatus")
    if ENV_PATH.exists():
        st.success("✅ Bereit — .env gefunden (LLM-Keys)")
    else:
        st.error("❌ Fehlt — .env (LLM-Keys)")

    if CLIENT_SECRET_PATH.exists():
        st.success("✅ Bereit — client_secret.json (YouTube)")
    else:
        st.error("❌ Fehlt — client_secret.json (YouTube)")

    if TIKTOK_CLIENT_CONFIG_PATH.exists():
        st.success("✅ Bereit — tiktok_client_secret.json (TikTok)")
    else:
        st.error("❌ Fehlt — tiktok_client_secret.json (TikTok)")

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

st.subheader("Video-Quelle")
col_url, col_upload = st.columns(2)
with col_url:
    url = st.text_input("YouTube/Twitch-URL", placeholder="https://...")
with col_upload:
    uploaded_file = st.file_uploader("...oder lokales Video hochladen", type=["mp4", "mkv"])

st.subheader("🎛️ Individuelle Einstellungen")
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

    if do_tiktok_upload and not TIKTOK_CLIENT_CONFIG_PATH.exists():
        st.error("❌ tiktok_client_secret.json fehlt. Wird für den automatischen TikTok-Upload benötigt.")
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

            status.update(
                label=f"Analyse abgeschlossen ✅ — {len(clips)} Clip(s) gefunden, Rendering startet",
                state="complete",
            )

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
                hashtags = clip.get("hashtags") or []
                hashtags_str = " ".join(
                    h if h.startswith("#") else f"#{h}" for h in hashtags
                ) or upload_tiktok.DEFAULT_HASHTAGS

                if do_tiktok_upload:
                    caption = upload_tiktok.build_caption(
                        clip["title"], hashtags_str, clip.get("description", "")
                    )
                    publish_id = upload_tiktok.try_upload_to_tiktok(output_path, caption)
                    upload_status = "uploaded" if publish_id else "failed"

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

        st.success(f"✅ Fertig! {len(clips)} Clip(s) verarbeitet.")
    except Exception as e:
        logger.exception("Pipeline failed")
        st.error(f"Fehler in der Pipeline: {e}")

st.divider()
st.subheader("Ergebnisse")

video_files = sorted(OUTPUT_DIR.glob("*.mp4")) if OUTPUT_DIR.exists() else []

if not video_files:
    st.info("Noch keine Clips vorhanden. Starte die Pipeline, um Ergebnisse zu sehen.")
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
