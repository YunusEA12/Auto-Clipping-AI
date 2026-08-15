"""Streamlit web UI to configure and run the Auto-Clipping AI pipeline end-to-end."""

import json
import logging
import os
import re
import shutil
from pathlib import Path

import streamlit as st

import ingest
import transcribe
import analyze
import process as process_module
import upload as upload_module

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ENV_PATH = Path(".env")
CLIENT_SECRET_PATH = Path("client_secret.json")
CLIPS_PATH = Path("temp/clips.json")
OUTPUT_DIR = Path("output")
FEEDBACK_PATH = Path("feedback.json")

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

st.set_page_config(page_title="Auto-Clipping AI", page_icon="🎬", layout="wide")
st.title("🎬 Auto-Clipping AI")

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

st.divider()
start_clicked = st.button("🚀 Pipeline starten", type="primary", width="stretch")


def resolve_source_video() -> Path:
    if uploaded_file is not None:
        dest = Path(uploaded_file.name)
        dest.write_bytes(uploaded_file.getbuffer())
        logger.info("Saved uploaded video to %s", dest)
        return dest

    if url:
        import yt_dlp

        ydl_opts = {
            "format": "mp4/bestvideo+bestaudio",
            "outtmpl": "%(title)s.%(ext)s",
            "merge_output_format": "mp4",
        }
        logger.info("Downloading source video from %s", url)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        return Path(filename)

    raise ValueError("Bitte eine URL angeben oder ein Video hochladen.")


def save_feedback(clip_title: str, feedback_text: str) -> None:
    entries = []
    if FEEDBACK_PATH.exists():
        try:
            with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append({"clip_title": clip_title, "feedback": feedback_text})

    with open(FEEDBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    logger.info("Saved feedback for clip '%s'", clip_title)


if start_clicked:
    if shutil.which("ffmpeg") is None:
        st.error("FFmpeg ist nicht installiert oder nicht im PATH. Bitte installiere es.")
        st.stop()

    if not ENV_PATH.exists():
        st.error("❌ .env fehlt. Bitte lege eine .env-Datei mit deinem LLM-API-Key an, bevor du startest.")
        st.stop()

    if do_upload and not CLIENT_SECRET_PATH.exists():
        st.error("❌ client_secret.json fehlt. Wird für den automatischen YouTube-Upload benötigt.")
        st.stop()

    try:
        with st.status("Pipeline läuft...", expanded=True) as status:
            status.write("📥 Lese Quellvideo...")
            video_path = resolve_source_video()

            status.write("🎧 Extrahiere Audio...")
            wav_path = ingest.extract_audio(video_path)

            status.write("📝 Transkribiere (faster-whisper)...")
            transcribe.transcribe(wav_path)

            status.write("🤖 KI-Analyse der Szenen...")
            analyze.analyze()

            status.write("🎬 Schneide & rendere Clips...")
            process_module.process(
                video_path,
                layout=selected_layout,
                video_format=selected_format,
                highlight_color=selected_highlight,
            )

            if do_upload:
                status.write("☁️ Lade Clips zu YouTube hoch (privat)...")
                upload_module.upload_all()

            status.update(label="Pipeline abgeschlossen ✅", state="complete")
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
    if CLIPS_PATH.exists():
        with open(CLIPS_PATH, "r", encoding="utf-8") as f:
            clips_data = json.load(f)
        clips_by_index = {i: c for i, c in enumerate(clips_data.get("clips", []), start=1)}

    for video_path in video_files:
        match = re.match(r"clip_(\d+)_", video_path.name)
        index = int(match.group(1)) if match else None
        clip = clips_by_index.get(index)

        clip_title = clip["title"] if clip else video_path.name

        col_video, col_info = st.columns([1, 1])
        with col_video:
            st.video(str(video_path))
        with col_info:
            if clip:
                st.markdown(f"### {clip['title']}")
                st.markdown(f"**Viral Score:** {clip['viral_score']}/10")
                st.write(clip["hook_explanation"])
            else:
                st.markdown(f"### {video_path.name}")
                st.caption("Keine KI-Metadaten gefunden.")

            feedback_text = st.text_input(
                "Was war schlecht an diesem Clip?",
                key=f"feedback_input_{video_path.name}",
            )
            col_save, col_delete = st.columns(2)
            with col_save:
                if st.button("Feedback speichern", key=f"feedback_btn_{video_path.name}"):
                    if feedback_text:
                        save_feedback(clip_title, feedback_text)
                        st.success("Feedback gespeichert ✅")
                    else:
                        st.warning("Bitte zuerst ein Feedback eingeben.")
            with col_delete:
                if st.button("🗑️ Clip löschen", key=f"del_{video_path.name}"):
                    os.remove(video_path)
                    logger.info("Deleted clip %s", video_path)
                    st.rerun()
        st.divider()
