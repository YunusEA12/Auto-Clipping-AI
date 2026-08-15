"""Streamlit web UI to configure and run the Auto-Clipping AI pipeline end-to-end."""

import json
import logging
import os
import re
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

import ingest
import transcribe
import analyze
import process as process_module
import upload as upload_module
import upload_tiktok

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ENV_PATH = Path(".env")
CLIENT_SECRET_PATH = Path("client_secret.json")
TIKTOK_CLIENT_CONFIG_PATH = Path("tiktok_client_secret.json")
CLIPS_PATH = Path("temp/clips.json")
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

st.set_page_config(page_title="Auto-Clipping AI", page_icon="🎬", layout="wide")

# Base look (dark background, accent color, rounded cards) comes from .streamlit/config.toml.
# This CSS only adds what the theme system can't: a gradient CTA button and a bit of card
# elevation/hover polish. Selectors use `.st-key-*` (from each widget's own `key=`) instead of
# Streamlit's internal DOM classes, which are not guaranteed stable across versions.
st.markdown(
    """
    <style>
    .st-key-analyze_btn button, .st-key-render_btn button {
        background: linear-gradient(135deg, #8B5CF6 0%, #EC4899 100%);
        border: none;
        font-weight: 600;
        letter-spacing: 0.2px;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
        box-shadow: 0 4px 14px rgba(139, 92, 246, 0.35);
    }
    .st-key-analyze_btn button:hover, .st-key-render_btn button:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 20px rgba(139, 92, 246, 0.5);
    }
    .st-key-setup_card, .st-key-editor_card, [class*="st-key-clip_card_"] {
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
st.caption("Von der langen VOD zum geschnittenen, untertitelten Short — automatisiert.")

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

with st.container(border=True, key="setup_card"):
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

    analyze_clicked = st.button(
        "🔍 Transkribieren & Analysieren", type="primary", width="stretch", key="analyze_btn"
    )


def resolve_source_video() -> Path:
    if uploaded_file is not None:
        dest = Path(uploaded_file.name)
        dest.write_bytes(uploaded_file.getbuffer())
        logger.info("Saved uploaded video to %s", dest)
        return dest

    if url:
        return ingest.download_from_url(url)

    raise ValueError("Bitte eine URL angeben oder ein Video hochladen.")


def build_editable_segments(transcript: dict, clips_data: dict) -> list:
    """Segments that actually overlap a selected clip — the only ones that end up as subtitles."""
    rows = []
    for seg_idx, seg in enumerate(transcript.get("segments", [])):
        for clip in clips_data.get("clips", []):
            if seg["end"] <= clip["start_time"] or seg["start"] >= clip["end_time"]:
                continue
            rows.append(
                {
                    "segment_index": seg_idx,
                    "clip_title": clip["title"],
                    "start": round(seg["start"], 2),
                    "end": round(seg["end"], 2),
                    "text": seg["text"],
                }
            )
            break
    return rows


def apply_subtitle_edits(transcript: dict, edited_df: pd.DataFrame) -> int:
    """Write edited text back into the transcript. Edited segments lose their word-level
    timestamps (no longer valid for arbitrary new text) and fall back to whole-segment
    subtitle rendering, which process.py already handles safely."""
    segments = transcript.get("segments", [])
    changed = 0
    for _, row in edited_df.iterrows():
        idx = int(row["segment_index"])
        new_text = str(row["text"]).strip()
        if idx < len(segments) and new_text and new_text != segments[idx]["text"]:
            segments[idx]["text"] = new_text
            segments[idx]["words"] = []
            changed += 1
    if changed:
        logger.info("%d subtitle segment(s) edited by user", changed)
    return changed


if analyze_clicked:
    if shutil.which("ffmpeg") is None:
        st.error("FFmpeg ist nicht installiert oder nicht im PATH. Bitte installiere es.")
        st.stop()

    if not ENV_PATH.exists():
        st.error("❌ .env fehlt. Bitte lege eine .env-Datei mit deinem LLM-API-Key an, bevor du startest.")
        st.stop()

    try:
        with st.status("Transkription & Analyse laufen...", expanded=True) as status:
            status.write("📥 Lese Quellvideo...")
            video_path = resolve_source_video()

            status.write("🎧 Extrahiere Audio...")
            wav_path = ingest.extract_audio(video_path)

            status.write("📝 Transkribiere (faster-whisper)...")
            transcription_path = transcribe.transcribe(wav_path)

            status.write("🤖 KI-Analyse der Szenen (inkl. Emotional-Energy-Scoring)...")
            analyze.analyze(transcription_path, audio_path=wav_path)

            st.session_state["video_path"] = str(video_path)
            st.session_state["transcript"] = analyze.load_transcript(transcription_path)
            with open(CLIPS_PATH, "r", encoding="utf-8") as f:
                st.session_state["clips_data"] = json.load(f)

            status.update(label="Analyse abgeschlossen ✅ — jetzt Untertitel prüfen", state="complete")
    except Exception as e:
        logger.exception("Analysis failed")
        st.error(f"Fehler bei Transkription/Analyse: {e}")

if st.session_state.get("transcript") is not None:
    with st.container(border=True, key="editor_card"):
        st.subheader("✏️ Untertitel bearbeiten")
        st.caption("Korrigiere Tippfehler oder falsch erkannte Wörter (z. B. Gaming-Slang), bevor die Clips gerendert werden.")

        rows = build_editable_segments(st.session_state["transcript"], st.session_state["clips_data"])
        if rows:
            edited_df = st.data_editor(
                pd.DataFrame(rows),
                column_config={
                    "segment_index": None,
                    "clip_title": st.column_config.TextColumn("Clip", disabled=True),
                    "start": st.column_config.NumberColumn("Start (s)", disabled=True),
                    "end": st.column_config.NumberColumn("Ende (s)", disabled=True),
                    "text": st.column_config.TextColumn("Text", width="large"),
                },
                hide_index=True,
                width="stretch",
                key="subtitle_editor",
            )

            if st.button("✅ Änderungen übernehmen"):
                changed = apply_subtitle_edits(st.session_state["transcript"], edited_df)
                st.success(f"{changed} Untertitel-Segment(e) aktualisiert ✅" if changed else "Keine Änderungen erkannt.")
        else:
            st.info("Keine Untertitel-Segmente für die ausgewählten Clips gefunden.")

        render_clicked = st.button(
            "🎬 Clips rendern", type="primary", width="stretch", key="render_btn"
        )

    if render_clicked:
        if do_upload and not CLIENT_SECRET_PATH.exists():
            st.error("❌ client_secret.json fehlt. Wird für den automatischen YouTube-Upload benötigt.")
            st.stop()

        if do_tiktok_upload and not TIKTOK_CLIENT_CONFIG_PATH.exists():
            st.error("❌ tiktok_client_secret.json fehlt. Wird für den automatischen TikTok-Upload benötigt.")
            st.stop()

        try:
            with st.status("Clips werden gerendert...", expanded=True) as status:
                status.write("🎬 Schneide & rendere Clips...")
                rendered_paths = process_module.process(
                    Path(st.session_state["video_path"]),
                    layout=selected_layout,
                    video_format=selected_format,
                    highlight_color=selected_highlight,
                    transcript=st.session_state["transcript"],
                )

                if do_upload:
                    status.write("☁️ Lade Clips zu YouTube hoch (privat)...")
                    upload_module.upload_all()

                if do_tiktok_upload:
                    status.write("🚀 Lade Clips zu TikTok hoch (privat)...")
                    clips_by_index = {
                        i: c for i, c in enumerate(st.session_state["clips_data"].get("clips", []), start=1)
                    }
                    for rendered_path in rendered_paths:
                        match = re.match(r"clip_(\d+)_", rendered_path.name)
                        idx = int(match.group(1)) if match else None
                        rendered_clip = clips_by_index.get(idx)
                        title = rendered_clip["title"] if rendered_clip else rendered_path.stem
                        caption = upload_tiktok.build_caption(title)
                        upload_tiktok.upload_to_tiktok(rendered_path, caption)

                status.update(label="Rendering abgeschlossen ✅", state="complete")
        except Exception as e:
            logger.exception("Rendering failed")
            st.error(f"Fehler beim Rendern: {e}")

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
