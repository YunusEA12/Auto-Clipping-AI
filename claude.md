# CLAUDE.md — Auto-Clipping Pipeline Rules

## Always Do First
- **Check Environment:** Always verify that the virtual environment (`venv`) is active and `.env` variables (API keys) are loaded before running scripts.
- **Modularity Check:** Ensure any new code fits into the modular pipeline: Ingestion -> Transcription -> Analysis -> Vision -> Processing -> Upload -> UI. Do not mix these domains in a single file.

## Local Hardware & Processing Limits
- **CPU & GPU Constraints:** The system runs on 32GB RAM with an Intel integrated GPU. DO NOT use CUDA-exclusive libraries. 
- **Local AI (Text/Audio):** Use CPU-optimized libraries like `faster-whisper` (CTranslate2) or Intel OpenVINO for local speech-to-text.
- **Local AI (Vision):** Use `mediapipe` (specifically the modern Tasks API) for lightweight, CPU-based face detection and dynamic cropping.
- **Cloud AI:** Heavy natural language processing and scene selection must be offloaded to external LLM APIs (OpenAI/Claude) with strict Pydantic Structured Outputs.

## Video Output Defaults
- **Format:** 9:16 vertical video (1080x1920 resolution).
- **Layout:** Split-screen. Facecam is dynamically detected via MediaPipe, padded, and placed at the top half; gameplay/main content is cropped to the bottom half.
- **Subtitles:** `.ass` (Advanced SubStation Alpha) format for dynamic, word-by-word highlighting. Hardcoded into the final video via FFmpeg.
- **Framerate:** Match the source video framerate (usually 30 or 60 fps).

## Testing, Workflow & UI
- **Test Chunks:** Never process a full VOD during testing. Always use FFmpeg to extract a 1- to 3-minute test sample before running the full pipeline.
- **File Management:** Save intermediate files (`.wav`, `.json`, `.ass`) in `/temp` and final videos in `/output`.
- **Log, don't print:** Use Python's `logging` module.
- **Web UI:** Use `streamlit` for the frontend. The `app.py` must only import and orchestrate the backend modules, not duplicate their logic.

## Git & Security (CRITICAL)
- **Auto-Commit Rule:** At the end of every successful task/feature, automatically stage changes (`git add .`), create a Conventional Commit, and run `git push`.
- **Secret Protection:** NEVER commit `.env`, `client_secret.json`, or `token.json`. Always ensure they are in `.gitignore`. 
- **Large Files:** NEVER commit `temp/` files, `output/` files, or any raw video/audio (`*.mp4`, `*.wav`).

## Hard Rules
- Do not introduce heavy C++ compilation dependencies unless absolutely necessary.
- Do not overwrite the original source video files.
- Do not proceed to the next pipeline step if the previous step's output fails validation.
- Do not hallucinate timestamps. The LLM analysis must only select timestamps that exist exactly in the Whisper transcription data.