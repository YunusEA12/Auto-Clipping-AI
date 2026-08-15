"""Streamlit web UI to configure and run the Auto-Clipping AI pipeline end-to-end."""

import json
import logging
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

with st.expander("⚙️ Erweiterte Optionen"):
    do_upload = st.checkbox("Automatischer Upload zu YouTube", value=False)

st.divider()
start_clicked = st.button("🚀 Pipeline starten", type="primary", use_container_width=True)


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
            process_module.process(video_path)

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
        st.divider()
